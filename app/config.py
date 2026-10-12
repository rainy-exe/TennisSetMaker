import os
from datetime import timedelta
from pathlib import Path


def env_flag(name, default=False):
    value = os.environ.get(name)
    if value is None or value == "":
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


class Config:
    SECRET_KEY = os.environ.get("SECRET_KEY", "development-only-change-me")
    SQLALCHEMY_DATABASE_URI = os.environ.get(
        "DATABASE_URL", "sqlite:///tennis_set_maker.db"
    )
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    # Neon suspends idle databases and drops idle connections. Test each
    # pooled connection before use (replacing dead ones) and retire them
    # after 5 minutes, so a stale connection never reaches a request.
    SQLALCHEMY_ENGINE_OPTIONS = {
        "pool_pre_ping": True,
        "pool_recycle": 300,
    }
    ALGORITHM_PATH = os.environ.get(
        "TENNIS_ALGORITHM_PATH",
        str(
            Path(__file__).resolve().parent.parent
            / "Backend"
            / "Set_Algorithm"
            / "Prototype4"
        ),
    )
    ALGORITHM_RANDOM_SEED = None
    HISTORY_WINDOW_DAYS = int(os.environ.get("HISTORY_WINDOW_DAYS", "120"))
    RECAPTCHA_SECRET_KEY = os.environ.get("RECAPTCHA_SECRET_KEY")
    CURRENT_ACTOR_PROVIDER = None

    # Login cookies. "Remember me" keeps the login for this long; otherwise
    # it ends when the browser closes. Set SESSION_COOKIE_SECURE=true in
    # production (HTTPS only).
    PERMANENT_SESSION_LIFETIME = timedelta(
        days=int(os.environ.get("REMEMBER_ME_DAYS", "30"))
    )
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    SESSION_COOKIE_SECURE = env_flag("SESSION_COOKIE_SECURE")

    # Behind Cloudflare: CLIENT_IP_HEADER=CF-Connecting-IP so rate limits see
    # the visitor's IP rather than the proxy's.
    CLIENT_IP_HEADER = os.environ.get("CLIENT_IP_HEADER") or None

    # Email (SMTP_HOST, SMTP_PORT, SMTP_USERNAME, SMTP_PASSWORD, SMTP_FROM,
    # SMTP_REPLY_TO, SMTP_SECURITY, PUBLIC_BASE_URL, APP_NAME) is read from
    # the environment when sent -- see app/mailer.py and
    # Documents/email-setup.md.
