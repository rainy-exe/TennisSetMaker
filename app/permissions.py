from dataclasses import dataclass
from enum import Enum
from functools import wraps

from flask import current_app

from app.errors import ApiError


class Role(str, Enum):
    OWNER = "owner"
    ADMIN = "admin"
    STAFF = "staff"


@dataclass(frozen=True)
class CurrentActor:
    actor_id: str
    club_id: str
    role: Role


def current_actor():
    provider = current_app.config.get("CURRENT_ACTOR_PROVIDER")

    if provider is None:
        raise ApiError(
            "Authentication integration is not configured.",
            401,
            "authentication_required",
        )

    actor = provider()

    if actor is None:
        raise ApiError(
            "Authentication is required.",
            401,
            "authentication_required",
        )

    if not isinstance(actor, CurrentActor):
        raise TypeError(
            "CURRENT_ACTOR_PROVIDER must return CurrentActor"
        )

    return actor


def require_authenticated(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        current_actor()
        return view(*args, **kwargs)

    return wrapped


def require_roles(*roles):
    allowed = {Role(role) for role in roles}

    def decorator(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            actor = current_actor()

            if actor.role not in allowed:
                raise ApiError(
                    "You do not have permission to perform this action.",
                    403,
                    "forbidden",
                )

            return view(*args, **kwargs)

        return wrapped

    return decorator


# ---------------------------------------------------------
# Player permissions
# ---------------------------------------------------------

def can_view_players(actor=None):
    actor = actor or current_actor()

    return actor.role in {
        Role.OWNER,
        Role.ADMIN,
        Role.STAFF,
    }


def can_add_player(actor=None):
    actor = actor or current_actor()

    return actor.role in {
        Role.OWNER,
        Role.ADMIN,
        Role.STAFF,
    }


def can_delete_player(actor=None):
    actor = actor or current_actor()

    return actor.role in {
        Role.OWNER,
        Role.ADMIN,
    }


# Everyone can see player skill levels.
def can_view_skill(actor=None):
    actor = actor or current_actor()

    return actor.role in {
        Role.OWNER,
        Role.ADMIN,
        Role.STAFF,
    }


# Only Owner/Admin can edit a player's skill level.
def can_manage_skill(actor=None):
    actor = actor or current_actor()

    return actor.role in {
        Role.OWNER,
        Role.ADMIN,
    }


# ---------------------------------------------------------
# Invitation permissions
# ---------------------------------------------------------

def can_invite_admin(actor=None):
    actor = actor or current_actor()

    return actor.role == Role.OWNER


def can_invite_staff(actor=None):
    actor = actor or current_actor()

    return actor.role in {
        Role.OWNER,
        Role.ADMIN,
    }


def can_invite_role(role, actor=None):
    actor = actor or current_actor()
    role = Role(role)

    if role == Role.ADMIN:
        return can_invite_admin(actor)

    if role == Role.STAFF:
        return can_invite_staff(actor)

    # Nobody can invite another owner.
    return False


# ---------------------------------------------------------
# Account removal permissions
# ---------------------------------------------------------

def can_remove_user(target_role, actor=None):
    actor = actor or current_actor()
    target_role = Role(target_role)

    # Nobody can remove the Owner account.
    if target_role == Role.OWNER:
        return False

    # Owner can remove Admin or Staff.
    if actor.role == Role.OWNER:
        return target_role in {
            Role.ADMIN,
            Role.STAFF,
        }

    # Admin can only remove Staff.
    if actor.role == Role.ADMIN:
        return target_role == Role.STAFF

    # Staff cannot remove any account.
    return False


# ---------------------------------------------------------
# Club permissions
# ---------------------------------------------------------

def can_manage_club(actor=None):
    actor = actor or current_actor()

    return actor.role == Role.OWNER


def can_manage_courts(actor=None):
    actor = actor or current_actor()

    return actor.role in {
        Role.OWNER,
        Role.ADMIN,
    }


def can_manage_sessions(actor=None):
    actor = actor or current_actor()

    return actor.role in {
        Role.OWNER,
        Role.ADMIN,
        Role.STAFF,
    }


# ---------------------------------------------------------
# Club isolation
# ---------------------------------------------------------

def require_same_club(club_id, actor=None):
    actor = actor or current_actor()

    if actor.club_id != club_id:
        raise ApiError(
            "You do not have permission to access this club.",
            403,
            "forbidden",
        )

    return True