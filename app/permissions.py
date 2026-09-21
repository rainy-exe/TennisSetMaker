from dataclasses import dataclass
from enum import Enum
from functools import wraps

from flask import current_app

from app.errors import ApiError


class Role(str, Enum):
    OWNER = "owner"
    ADMINISTRATOR = "administrator"
    USER = "user"


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
        raise ApiError("Authentication is required.", 401, "authentication_required")
    if not isinstance(actor, CurrentActor):
        raise TypeError("CURRENT_ACTOR_PROVIDER must return CurrentActor")
    return actor


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


def require_authenticated(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        current_actor()
        return view(*args, **kwargs)

    return wrapped


def can_manage_skill(actor=None):
    actor = actor or current_actor()
    return actor.role in {Role.OWNER, Role.ADMINISTRATOR}
