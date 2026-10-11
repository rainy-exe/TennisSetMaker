import re

import requests
from flask import (
    Blueprint,
    current_app,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from flask_wtf.csrf import generate_csrf
from jinja2 import TemplateNotFound
from sqlalchemy import update
from sqlalchemy.exc import IntegrityError
from werkzeug.security import check_password_hash, generate_password_hash

from app import mailer
from app.account_security import (
    account_email_on_cooldown,
    client_ip,
    email_change_from_token,
    limiter,
    login_user,
    mark_account_email_sent,
    new_password_from,
    notify_email_changed,
    notify_password_changed,
    send_password_reset,
    send_verification,
    session_is_current,
    too_many_requests,
    user_from_reset_token,
    user_from_verify_token,
)
from app.errors import ApiError
from app.extensions import db
from app.forms import RegisterForm
from app.models import Club, Invitation, User, utc_now
from app.permissions import CurrentActor, Role, can_invite_role

auth_bp = Blueprint("auth", __name__)

LOGIN_FAILURE_LIMIT = 10
LOGIN_FAILURE_WINDOW = 15 * 60
FORGOT_PER_IP_LIMIT = 30
FORGOT_PER_EMAIL_LIMIT = 5
FORGOT_WINDOW = 60 * 60


# ---------------------------------------------------------
# Helpers
# ---------------------------------------------------------


def user_payload(user):
    return {
        "id": user.id,
        "full_name": user.full_name,
        "email": user.email,
        "role": user.role,
        "club_id": user.club_id,
        "email_verified": user.email_verified_at is not None,
    }


def normalize_email(email):
    if not isinstance(email, str):
        return ""

    return email.strip().lower()


def auth_body():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        raise ApiError("A JSON object is required.")
    return data


def validate_email(value):
    email = normalize_email(value)
    if len(email) > 255 or not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", email):
        raise ApiError("A valid email address is required.")
    return email


def password_from(data):
    password = data.get("password")
    if not isinstance(password, str) or not password or len(password) > 128:
        raise ApiError("Password must be a non-empty string of at most 128 characters.")
    return password


def render_account_page(template, **context):
    # Keep Kate's page endpoints ready for team-owned templates, without
    # building a second frontend or returning an unhandled missing-template 500.
    try:
        return render_template(template, **context)
    except TemplateNotFound as error:
        raise ApiError(
            "This account page has not been integrated yet. Use the JSON API.",
            503,
            "frontend_unavailable",
        ) from error


def session_actor_provider():
    """
    Converts the currently logged-in User into a CurrentActor.

    permissions.py uses this to determine:
    - who is logged in
    - which club they belong to
    - whether they are owner/admin/staff
    """

    user_id = session.get("user_id")

    if user_id is None:
        return None

    user = db.session.get(User, user_id)

    if (
        user is None
        or not user.active
        or not user.club.active
        or not session_is_current(user)
    ):
        forget_login()
        return None

    return CurrentActor(
        actor_id=user.id,
        club_id=user.club_id,
        role=Role(user.role),
    )


def forget_login():
    session.pop("user_id", None)
    session.pop("sv", None)


def verify_recaptcha(token):
    """
    Verify a reCAPTCHA token using the secret stored in Flask config.

    The secret should NOT be hard-coded in this file.
    """

    secret_key = current_app.config.get("RECAPTCHA_SECRET_KEY")

    # If reCAPTCHA has not been configured, allow development/testing
    # without it. In production, RECAPTCHA_SECRET_KEY should be set.
    if not secret_key:
        return True

    if not token:
        return False

    verify_url = "https://www.google.com/recaptcha/api/siteverify"

    payload = {
        "secret": secret_key,
        "response": token,
    }

    try:
        response = requests.post(
            verify_url,
            data=payload,
            timeout=5,
        )

        result = response.json()

    except (requests.RequestException, ValueError):
        return False

    return bool(result.get("success"))


# ---------------------------------------------------------
# Owner / Club registration
# ---------------------------------------------------------


@auth_bp.route("/register", methods=["POST"])
def register():
    """
    Create a brand-new club and its Owner account.

    Admin and Staff accounts should NOT use this endpoint.
    They should be created through invitations.
    """

    data = auth_body()
    
    full_name = data.get("full_name")

    if not isinstance(full_name, str) or not 1 <= len(full_name.strip()) <= 120:
        raise ApiError("Full name must contain 1 to 120 characters.")

    full_name = full_name.strip()

    email = validate_email(data.get("email"))
    password = new_password_from(data)
    club_name = data.get("club_name")
    if (
        not isinstance(club_name, str)
        or not club_name.strip()
        or len(club_name.strip()) > 120
    ):
        raise ApiError("Club name must contain 1 to 120 characters.")
    club_name = club_name.strip()
    recaptcha_token = data.get("recaptcha_token")

    if not email:
        return jsonify({"message": "Email is required."}), 400

    if not password:
        return jsonify({"message": "Password is required."}), 400

    if not club_name:
        return jsonify({"message": "Club name is required."}), 400

    if not verify_recaptcha(recaptcha_token):
        return jsonify({"message": "reCAPTCHA verification failed."}), 400

    existing_user = User.query.filter_by(email=email).first()

    if existing_user:
        return jsonify({"message": "Email already registered."}), 409

    club = Club(
        name=club_name,
    )

    owner = User(
        club=club,
        full_name=full_name,
        email=email,
        password_hash=generate_password_hash(password),
        role=Role.OWNER.value,
        active=True,
    )

    db.session.add(club)
    db.session.add(owner)

    try:
        db.session.commit()

    except IntegrityError as error:
        db.session.rollback()
        raise ApiError("Email already registered.", 409, "email_exists") from error
    except Exception:
        db.session.rollback()
        raise

    session.clear()

    # Best effort: the account works without a confirmed email, and the
    # owner can resend the link from Settings > My Account.
    verification_sent = False
    if mailer.mail_configured():
        try:
            send_verification(owner)
            mark_account_email_sent(owner)
            db.session.commit()
            verification_sent = True
        except mailer.MAIL_ERRORS:
            db.session.rollback()
            current_app.logger.exception("Verification email delivery failed")

    return jsonify(
        {
            "message": "Club and Owner account created successfully.",
            "user": user_payload(owner),
            "verification_email_sent": verification_sent,
            "csrf_token": generate_csrf(),
        }
    ), 201


# ---------------------------------------------------------
# Login
# ---------------------------------------------------------


@auth_bp.route("/login", methods=["POST"])
def login():
    data = auth_body()

    email = normalize_email(data.get("email"))
    password = password_from(data)

    if not email or not password:
        return jsonify({"message": "Email and password are required."}), 400

    # Count failed attempts per visitor + email address, so a password can't
    # be guessed quickly but other people's logins are never locked out.
    attempts_key = ("login", client_ip(), email)
    if limiter().blocked(attempts_key, LOGIN_FAILURE_LIMIT, LOGIN_FAILURE_WINDOW):
        raise too_many_requests(
            "Too many failed log-in attempts. Wait 15 minutes or reset your password."
        )

    user = User.query.filter_by(email=email).first()

    if (
        user is None
        or not user.active
        or not user.club.active
        or not check_password_hash(user.password_hash, password)
    ):
        limiter().record(attempts_key)
        return jsonify({"message": "Invalid credentials."}), 401

    limiter().reset(attempts_key)
    login_user(user, remember=data.get("remember") is True)

    return jsonify(
        {
            "message": "Login successful.",
            "user": user_payload(user),
            "csrf_token": generate_csrf(),
        }
    )


# ---------------------------------------------------------
# Logout
# ---------------------------------------------------------


@auth_bp.route("/logout", methods=["POST"])
def logout():
    session.clear()

    flash(
        "Logged out successfully.",
        "info",
    )

    return redirect(url_for("auth.login_page"))


# ---------------------------------------------------------
# Check login/session
# ---------------------------------------------------------


@auth_bp.route("/check-session", methods=["GET"])
def check_session():
    user_id = session.get("user_id")

    if user_id is None:
        return jsonify(
            {
                "logged_in": False,
            }
        )

    user = db.session.get(User, user_id)

    if (
        user is None
        or not user.active
        or not user.club.active
        or not session_is_current(user)
    ):
        forget_login()

        return jsonify(
            {
                "logged_in": False,
            }
        )

    return jsonify(
        {
            "logged_in": True,
            "user": user_payload(user),
        }
    )


# ---------------------------------------------------------
# Accept Admin / Staff invitation
# ---------------------------------------------------------


@auth_bp.route("/accept-invitation", methods=["POST"])
def accept_invitation():
    """
    Creates an Admin or Staff account from a valid invitation.

    The invitation already determines:
    - club_id
    - email
    - role
    """

    data = auth_body()

    token = data.get("token", "")
    password = new_password_from(data)
    full_name = data.get("full_name")

    if not isinstance(full_name, str) or not 1 <= len(full_name.strip()) <= 120:
        raise ApiError("Full name must contain 1 to 120 characters.")

    full_name = full_name.strip()
    
    if not isinstance(token, str) or not token or len(token) > 255:
        return jsonify({"message": "Invitation token is required."}), 400

    if not password:
        return jsonify({"message": "Password is required."}), 400

    invitation = Invitation.query.filter_by(token=token).first()

    if invitation is None:
        return jsonify({"message": "Invalid invitation."}), 404

    if invitation.accepted_at is not None:
        return jsonify({"message": "This invitation has already been accepted."}), 409

    if invitation.expires_at < utc_now():
        return jsonify({"message": "This invitation has expired."}), 410

    if (
        not invitation.club.active
        or not invitation.inviter.active
        or not can_invite_role(
            invitation.role,
            CurrentActor(
                invitation.inviter.id, invitation.club_id, Role(invitation.inviter.role)
            ),
        )
    ):
        raise ApiError(
            "This invitation is no longer available.", 410, "invitation_unavailable"
        )

    if invitation.role not in {
        Role.ADMIN.value,
        Role.STAFF.value,
    }:
        return jsonify({"message": "Invalid invitation role."}), 400

    email = normalize_email(invitation.email)

    existing_user = User.query.filter_by(email=email).first()

    if existing_user:
        return jsonify({"message": "An account with this email already exists."}), 409

    user = User(
        club_id=invitation.club_id,
        full_name=full_name,
        email=email,
        password_hash=generate_password_hash(password),
        role=invitation.role,
        active=True,
        # The invitation link was emailed to this address.
        email_verified_at=utc_now(),
    )

    # Atomically claim a one-use invitation. A concurrent acceptance must not
    # create a second account, even when workers read the same token together.
    claimed_at = utc_now()
    claimed = db.session.execute(
        update(Invitation)
        .where(
            Invitation.id == invitation.id,
            Invitation.accepted_at.is_(None),
            Invitation.expires_at >= claimed_at,
        )
        .values(accepted_at=claimed_at)
    )
    if claimed.rowcount != 1:
        raise ApiError(
            "This invitation is no longer available.", 409, "invitation_unavailable"
        )

    db.session.add(user)

    try:
        db.session.commit()

    except IntegrityError as error:
        db.session.rollback()
        raise ApiError(
            "An account with this email already exists.", 409, "email_exists"
        ) from error
    except Exception:
        db.session.rollback()
        raise

    # Automatically log in the newly-created account.
    login_user(user)

    return jsonify(
        {
            "message": "Invitation accepted successfully.",
            "user": user_payload(user),
            "csrf_token": generate_csrf(),
        }
    ), 201

@auth_bp.get("/join")
def join_page():
    token = request.args.get("token", "")

    return render_account_page(
        "accept-invite.html",
        token=token,
    )

# ---------------------------------------------------------
# Login page
# ---------------------------------------------------------


@auth_bp.route("/login-page", methods=["GET"])
def login_page():
    return render_account_page("login.html")


# ---------------------------------------------------------
# Signup page
# ---------------------------------------------------------


@auth_bp.route("/signup-page", methods=["GET"])
def signup_page():
    form = RegisterForm()

    return render_account_page(
        "signup.html",
        form=form,
    )


# ---------------------------------------------------------
# reCAPTCHA verification endpoint
# ---------------------------------------------------------


@auth_bp.route("/api/signup", methods=["POST"])
def api_signup():
    data = auth_body()

    recaptcha_response = data.get("recaptcha_token")

    if not verify_recaptcha(recaptcha_response):
        return jsonify({"message": "reCAPTCHA failed. Try again."}), 400

    return jsonify({"message": "reCAPTCHA verification successful."})


# ---------------------------------------------------------
# Forgot / reset password
# ---------------------------------------------------------

FORGOT_PASSWORD_REPLY = (
    "If an account exists for that email, we've sent a link to reset the "
    "password. It expires in 1 hour. Check your spam folder if it doesn't arrive."
)


@auth_bp.get("/forgot-password")
def forgot_password():
    return render_account_page(
        "forgot-password.html", mail_configured=mailer.mail_configured()
    )


@auth_bp.post("/forgot-password")
def request_password_reset():
    data = auth_body()

    if not mailer.mail_configured():
        raise ApiError(
            "Password reset email isn't set up on this server yet. "
            "No reset email has been sent.",
            503,
            "password_reset_unavailable",
        )

    email = validate_email(data.get("email"))

    if not limiter().hit(("forgot-ip", client_ip()), FORGOT_PER_IP_LIMIT, FORGOT_WINDOW):
        raise too_many_requests()

    # From here on the reply is the same whether or not the account exists,
    # so this form can't be used to find out who has an account.
    user = User.query.filter_by(email=email).first()
    if (
        user is not None
        and user.active
        and user.club.active
        and not account_email_on_cooldown(user)
        and limiter().hit(("forgot-email", email), FORGOT_PER_EMAIL_LIMIT, FORGOT_WINDOW)
    ):
        try:
            send_password_reset(user, background=True)
            mark_account_email_sent(user)
            db.session.commit()
        except mailer.MAIL_ERRORS:
            db.session.rollback()
            current_app.logger.exception("Password reset email delivery failed")

    return jsonify({"message": FORGOT_PASSWORD_REPLY})


@auth_bp.get("/reset-password")
def reset_password():
    token = request.args.get("token", "")
    user = user_from_reset_token(token)
    return render_account_page(
        "reset-password.html",
        token=token if user else "",
        token_valid=user is not None,
        account_email=user.email if user else None,
    )


@auth_bp.post("/reset-password")
def complete_password_reset():
    data = auth_body()
    user = user_from_reset_token(data.get("token"))

    if user is None:
        raise ApiError(
            "This reset link is invalid, has expired or was already used. "
            "Request a new one.",
            400,
            "invalid_token",
        )

    password = new_password_from(data)

    user.password_hash = generate_password_hash(password)
    # Log out every device, including any an intruder was using.
    user.session_version = (user.session_version or 0) + 1
    # Opening the link proves they own the inbox.
    if user.email_verified_at is None:
        user.email_verified_at = utc_now()
    db.session.commit()

    session.clear()
    limiter().reset(("login", client_ip(), user.email))
    notify_password_changed(user)

    return jsonify({"message": "Your password has been reset. You can now log in."})


# ---------------------------------------------------------
# Email links: verification and email change
# ---------------------------------------------------------


def email_status_page(title, message, ok, status=200):
    return (
        render_account_page(
            "email-status.html", title=title, message=message, ok=ok
        ),
        status,
    )


@auth_bp.get("/verify-email")
def verify_email():
    user = user_from_verify_token(request.args.get("token", ""))

    if user is None:
        return email_status_page(
            "Link not valid",
            "This confirmation link is invalid or has expired. Log in and use "
            "Settings > My Account to send a new one.",
            ok=False,
            status=400,
        )

    if user.email_verified_at is None:
        user.email_verified_at = utc_now()
        db.session.commit()

    return email_status_page(
        "Email confirmed", f"Thanks, {user.email} is confirmed.", ok=True
    )


@auth_bp.get("/confirm-email-change")
def confirm_email_change():
    user, new_email = email_change_from_token(request.args.get("token", ""))

    if user is None:
        return email_status_page(
            "Link not valid",
            "This link is invalid, has expired or was already used. "
            "Your login email has not changed.",
            ok=False,
            status=400,
        )

    if User.query.filter_by(email=new_email).first() is not None:
        return email_status_page(
            "Email already in use",
            f"{new_email} is already used by another account, so your login "
            "email has not changed.",
            ok=False,
            status=409,
        )

    old_email = user.email
    user.email = new_email
    user.email_verified_at = utc_now()

    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        return email_status_page(
            "Email already in use",
            f"{new_email} is already used by another account, so your login "
            "email has not changed.",
            ok=False,
            status=409,
        )

    notify_email_changed(user, old_email)

    return email_status_page(
        "Email changed",
        f"Your login email is now {new_email}. Use it next time you log in.",
        ok=True,
    )


@auth_bp.get("/csrf-token")
def csrf_token():
    response = jsonify({"csrf_token": generate_csrf()})
    response.headers["Cache-Control"] = "no-store"
    return response
