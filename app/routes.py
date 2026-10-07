from flask import Blueprint, render_template, request, redirect, url_for

from app.extensions import db
from app.models import Player, SessionTemplate

main = Blueprint("main", __name__)


@main.route("/")
def home():
    return render_template("dashboard.html")


@main.route("/dashboard")
def dashboard():
    return render_template("dashboard.html")


@main.route("/players")
def players():
    return render_template(
        "players.html",
        players=[],
        selected_player=None,
        duplicate_player=None
    )


@main.route("/courts")
def courts():
    return render_template("courts.html")


@main.route("/templates")
def templates():
    return render_template("templates.html")


@main.route("/sessions/create", methods=["GET", "POST"])
def create_session():
    if request.method == "POST":
        # temporary for now
        return redirect(url_for("main.active_session"))

    return render_template("create-session.html")


@main.route("/sessions/active")
def active_session():
    return render_template("active-session.html")


@main.route("/players/add")
def add_player():
    return render_template(
        "addplayer.html",
        form={},
        errors=[]
    )


@main.route("/players/<string:player_id>/edit")
def edit_player(player_id):
    player = Player.query.get_or_404(player_id)

    return render_template(
        "editplayer.html",
        player=player
    )


@main.route("/players/<string:player_id>/delete", methods=["POST"])
def delete_player(player_id):
    player = Player.query.get_or_404(player_id)

    db.session.delete(player)
    db.session.commit()

    return redirect(url_for("main.players"))


@main.route("/courts/add")
def add_court():
    return render_template("addcourt.html")


@main.route("/templates/add")
def add_template():
    return render_template("addtemplate.html")


@main.route("/templates/<string:template_id>/edit")
def edit_template(template_id):
    template = SessionTemplate.query.get_or_404(template_id)
    return render_template(
        "edittemplate.html",
        template=template
    )