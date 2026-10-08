from flask import Blueprint, redirect, render_template, request, url_for
from jinja2 import TemplateNotFound

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
        **context,
    }
    try:
        return render_template(template_name, page_data=data, **context)
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
    return render_template("index.html", message="Tennis SetMaker")


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
