import os
import smtplib
import ssl

from email.message import EmailMessage
from urllib.parse import urlencode
import secrets
from datetime import timedelta

from flask import Blueprint, jsonify, url_for, current_app
from sqlalchemy import select

from app.api import body, commit_or_conflict
from app.auth import user_payload, validate_email
from app.errors import ApiError
from app.extensions import db
from app.models import Club, Invitation, User, utc_now
from app.permissions import (
    Role,
    can_invite_role,
    can_remove_user,
    current_actor,
    require_roles,
)

team = Blueprint("team", __name__)
MANAGERS = (Role.OWNER, Role.ADMIN)


def user_for_club(user_id):
    user = db.session.get(User, user_id)
    if user is None or user.club_id != current_actor().club_id:
        raise ApiError("Team member not found.", 404, "not_found")
    return user


def invitation_for_club(invitation_id):
    invitation = db.session.get(Invitation, invitation_id)
    if invitation is None or invitation.club_id != current_actor().club_id:
        raise ApiError("Invitation not found.", 404, "not_found")
    return invitation


def invitation_payload(invitation):
    return {
        "id": invitation.id,
        "email": invitation.email,
        "role": invitation.role,
        "expires_at": invitation.expires_at.isoformat(),
        "accepted_at": invitation.accepted_at.isoformat()
        if invitation.accepted_at
        else None,
    }


@team.get("/users")
@require_roles(*MANAGERS)
def list_users():
    users = db.session.scalars(
        select(User).where(User.club_id == current_actor().club_id).order_by(User.email)
    ).all()
    return jsonify(
        {"users": [{**user_payload(user), "active": user.active} for user in users]}
    )


@team.patch("/users/<user_id>")
@require_roles(*MANAGERS)
def update_user_role(user_id):
    user = user_for_club(user_id)
    data = body()
    if set(data) != {"role"}:
        raise ApiError("Only role can be changed through this endpoint.")
    try:
        role = Role(data["role"])
    except (ValueError, TypeError) as error:
        raise ApiError("role must be admin or staff.") from error
    if not can_remove_user(user.role) or not can_invite_role(role):
        raise ApiError("You cannot change this member's role.", 403, "forbidden")
    user.role = role.value
    db.session.commit()
    return jsonify({"user": {**user_payload(user), "active": user.active}})


@team.delete("/users/<user_id>")
@require_roles(*MANAGERS)
def deactivate_user(user_id):
    user = user_for_club(user_id)
    if not can_remove_user(user.role):
        raise ApiError("You cannot remove this member.", 403, "forbidden")
    # Preserve created_by/overridden_by foreign keys and historical rounds.
    user.active = False
    for invitation in user.invitations_sent:
        if invitation.accepted_at is None:
            invitation.expires_at = utc_now() - timedelta(seconds=1)
    db.session.commit()
    return "", 204


@team.get("/invitations")
@require_roles(*MANAGERS)
def list_invitations():
    invitations = db.session.scalars(
        select(Invitation)
        .where(Invitation.club_id == current_actor().club_id)
        .order_by(Invitation.created_at.desc())
    ).all()
    # Bearer tokens are returned once at creation, not in roster/list responses.
    return jsonify({"invitations": [invitation_payload(item) for item in invitations]})

def send_invitation_email(invitation):
    smtp_host = os.environ.get("SMTP_HOST")
    smtp_port = int(os.environ.get("SMTP_PORT", "465"))
    smtp_username = os.environ.get("SMTP_USERNAME")
    smtp_password = os.environ.get("SMTP_PASSWORD")
    smtp_from = os.environ.get("SMTP_FROM")
    base_url = os.environ.get("PUBLIC_BASE_URL", "").rstrip("/")
    club = db.session.get(Club, invitation.club_id)

    if not all([
        smtp_host,
        smtp_username,
        smtp_password,
        smtp_from,
        base_url,
    ]):
        raise RuntimeError("SMTP configuration is incomplete.")

    # Build invitation link using the existing /join page.
    join_path = url_for("auth.join_page")
    invitation_url = (
        f"{base_url}{join_path}?"
        f"{urlencode({'token': invitation.token})}"
    )

    message = EmailMessage()
    message["Subject"] = "You're invited to join SetMaker"
    message["From"] = smtp_from
    message["To"] = invitation.email

    message.set_content(
        f"""Hello,

You have been invited to join a club on SetMaker.

Club: {club.name}
Role: {invitation.role.capitalize()}

Please use the link below to accept your invitation:

{invitation_url}

This invitation expires in 7 days.

If you weren't expecting this invitation, you can ignore this email.

SetMaker Team
"""
    )

    with smtplib.SMTP_SSL(
        smtp_host,
        smtp_port,
        context=ssl.create_default_context(),
        timeout=10,
    ) as smtp:
        smtp.login(smtp_username, smtp_password)
        smtp.send_message(message)

@team.post("/invitations")
@require_roles(*MANAGERS)
def create_invitation():
    actor = current_actor()
    data = body()
    email = validate_email(data.get("email"))
    try:
        role = Role(data.get("role"))
    except (ValueError, TypeError) as error:
        raise ApiError("role must be admin or staff.") from error
    if not can_invite_role(role, actor):
        raise ApiError("You cannot invite that role.", 403, "forbidden")
    if db.session.scalar(select(User).where(User.email == email)):
        raise ApiError(
            "An account with this email already exists.", 409, "email_exists"
        )
    pending = db.session.scalars(
        select(Invitation).where(
            Invitation.club_id == actor.club_id,
            Invitation.email == email,
            Invitation.accepted_at.is_(None),
            Invitation.expires_at > utc_now(),
        )
    ).all()
    if pending:
        raise ApiError(
            "A pending invitation already exists for this email.",
            409,
            "invitation_exists",
        )
    invitation = Invitation(
        club_id=actor.club_id,
        email=email,
        role=role.value,
        invited_by=actor.actor_id,
        token=secrets.token_urlsafe(32),
        expires_at=utc_now() + timedelta(days=7),
    )
    db.session.add(invitation)
    commit_or_conflict("The invitation could not be created.")

    try:
        send_invitation_email(invitation)

    except (
        smtplib.SMTPException,
        OSError,
        RuntimeError,
        ValueError,
    ):
        current_app.logger.exception("Invitation email delivery failed")
        # Prevent an unsent invitation from blocking another attempt.
        invitation.expires_at = utc_now() - timedelta(seconds=1)
        db.session.commit()

        raise ApiError(
            "Invitation email could not be sent. Please try again.",
            502,
            "email_delivery_failed",
        )

    response = jsonify({
        "message": "Invitation email submitted successfully.",
        "invitation": invitation_payload(invitation),
    })

    response.headers["Cache-Control"] = "no-store"

    return response, 201


@team.delete("/invitations/<invitation_id>")
@require_roles(*MANAGERS)
def revoke_invitation(invitation_id):
    invitation = invitation_for_club(invitation_id)
    if not can_invite_role(invitation.role):
        raise ApiError("You cannot revoke that invitation.", 403, "forbidden")
    if invitation.accepted_at is not None:
        raise ApiError(
            "This invitation was already accepted.", 409, "invitation_accepted"
        )
    invitation.expires_at = utc_now() - timedelta(seconds=1)
    db.session.commit()
    return "", 204
