"""The logged-in user's own account: name, password, login email and email
verification. Used by Settings > My Account. Any role may use these."""

from flask import Blueprint, current_app, jsonify
from werkzeug.security import check_password_hash, generate_password_hash

from app import mailer
from app.account_security import (
    account_email_on_cooldown,
    end_other_sessions,
    limiter,
    mark_account_email_sent,
    new_password_from,
    notify_password_changed,
    send_email_change_confirmation,
    send_verification,
    too_many_requests,
)
from app.auth import auth_body, user_payload, validate_email
from app.errors import ApiError
from app.extensions import db
from app.models import User
from app.permissions import current_actor

account = Blueprint("account", __name__)

PASSWORD_CHECK_LIMIT = 10
PASSWORD_CHECK_WINDOW = 15 * 60


def current_user():
    user = db.session.get(User, current_actor().actor_id)
    if user is None:
        raise ApiError("Account not found.", 404, "not_found")
    return user


def account_payload(user):
    return {
        "user": user_payload(user),
        "email_configured": mailer.mail_configured(),
    }


def require_current_password(user, data):
    """Re-check the password before sensitive changes, so someone using an
    unattended logged-in browser can't take over the account."""
    key = ("current-password", user.id)
    if limiter().blocked(key, PASSWORD_CHECK_LIMIT, PASSWORD_CHECK_WINDOW):
        raise too_many_requests()
    password = data.get("current_password")
    if (
        not isinstance(password, str)
        or len(password) > 128
        or not check_password_hash(user.password_hash, password)
    ):
        limiter().record(key)
        raise ApiError("Current password is incorrect.", 400, "wrong_password")
    limiter().reset(key)


def email_unavailable():
    return ApiError(
        "Email isn't set up on this server yet, so this can't be done right now.",
        503,
        "email_unavailable",
    )


def cooldown_error():
    return too_many_requests("An email was sent less than a minute ago. Please wait and try again.")


@account.get("/account")
def get_account():
    return jsonify(account_payload(current_user()))


@account.patch("/account")
def update_account():
    user = current_user()
    data = auth_body()
    if set(data) - {"full_name"} or "full_name" not in data:
        raise ApiError("Only full_name can be changed through this endpoint.")
    full_name = data["full_name"]
    if not isinstance(full_name, str) or not 1 <= len(full_name.strip()) <= 120:
        raise ApiError("Full name must contain 1 to 120 characters.")
    user.full_name = full_name.strip()
    db.session.commit()
    return jsonify(account_payload(user))


@account.post("/account/password")
def change_password():
    user = current_user()
    data = auth_body()
    require_current_password(user, data)
    new_password = new_password_from(data, "new_password")

    user.password_hash = generate_password_hash(new_password)
    # Other devices are logged out; this one stays logged in.
    end_other_sessions(user, keep_current=True)
    db.session.commit()

    notify_password_changed(user)
    return jsonify(
        {"message": "Password changed. Other devices have been logged out."}
    )


@account.post("/account/email")
def request_email_change():
    user = current_user()
    data = auth_body()
    require_current_password(user, data)
    new_email = validate_email(data.get("new_email"))

    if new_email == user.email:
        raise ApiError("That is already your login email.", 400, "same_email")
    if User.query.filter_by(email=new_email).first() is not None:
        raise ApiError(
            "An account with this email already exists.", 409, "email_exists"
        )
    if not mailer.mail_configured():
        raise email_unavailable()
    if account_email_on_cooldown(user):
        raise cooldown_error()

    try:
        send_email_change_confirmation(user, new_email)
    except mailer.MAIL_ERRORS as error:
        current_app.logger.exception("Email change confirmation delivery failed")
        raise ApiError(
            "The confirmation email could not be sent. Please try again.",
            502,
            "email_delivery_failed",
        ) from error

    mark_account_email_sent(user)
    db.session.commit()
    return jsonify(
        {
            "message": f"To approve the change, open the link we've sent to "
            f"your current email, {user.email}. Then confirm {new_email} "
            "from the email we send there."
        }
    ), 202


@account.post("/account/verification-email")
def resend_verification():
    user = current_user()

    if user.email_verified_at is not None:
        return jsonify({"message": "Your email is already confirmed."})
    if not mailer.mail_configured():
        raise email_unavailable()
    if account_email_on_cooldown(user):
        raise cooldown_error()

    try:
        send_verification(user)
    except mailer.MAIL_ERRORS as error:
        current_app.logger.exception("Verification email delivery failed")
        raise ApiError(
            "The confirmation email could not be sent. Please try again.",
            502,
            "email_delivery_failed",
        ) from error

    mark_account_email_sent(user)
    db.session.commit()
    return jsonify({"message": f"We've sent a confirmation link to {user.email}."}), 202
