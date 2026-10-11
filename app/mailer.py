"""Outgoing email (invitations, password resets, account notices).

Settings come from app.config first, then environment variables, so tests
can configure mail without touching the process environment:

    SMTP_HOST        e.g. smtp.resend.com
    SMTP_PORT        465 (implicit TLS, default) or 587 (STARTTLS)
    SMTP_USERNAME    e.g. resend
    SMTP_PASSWORD    the provider's API key / SMTP password
    SMTP_FROM        e.g. Setify <noreply@setify.net>
    PUBLIC_BASE_URL  e.g. https://setify.net (used to build links)

Optional:
    SMTP_SECURITY    "ssl" or "starttls" (default: ssl on port 465, else starttls)
    SMTP_REPLY_TO    e.g. support@setify.net
    APP_NAME         product name used in emails (default "SetMaker")

See Documents/email-setup.md for the DNS / provider setup.
"""

import os
import smtplib
import ssl
import threading
from email.message import EmailMessage
from email.utils import formatdate, make_msgid, parseaddr

from flask import current_app

REQUIRED_SETTINGS = (
    "SMTP_HOST",
    "SMTP_USERNAME",
    "SMTP_PASSWORD",
    "SMTP_FROM",
    "PUBLIC_BASE_URL",
)

# Everything a failed SMTP delivery can raise. Callers catch this tuple.
MAIL_ERRORS = (smtplib.SMTPException, OSError, RuntimeError, ValueError)


class EmailNotConfigured(RuntimeError):
    pass


def setting(name, default=None):
    value = current_app.config.get(name)
    if value is None or value == "":
        value = os.environ.get(name)
    if value is None or value == "":
        return default
    return value


def mail_configured():
    return all(setting(name) for name in REQUIRED_SETTINGS)


def app_name():
    return setting("APP_NAME", "SetMaker")


def public_url(path):
    """Absolute link for an email. `path` comes from url_for()."""
    base = (setting("PUBLIC_BASE_URL") or "").rstrip("/")
    if not base:
        raise EmailNotConfigured("PUBLIC_BASE_URL is not set.")
    return f"{base}{path}"


def build_message(to, subject, body):
    sender = setting("SMTP_FROM")
    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = sender
    message["To"] = to
    message["Date"] = formatdate(usegmt=True)
    domain = parseaddr(sender or "")[1].rpartition("@")[2] or None
    message["Message-ID"] = make_msgid(domain=domain)
    reply_to = setting("SMTP_REPLY_TO")
    if reply_to:
        message["Reply-To"] = reply_to
    # Ask mail servers not to send out-of-office replies to noreply@.
    message["Auto-Submitted"] = "auto-generated"
    message.set_content(body)
    return message


def send_email(to, subject, body, *, background=False):
    """Send a plain-text email. Raises EmailNotConfigured or one of
    MAIL_ERRORS on failure (unless background=True, where failures are
    logged instead).

    background=True returns straight away and delivers on a thread, so a
    response's timing doesn't reveal whether an email was sent (used by
    forgot-password). Tests always deliver synchronously."""
    if not mail_configured():
        raise EmailNotConfigured("SMTP configuration is incomplete.")
    message = build_message(to, subject, body)
    if background and not current_app.testing:
        app = current_app._get_current_object()
        threading.Thread(
            target=_deliver_logged, args=(app, message), daemon=True
        ).start()
    else:
        _deliver(message)
    return message


def send_best_effort(to, subject, body, *, background=False):
    """For notices that must not break the request (e.g. "your password was
    changed"). Returns True if the email was handed to the mail server (or
    queued for it)."""
    try:
        send_email(to, subject, body, background=background)
    except EmailNotConfigured:
        return False
    except MAIL_ERRORS:
        current_app.logger.exception("Email delivery failed: %s", subject)
        return False
    return True


def _deliver_logged(app, message):
    with app.app_context():
        try:
            _deliver(message)
        except MAIL_ERRORS:
            app.logger.exception("Email delivery failed: %s", message["Subject"])


def _deliver(message):
    host = setting("SMTP_HOST")
    port = int(setting("SMTP_PORT", "465"))
    security = (setting("SMTP_SECURITY") or ("ssl" if port == 465 else "starttls")).lower()
    if security not in {"ssl", "starttls"}:
        raise ValueError("SMTP_SECURITY must be 'ssl' or 'starttls'.")
    context = ssl.create_default_context()

    if security == "ssl":
        connection = smtplib.SMTP_SSL(host, port, context=context, timeout=10)
    else:
        connection = smtplib.SMTP(host, port, timeout=10)

    with connection as smtp:
        if security == "starttls":
            smtp.starttls(context=context)
        smtp.login(setting("SMTP_USERNAME"), setting("SMTP_PASSWORD"))
        smtp.send_message(message)
