"""Account security helpers shared by auth.py and account.py:

- signed, expiring email links (password reset, email verification,
  email change) using itsdangerous and SECRET_KEY
- log-in / log-out-everywhere via a per-user session_version
- a small in-memory rate limiter
- the emails those flows send
"""

import hashlib
import hmac
import threading
import time
from datetime import timedelta
from urllib.parse import urlencode

from flask import current_app, request, session, url_for
from itsdangerous import BadSignature, URLSafeTimedSerializer

from app import mailer
from app.errors import ApiError
from app.extensions import db
from app.models import User, utc_now

PASSWORD_MIN_LENGTH = 8
PASSWORD_MAX_LENGTH = 128

RESET_TOKEN_MAX_AGE = 60 * 60  # 1 hour
VERIFY_TOKEN_MAX_AGE = 3 * 24 * 60 * 60  # 3 days
EMAIL_CHANGE_TOKEN_MAX_AGE = 24 * 60 * 60  # 1 day

# Minimum gap between account emails to the same user (resend buttons,
# repeated forgot-password requests).
ACCOUNT_EMAIL_COOLDOWN = timedelta(seconds=60)


# ---------------------------------------------------------
# Passwords
# ---------------------------------------------------------


def new_password_from(data, key="password"):
    """Validate a password being set (sign-up, reset, change). Log-in keeps
    accepting any existing password so older short ones still work."""
    password = data.get(key)
    if (
        not isinstance(password, str)
        or not PASSWORD_MIN_LENGTH <= len(password) <= PASSWORD_MAX_LENGTH
    ):
        raise ApiError(
            f"Password must be {PASSWORD_MIN_LENGTH} to {PASSWORD_MAX_LENGTH} characters.",
            400,
            "invalid_password",
        )
    return password


# ---------------------------------------------------------
# Sessions
# ---------------------------------------------------------


def login_user(user, remember=False):
    session.clear()
    session["user_id"] = user.id
    session["sv"] = user.session_version or 0
    # Permanent = cookie survives closing the browser (PERMANENT_SESSION_LIFETIME).
    session.permanent = bool(remember)


def session_is_current(user):
    """False once the user's password has been reset/changed elsewhere."""
    return session.get("sv", 0) == (user.session_version or 0)


def end_other_sessions(user, keep_current=False):
    """Invalidate every existing login for this user. With keep_current,
    the browser making this request stays logged in."""
    user.session_version = (user.session_version or 0) + 1
    if keep_current and session.get("user_id") == user.id:
        session["sv"] = user.session_version


# ---------------------------------------------------------
# Signed email links
# ---------------------------------------------------------


def _serializer(purpose):
    return URLSafeTimedSerializer(
        current_app.config["SECRET_KEY"], salt=f"setmaker-{purpose}"
    )


def password_fingerprint(user):
    """Changes whenever the password changes, so reset / email-change links
    stop working once used or once the password is changed another way."""
    key = str(current_app.config["SECRET_KEY"]).encode()
    return hmac.new(key, user.password_hash.encode(), hashlib.sha256).hexdigest()[:20]


def _load(purpose, token, max_age):
    if not isinstance(token, str) or not token or len(token) > 1000:
        return None
    try:
        data = _serializer(purpose).loads(token, max_age=max_age)
    except BadSignature:  # also covers SignatureExpired
        return None
    return data if isinstance(data, dict) else None


def _active_user(user_id):
    if not isinstance(user_id, str):
        return None
    user = db.session.get(User, user_id)
    if user is None or not user.active or not user.club.active:
        return None
    return user


def make_reset_token(user):
    return _serializer("password-reset").dumps(
        {"u": user.id, "f": password_fingerprint(user)}
    )


def user_from_reset_token(token):
    data = _load("password-reset", token, RESET_TOKEN_MAX_AGE)
    user = _active_user(data.get("u")) if data else None
    if user is None or not hmac.compare_digest(
        str(data.get("f")), password_fingerprint(user)
    ):
        return None
    return user


def make_verify_token(user):
    return _serializer("verify-email").dumps({"u": user.id, "e": user.email})


def user_from_verify_token(token):
    data = _load("verify-email", token, VERIFY_TOKEN_MAX_AGE)
    user = _active_user(data.get("u")) if data else None
    if user is None or data.get("e") != user.email:
        return None
    return user


def make_email_change_token(user, new_email):
    return _serializer("change-email").dumps(
        {
            "u": user.id,
            "e": new_email,
            "c": user.email,
            "f": password_fingerprint(user),
        }
    )


def email_change_from_token(token):
    """Returns (user, new_email) or (None, None)."""
    data = _load("change-email", token, EMAIL_CHANGE_TOKEN_MAX_AGE)
    user = _active_user(data.get("u")) if data else None
    if (
        user is None
        or data.get("c") != user.email
        or not isinstance(data.get("e"), str)
        or not hmac.compare_digest(str(data.get("f")), password_fingerprint(user))
    ):
        return None, None
    return user, data["e"]


