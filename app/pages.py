"""Read-only page views. All writes go through the existing JSON APIs."""

from flask import (
    Blueprint,
    g,
    redirect,
    render_template,
    request,
    send_from_directory,
    url_for,
)
from sqlalchemy import select

from app.api import (
    court_for_club,
    player_for_club,
    serialize_session,
    serialize_template,
    session_for_club,
    template_for_club,
)
from app.errors import ApiError
from app.extensions import db
from app.models import Court, Invitation, Player, SessionTemplate, User
from app.permissions import (
    Role,
    can_delete_player,
    can_invite_role,
    can_manage_courts,
    can_manage_skill,
    can_remove_user,
    current_actor,
    require_roles,
)

pages = Blueprint("pages", __name__, template_folder="../frontend")


@pages.before_request
def authenticate_page():
    if request.endpoint == "pages.styles":
        return None
    try:
        g.page_actor = current_actor()
    except ApiError as error:
        if error.status_code == 401:
            return redirect(url_for("auth.login_page"))
        raise


@pages.app_context_processor
def page_permissions():
    actor = getattr(g, "page_actor", None)
    if actor is None:
        return {}
    return {
        "actor": actor,
        "can_manage_records": can_manage_courts(actor),
        "can_edit_skill": can_manage_skill(actor),
        "can_deactivate_player": can_delete_player(actor),
    }


def club_records(model, order):
    return db.session.scalars(
        select(model).where(model.club_id == g.page_actor.club_id).order_by(order)
    ).all()


@pages.get("/frontend/styles.css")
def styles():
    return send_from_directory(pages.root_path + "/../frontend", "styles.css")


@pages.get("/players")
def players():
    roster = club_records(Player, Player.name)
    selected = None
    if request.args.get("selected"):
        selected = player_for_club(request.args["selected"])
    elif roster:
        selected = roster[0]
    return render_template(
        "players.html", players=roster, selected_player=selected, active_page="players"
    )


@pages.get("/players/add")
def add_player():
    return render_template("addplayer.html", player=None, active_page="players")


@pages.get("/players/<player_id>/edit")
def edit_player(player_id):
    return render_template(
        "editplayer.html", player=player_for_club(player_id), active_page="players"
    )


@pages.get("/courts")
def courts():
    return render_template(
        "courts.html", courts=club_records(Court, Court.priority), active_page="courts"
    )


@pages.get("/courts/add")
@pages.get("/courts/<court_id>/edit")
@require_roles(Role.OWNER, Role.ADMIN)
def court_form(court_id=None):
    return render_template(
        "addcourt.html",
        court=court_for_club(court_id) if court_id else None,
        active_page="courts",
    )


@pages.get("/templates")
def templates():
    return render_template(
        "templates.html",
        templates=club_records(SessionTemplate, SessionTemplate.name),
        active_page="templates",
    )


@pages.get("/templates/add")
@pages.get("/templates/<template_id>/edit")
@require_roles(Role.OWNER, Role.ADMIN)
def template_form(template_id=None):
    template = template_for_club(template_id) if template_id else None
    return render_template(
        "edittemplate.html" if template else "addtemplate.html",
        template=serialize_template(template) if template else None,
        courts=club_records(Court, Court.priority),
        active_page="templates",
    )


@pages.get("/sessions/create")
def create_session():
    template = None
    if request.args.get("template"):
        template = template_for_club(request.args["template"])
    return render_template(
        "session_form.html",
        template=serialize_template(template) if template else None,
        courts=club_records(Court, Court.priority),
        players=club_records(Player, Player.name),
        active_page="sessions",
    )


@pages.get("/sessions/<session_id>")
def session_page(session_id):
    tennis_session = session_for_club(session_id)
    players = club_records(Player, Player.name)
    return render_template(
        "session_live.html",
        tennis_session=tennis_session,
        rounds=serialize_session(tennis_session)["rounds"],
        players=players,
        player_names={player.id: player.name for player in players},
        courts=club_records(Court, Court.priority),
        active_page="sessions",
    )


@pages.get("/accounts")
@require_roles(Role.OWNER, Role.ADMIN)
def accounts():
    return render_template(
        "team_members.html",
        users=club_records(User, User.email),
        invitations=club_records(Invitation, Invitation.created_at.desc()),
        can_remove_user=can_remove_user,
        can_invite_role=can_invite_role,
        active_page="accounts",
    )
