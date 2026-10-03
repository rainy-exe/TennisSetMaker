from flask import Blueprint, g, redirect, render_template, session, url_for
from sqlalchemy import select

from app.auth import session_actor_provider
from app.extensions import db
from app.models import TennisSession, User

main = Blueprint("main", __name__)


@main.route("/")
def home():
    return render_template("index.html", message="Hello, Flask is working!")


@main.get("/dashboard")
def dashboard():
    actor = session_actor_provider()
    if actor is None:
        return redirect(url_for("auth.login_page"))
    g.page_actor = actor
    user = db.session.get(User, session["user_id"])
    sessions = db.session.scalars(
        select(TennisSession)
        .where(
            TennisSession.club_id == actor.club_id,
        )
        .order_by(TennisSession.starts_at.desc())
    ).all()
    return render_template(
        "account_home.html", user=user, club=user.club, sessions=sessions
    )
