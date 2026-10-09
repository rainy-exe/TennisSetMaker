from flask import Blueprint, redirect, render_template, request, url_for
from jinja2 import TemplateNotFound
from app.extensions import db
from app.models import Club, User

from app import api as endpoints
from app.errors import ApiError
from app.permissions import Role, can_manage_skill, current_actor, require_roles

main = Blueprint("main", __name__)


@main.before_request
def protect_management_pages():
    if request.endpoint != "main.home":
        current_actor()


def page(template_name, kind, **context):
    actor = current_actor()

    data = {
        "page": kind,
        "can_manage": actor.role in endpoints.MANAGERS,
        "can_manage_skill": can_manage_skill(actor),
    }

    try:
        return render_template(
            template_name,
            page_data=data,
            **context
        )
    except TemplateNotFound as error:
        raise ApiError(
            "A required frontend template is not available yet.",
            503,
            "frontend_unavailable",
        ) from error


def display_player(player):
    return {
        **player,
        "rating": player.get("skill"),
        "rating_dots": player.get("skill") or 0,
        "initials": "".join(word[0] for word in player["name"].split()[:2]).upper(),
        "member_since": player["created_at"][:10],
        "frequent_partners": [],
        "recent_history": [],
    }


@main.get("/")
def home():
    return render_template("main.html")


@main.get("/dashboard")
def dashboard():
    return page("dashboard.html", "dashboard")


@main.get("/players")
def players():
    roster = [
        display_player(item) for item in endpoints.list_players().get_json()["players"]
    ]
    selected_id = request.args.get("selected")
    selected = next((p for p in roster if p["id"] == selected_id), None)
    return page(
        "players.html",
        "players",
        players=roster,
        selected_player=selected or (roster[0] if roster else None),
        duplicate_player=None,
    )


@main.get("/courts")
def courts():
    return page(
        "courts.html", "courts", courts=endpoints.list_courts().get_json()["courts"]
    )


@main.get("/templates")
def templates():
    return page(
        "templates.html",
        "templates",
        templates=endpoints.list_templates().get_json()["session_templates"],
    )


@main.get("/sessions/create")
def create_session():
    template_id = request.args.get("template_id")
    template = (
        endpoints.serialize_template(endpoints.template_for_club(template_id))
        if template_id
        else None
    )
    return page("create-session.html", "session-form", template=template)


@main.get("/sessions/active")
def active_session():
    session_id = request.args.get("session_id")
    if not session_id:
        raise ApiError("A session_id is required. Select a session from the dashboard.")
    return active_session_by_id(session_id)


@main.get("/sessions/<session_id>")
def active_session_by_id(session_id):
    session = endpoints.serialize_session(endpoints.session_for_club(session_id))
    return page("active-session.html", "active-session", session=session)


@main.get("/players/add")
def add_player():
    return page("addplayer.html", "player-form", form={}, errors=[])


@main.get("/players/<player_id>/edit")
def edit_player(player_id):
    player = display_player(
        endpoints.serialize_player(endpoints.player_for_club(player_id))
    )
    return page("editplayer.html", "player-form", player=player)


@main.post("/players/<player_id>/delete")
@require_roles(Role.OWNER, Role.ADMIN)
def delete_player(player_id):
    # Retain Kate's form URL, reusing the API's isolation and soft delete.
    endpoints.deactivate_player(player_id)
    return redirect(url_for("main.players"))


@main.get("/courts/add")
@main.get("/courts/<court_id>/edit")
@require_roles(Role.OWNER, Role.ADMIN)
def add_court(court_id=None):
    court = (
        endpoints.serialize_court(endpoints.court_for_club(court_id))
        if court_id
        else None
    )
    return page("addcourt.html", "court-form", court=court)


@main.get("/templates/add")
@require_roles(Role.OWNER, Role.ADMIN)
def add_template():
    return page("addtemplate.html", "template-form", template=None)


@main.get("/templates/<template_id>/edit")
@require_roles(Role.OWNER, Role.ADMIN)
def edit_template(template_id):
    template = endpoints.serialize_template(endpoints.template_for_club(template_id))
    return page("edittemplate.html", "template-form", template=template)

@main.route("/support")
def support():
    return render_template("support.html")

@main.get("/sessions")
def sessions():
    return page("sessions.html", "sessions")

@main.get("/history")
def history():
    return page("history.html", "history")


@main.get("/settings")
def settings():
    actor = current_actor()

    club = db.session.get(Club, actor.club_id)

    users = (
        User.query
        .filter_by(club_id=actor.club_id, active=True)
        .order_by(User.created_at)
        .all()
    )

    team_members = [
        {
            "id": user.id,
            "name": user.full_name or user.email.split("@")[0],
            "email": user.email,
            "role": user.role.capitalize(),
            "joined": user.created_at.strftime("%d %b %Y"),
        }
        for user in users
    ]

    return page(
        "settings.html",
        "settings",
        club=club,
        team_members=team_members,
    )

@main.post("/settings/club")
@require_roles(Role.OWNER, Role.ADMIN)
def update_club():
    actor = current_actor()

    club = db.session.get(Club, actor.club_id)

    club_name = request.form.get("club_name", "").strip()

    if not 1 <= len(club_name) <= 120:
        raise ApiError("Club name must contain 1 to 120 characters.")

    club.name = club_name

    db.session.commit()

    return redirect(url_for("main.settings", tab="club"))


@main.get("/accounts")
def accounts():
    return page("accounts.html", "accounts")

@main.get("/settings/invite-member")
@require_roles(Role.OWNER, Role.ADMIN)
def invite_member():
    actor = current_actor()

    role_info = {
        "Admin": [
            "Manage players, courts and sessions.",
            "Invite and manage staff members."
        ],
        "Staff": [
            "Manage sessions and add players.",
            "Cannot manage team accounts."
        ],
    }

    assignable_roles = (
        ["Admin", "Staff"]
        if actor.role == Role.OWNER
        else ["Staff"]
    )

    return page(
        "invite_member.html",
        "settings",
        form={},
        errors=[],
        assignable_roles=assignable_roles,
        role_info=role_info,
    )