def link(endpoint, token):
    return mailer.public_url(f"{url_for(endpoint)}?{urlencode({'token': token})}")


# ---------------------------------------------------------
# Rate limiting
# ---------------------------------------------------------


class RateLimiter:
    """Sliding-window counter kept in memory.

    Good enough for one server. Each worker process keeps its own counts,
    so with N workers the effective limit is up to N times higher; move this
    to Redis if that ever matters."""

    MAX_KEYS = 10_000

    def __init__(self):
        self._hits = {}
        self._lock = threading.Lock()

    def _recent(self, key, window, now):
        hits = [t for t in self._hits.get(key, ()) if now - t < window]
        if hits:
            self._hits[key] = hits
        else:
            self._hits.pop(key, None)
        return hits

    def blocked(self, key, limit, window):
        with self._lock:
            return len(self._recent(key, window, time.monotonic())) >= limit

    def record(self, key):
        with self._lock:
            if len(self._hits) >= self.MAX_KEYS:
                self._hits.clear()
            self._hits.setdefault(key, []).append(time.monotonic())

    def hit(self, key, limit, window):
        """Record an attempt; False if it is over the limit."""
        with self._lock:
            now = time.monotonic()
            hits = self._recent(key, window, now)
            if len(hits) >= limit:
                return False
            if len(self._hits) >= self.MAX_KEYS:
                self._hits.clear()
            self._hits.setdefault(key, []).append(now)
            return True

    def reset(self, key):
        with self._lock:
            self._hits.pop(key, None)


def limiter():
    return current_app.extensions.setdefault("setmaker_rate_limiter", RateLimiter())


def client_ip():
    """The visitor's IP. Behind Cloudflare set CLIENT_IP_HEADER=CF-Connecting-IP
    (only when the server is reachable through Cloudflare alone), otherwise
    every visitor looks like the proxy's address."""
    header = current_app.config.get("CLIENT_IP_HEADER")
    if header:
        value = request.headers.get(header, "").split(",")[0].strip()
        if value:
            return value
    return request.remote_addr or "unknown"


def too_many_requests(message="Too many attempts. Please wait a few minutes and try again."):
    return ApiError(message, 429, "rate_limited")


def account_email_on_cooldown(user):
    last = user.last_account_email_at
    return last is not None and utc_now() - last < ACCOUNT_EMAIL_COOLDOWN


def mark_account_email_sent(user):
    user.last_account_email_at = utc_now()


# ---------------------------------------------------------
# Emails
# ---------------------------------------------------------


def _greeting(user):
    return f"Hi {user.full_name}," if user.full_name else "Hi,"


def _signoff():
    return f"\n{mailer.app_name()}\n"


def send_password_reset(user, *, background=False):
    name = mailer.app_name()
    mailer.send_email(
        user.email,
        f"Reset your {name} password",
        f"""{_greeting(user)}

Someone (hopefully you) asked to reset the password for your {name} account.
Use the link below to choose a new password. It works once and expires in 1 hour.

{link("auth.reset_password", make_reset_token(user))}

If you didn't ask for this, you can ignore this email; your password won't change.
{_signoff()}""",
        background=background,
    )


def send_verification(user):
    name = mailer.app_name()
    mailer.send_email(
        user.email,
        f"Confirm your email for {name}",
        f"""{_greeting(user)}

Please confirm this is your email address for your {name} account:

{link("auth.verify_email", make_verify_token(user))}

The link expires in 3 days. If you didn't create an account, you can ignore this email.
{_signoff()}""",
    )


def send_email_change_confirmation(user, new_email):
    """Goes to the CURRENT address, so someone using an unattended
    logged-in browser can't move the account to an inbox they control.
    Once approved, the new address gets a normal confirmation email."""
    name = mailer.app_name()
    mailer.send_email(
        user.email,
        f"Approve your {name} login email change",
        f"""{_greeting(user)}

Someone (hopefully you) asked to change the login email for your {name} account
from {user.email} to {new_email}.

To approve the change, open the link below and press "Approve change".
It expires in 24 hours.

{link("auth.confirm_email_change", make_email_change_token(user, new_email))}

After you approve, we'll send a confirmation link to {new_email}.

If you didn't ask for this, don't open the link. Your email won't change, but
someone may know your password, so reset it straight away:

{mailer.public_url(url_for("auth.forgot_password"))}
{_contact_line()}{_signoff()}""",
    )


def notify_password_changed(user):
    name = mailer.app_name()
    return mailer.send_best_effort(
        user.email,
        f"Your {name} password was changed",
        f"""{_greeting(user)}

The password for your {name} account was just changed, and any other devices
that were logged in have been logged out.

If this was you, there's nothing else to do.
If it wasn't, reset your password straight away:

{mailer.public_url(url_for("auth.forgot_password"))}
{_contact_line()}{_signoff()}""",
        background=True,
    )


def _contact_line():
    support = mailer.setting("SMTP_REPLY_TO")
    return f"\nNeed help? Reply to this email or write to {support}.\n" if support else ""
