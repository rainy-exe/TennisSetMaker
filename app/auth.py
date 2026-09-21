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
from werkzeug.security import check_password_hash, generate_password_hash

from app import csrf
from app.extensions import db
from app.forms import RegisterForm
from app.models import Club, Invitation, User, utc_now
from app.permissions import CurrentActor, Role


auth_bp = Blueprint("auth", __name__)


# ---------------------------------------------------------
# Helpers
# ---------------------------------------------------------

def user_payload(user):
    return {
        "id": user.id,
        "email": user.email,
        "role": user.role,
        "club_id": user.club_id,
    }


def normalize_email(email):
    if not isinstance(email, str):
        return ""

    return email.strip().lower()


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

    if user is None or not user.active:
        session.pop("user_id", None)
        return None

    return CurrentActor(
        actor_id=user.id,
        club_id=user.club_id,
        role=Role(user.role),
    )


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
@csrf.exempt
def register():
    """
    Create a brand-new club and its Owner account.

    Admin and Staff accounts should NOT use this endpoint.
    They should be created through invitations.
    """

    data = request.get_json(silent=True) or {}

    email = normalize_email(data.get("email"))
    password = data.get("password", "")
    club_name = data.get("club_name", "").strip()
    recaptcha_token = data.get("recaptcha_token")

    if not email:
        return jsonify({
            "message": "Email is required."
        }), 400

    if not password:
        return jsonify({
            "message": "Password is required."
        }), 400

    if not club_name:
        return jsonify({
            "message": "Club name is required."
        }), 400

    if not verify_recaptcha(recaptcha_token):
        return jsonify({
            "message": "reCAPTCHA verification failed."
        }), 400

    existing_user = User.query.filter_by(email=email).first()

    if existing_user:
        return jsonify({
            "message": "Email already registered."
        }), 409

    club = Club(
        name=club_name,
    )

    owner = User(
        club=club,
        email=email,
        password_hash=generate_password_hash(password),
        role=Role.OWNER.value,
        active=True,
    )

    db.session.add(club)
    db.session.add(owner)

    try:
        db.session.commit()

    except Exception:
        db.session.rollback()
        raise

    # Log the new Owner in immediately.
    session.clear()
    session["user_id"] = owner.id

    return jsonify({
        "message": "Club and Owner account created successfully.",
        "user": user_payload(owner),
    }), 201


# ---------------------------------------------------------
# Login
# ---------------------------------------------------------

@auth_bp.route("/login", methods=["POST"])
@csrf.exempt
def login():
    data = request.get_json(silent=True) or {}

    email = normalize_email(data.get("email"))
    password = data.get("password", "")

    if not email or not password:
        return jsonify({
            "message": "Email and password are required."
        }), 400

    user = User.query.filter_by(email=email).first()

    if (
        user is None
        or not user.active
        or not check_password_hash(user.password_hash, password)
    ):
        return jsonify({
            "message": "Invalid credentials."
        }), 401

    session.clear()
    session["user_id"] = user.id

    return jsonify({
        "message": "Login successful.",
        "user": user_payload(user),
    })


# ---------------------------------------------------------
# Logout
# ---------------------------------------------------------

@auth_bp.route("/logout", methods=["GET"])
def logout():
    session.clear()

    flash(
        "Logged out successfully.",
        "info",
    )

    return redirect(
        url_for("auth.login_page")
    )


# ---------------------------------------------------------
# Check login/session
# ---------------------------------------------------------

@auth_bp.route("/check-session", methods=["GET"])
def check_session():
    user_id = session.get("user_id")

    if user_id is None:
        return jsonify({
            "logged_in": False,
        })

    user = db.session.get(User, user_id)

    if user is None or not user.active:
        session.pop("user_id", None)

        return jsonify({
            "logged_in": False,
        })

    return jsonify({
        "logged_in": True,
        "user": user_payload(user),
    })


# ---------------------------------------------------------
# Accept Admin / Staff invitation
# ---------------------------------------------------------

@auth_bp.route("/accept-invitation", methods=["POST"])
@csrf.exempt
def accept_invitation():
    """
    Creates an Admin or Staff account from a valid invitation.

    The invitation already determines:
    - club_id
    - email
    - role
    """

    data = request.get_json(silent=True) or {}

    token = data.get("token", "")
    password = data.get("password", "")

    if not token:
        return jsonify({
            "message": "Invitation token is required."
        }), 400

    if not password:
        return jsonify({
            "message": "Password is required."
        }), 400

    invitation = Invitation.query.filter_by(
        token=token
    ).first()

    if invitation is None:
        return jsonify({
            "message": "Invalid invitation."
        }), 404

    if invitation.accepted_at is not None:
        return jsonify({
            "message": "This invitation has already been accepted."
        }), 409

    if invitation.expires_at < utc_now():
        return jsonify({
            "message": "This invitation has expired."
        }), 410

    if invitation.role not in {
        Role.ADMIN.value,
        Role.STAFF.value,
    }:
        return jsonify({
            "message": "Invalid invitation role."
        }), 400

    email = normalize_email(invitation.email)

    existing_user = User.query.filter_by(
        email=email
    ).first()

    if existing_user:
        return jsonify({
            "message": "An account with this email already exists."
        }), 409

    user = User(
        club_id=invitation.club_id,
        email=email,
        password_hash=generate_password_hash(password),
        role=invitation.role,
        active=True,
    )

    invitation.accepted_at = utc_now()

    db.session.add(user)

    try:
        db.session.commit()

    except Exception:
        db.session.rollback()
        raise

    # Automatically log in the newly-created account.
    session.clear()
    session["user_id"] = user.id

    return jsonify({
        "message": "Invitation accepted successfully.",
        "user": user_payload(user),
    }), 201


# ---------------------------------------------------------
# Login page
# ---------------------------------------------------------

@auth_bp.route("/login-page", methods=["GET"])
def login_page():
    return render_template(
        "login.html"
    )


# ---------------------------------------------------------
# Signup page
# ---------------------------------------------------------

@auth_bp.route("/signup-page", methods=["GET"])
def signup_page():
    form = RegisterForm()

    return render_template(
        "signup.html",
        form=form,
    )


# ---------------------------------------------------------
# reCAPTCHA verification endpoint
# ---------------------------------------------------------

@auth_bp.route("/api/signup", methods=["POST"])
@csrf.exempt
def api_signup():
    data = request.get_json(silent=True) or {}

    recaptcha_response = data.get(
        "recaptcha_token"
    )

    if not verify_recaptcha(recaptcha_response):
        return jsonify({
            "message": "reCAPTCHA failed. Try again."
        }), 400

    return jsonify({
        "message": "reCAPTCHA verification successful."
    })


# ---------------------------------------------------------
# Forgot password
# ---------------------------------------------------------

@auth_bp.route(
    "/forgot-password",
    methods=["GET", "POST"],
)
def forgot_password():
    if request.method == "POST":
        email = normalize_email(
            request.form.get("email")
        )

        user = User.query.filter_by(
            email=email
        ).first()

        if not user:
            flash(
                "No account with that email was found.",
                "danger",
            )

            return redirect(
                url_for("auth.forgot_password")
            )

        # Password reset email functionality can
        # be implemented later.
        flash(
            "Password reset instructions have been sent to your email.",
            "info",
        )

        return redirect(
            url_for("auth.login_page")
        )

    return render_template(
        "forgot_password.html"
    )


# ---------------------------------------------------------
# Home
# ---------------------------------------------------------

@auth_bp.route("/", methods=["GET"])
def home():
    form = RegisterForm()

    return render_template(
        "signup.html",
        form=form,
    )