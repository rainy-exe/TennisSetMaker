import os
from io import BytesIO
from urllib.parse import urlsplit

import qrcode
from flask import Blueprint, redirect, render_template, request, send_file, url_for
from jinja2 import TemplateNotFound
from sqlalchemy import select

from app import api as endpoints
from app.errors import ApiError
from app.extensions import db
from app.models import Club, Court, Player, Round, TennisSession, User
from app.permissions import Role, can_manage_skill, current_actor, require_roles

main = Blueprint("main", __name__)


@main.before_request
def protect_management_pages():
    public_pages = {
        "main.home",
        "main.public_session",
    }

    if request.endpoint not in public_pages:
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

    return page(
        "active-session.html",
        "active-session",
        session=session,
        session_id=session["id"],
    )


@main.get("/club/qr-code")
def club_qr_code():
    actor = current_actor()

    club = db.session.get(Club, actor.club_id)

    if not club or not club.public_token:
        raise ApiError("Club QR code is not configured.", 404)

    public_path = url_for("main.public_session", token=club.public_token)

    base_url = os.environ.get("PUBLIC_BASE_URL", request.url_root).rstrip("/")
    try:
        parts = urlsplit(base_url)
    except ValueError as error:
        raise ApiError(
            "PUBLIC_BASE_URL must be an HTTP(S) URL.", 503, "qr_configuration_error"
        ) from error
    if (
        parts.scheme not in {"http", "https"}
        or not parts.hostname
        or parts.username
        or parts.password
        or parts.query
        or parts.fragment
    ):
        raise ApiError(
            "PUBLIC_BASE_URL must be an HTTP(S) URL.", 503, "qr_configuration_error"
        )

    public_url = base_url + public_path

    qr = qrcode.QRCode(
        version=None,
        box_size=10,
        border=4,
    )

    qr.add_data(public_url)
    qr.make(fit=True)

    image = qr.make_image(fill_color="black", back_color="white")

    output = BytesIO()
    image.save(output, format="PNG")
    output.seek(0)

    response = send_file(output, mimetype="image/png")
    response.headers["Cache-Control"] = "private, no-store"

    return response


@main.get("/club/<token>/live")
def public_session(token):
    club = db.session.scalar(
        select(Club).where(Club.public_token == token, Club.active.is_(True))
    )

    if club is None:
        raise ApiError("Club not found.", 404, "not_found")

    # Find the most recently CREATED session.
    session = db.session.scalar(
        select(TennisSession)
        .where(TennisSession.club_id == club.id)
        .order_by(TennisSession.created_at.desc(), TennisSession.id.desc())
        .limit(1)
    )

    result = {
        "club_name": club.name,
        "session_name": None,
        "round_number": None,
        "matches": [],
        "sit_out_players": [],
    }

    if session is not None:
        result["session_name"] = session.name

        # Find the latest generated round.
        latest_round = db.session.scalar(
            select(Round)
            .where(Round.session_id == session.id)
            .order_by(Round.number.desc())
            .limit(1)
        )

        if latest_round is not None:
            result["round_number"] = latest_round.number

            # Always display the FINAL assignments, including
            # the organiser's saved manual changes.
            final = endpoints.serialize_round(latest_round)["final"]

            # Restrict lookup to the current club.
            players = db.session.scalars(
                select(Player).where(Player.club_id == club.id)
            ).all()

            courts = db.session.scalars(
                select(Court).where(Court.club_id == club.id)
            ).all()

            player_names = {player.id: player.name for player in players}
            player_genders = {p.id: p.gender for p in players}

            def public_player(player_id):
                return {
                    "name": player_names.get(player_id, "Unknown player"),
                    "gender": player_genders.get(player_id),
                }

            court_numbers = {court.id: court.number for court in courts}

            for assignment in final["assignments"]:
                result["matches"].append(
                    {
                        "court": court_numbers.get(assignment["court_id"], "Unknown"),
                        "team_a": [
                            public_player(pid)
                            for pid in assignment["team_a_player_ids"]
                        ],
                        "team_b": [
                            public_player(pid)
                            for pid in assignment["team_b_player_ids"]
                        ],
                    }
                )

            result["sit_out_players"] = [
                public_player(pid) for pid in final["sit_out_player_ids"]
            ]

    response = render_template("public-session.html", result=result)

    return (
        response,
        200,
        {
            "Cache-Control": "no-store",
            "Referrer-Policy": "no-referrer",
            "X-Robots-Tag": "noindex, nofollow",
        },
    )


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
        User.query.filter_by(club_id=actor.club_id, active=True)
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
        club={"id": club.id, "name": club.name} if club else None,
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
@require_roles(Role.OWNER, Role.ADMIN)
def accounts():
    return page("accounts.html", "accounts")


@main.get("/settings/invite-member")
@require_roles(Role.OWNER, Role.ADMIN)
def invite_member():
    actor = current_actor()

    role_info = {
        "Admin": [
            "Manage players, courts and sessions.",
            "Invite and manage staff members.",
        ],
        "Staff": ["Manage sessions and add players.", "Cannot manage team accounts."],
    }

    assignable_roles = ["Admin", "Staff"] if actor.role == Role.OWNER else ["Staff"]

    return page(
        "invite_member.html",
        "settings",
        form={},
        errors=[],
        assignable_roles=assignable_roles,
        role_info=role_info,
    )
