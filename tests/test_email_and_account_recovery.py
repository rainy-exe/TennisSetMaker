"""Password reset, email verification, email change, password change,
log-in rate limiting and "Remember me".

Mail is captured by replacing app.mailer._deliver, so nothing is ever sent."""

import re
from urllib.parse import parse_qs, urlsplit

import pytest
from sqlalchemy import select

from app import account_security, create_app
from app.extensions import db
from app.models import User

PASSWORD = "Synthetic-password"
NEW_PASSWORD = "Synthetic-new-password"
BASE_URL = "https://setmaker.example.invalid"

MAIL_SETTINGS = {
    "SMTP_HOST": "smtp.example.invalid",
    "SMTP_PORT": "465",
    "SMTP_USERNAME": "synthetic-user",
    "SMTP_PASSWORD": "synthetic-not-a-real-secret",
    "SMTP_FROM": "SetMaker <noreply@example.invalid>",
    "SMTP_REPLY_TO": "support@example.invalid",
    "PUBLIC_BASE_URL": BASE_URL,
}


def make_app(tmp_path, monkeypatch, outbox, **config):
    monkeypatch.setattr("app.mailer._deliver", outbox.append)
    application = create_app(
        {
            "TESTING": True,
            "SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp_path / 'recovery.db'}",
            "RECAPTCHA_SECRET_KEY": None,
            "SECRET_KEY": "synthetic-test-secret",
            **config,
        }
    )
    with application.app_context():
        db.create_all()
    return application


@pytest.fixture
def outbox():
    return []


@pytest.fixture
def mail_app(tmp_path, monkeypatch, outbox):
    application = make_app(tmp_path, monkeypatch, outbox, **MAIL_SETTINGS)
    yield application
    with application.app_context():
        db.session.remove()
        db.drop_all()


@pytest.fixture
def no_mail_app(tmp_path, monkeypatch, outbox):
    for key in MAIL_SETTINGS:
        monkeypatch.delenv(key, raising=False)
    application = make_app(tmp_path, monkeypatch, outbox)
    yield application
    with application.app_context():
        db.session.remove()
        db.drop_all()


def write(client, method, path, payload=None):
    token = client.get("/csrf-token").get_json()["csrf_token"]
    return client.open(
        path, method=method, json=payload, headers={"X-CSRFToken": token}
    )


def register(client, email="owner@example.invalid"):
    response = write(
        client,
        "POST",
        "/register",
        {
            "full_name": "Synthetic Owner",
            "email": email,
            "password": PASSWORD,
            "club_name": "Synthetic Club",
        },
    )
    assert response.status_code == 201, response.get_json()
    return response.get_json()


def login(client, email="owner@example.invalid", password=PASSWORD, **extra):
    return write(client, "POST", "/login", {"email": email, "password": password, **extra})


def logged_in(client):
    return client.get("/check-session").get_json()["logged_in"]


def link_in(message):
    url = re.search(r"https://\S+", message.get_content()).group(0)
    parts = urlsplit(url)
    assert url.startswith(BASE_URL)
    return parts.path, parse_qs(parts.query)["token"][0]


def expire_cooldown(app, email):
    with app.app_context():
        user = db.session.scalar(select(User).where(User.email == email))
        user.last_account_email_at = None
        db.session.commit()


# ---------------------------------------------------------------- password reset


def test_password_reset_end_to_end_logs_out_every_device(mail_app, outbox):
    owner, other_device, visitor = (mail_app.test_client() for _ in range(3))
    created = register(owner)
    assert created["verification_email_sent"] is True
    assert outbox[-1]["To"] == "owner@example.invalid"
    assert outbox[-1]["Reply-To"] == "support@example.invalid"
    outbox.clear()
    expire_cooldown(mail_app, "owner@example.invalid")

    assert login(other_device).status_code == 200
    assert logged_in(other_device)

    response = write(visitor, "POST", "/forgot-password", {"email": "Owner@Example.invalid "})
    assert response.status_code == 200
    assert "If an account exists" in response.get_json()["message"]
    assert len(outbox) == 1
    message = outbox[0]
    assert message["To"] == "owner@example.invalid"
    assert "Reset your SetMaker password" == message["Subject"]
    path, token = link_in(message)
    assert path == "/reset-password"

    page = visitor.get(f"/reset-password?token={token}")
    assert page.status_code == 200
    assert "owner@example.invalid" in page.text
    assert "Choose a new password" in page.text

    short = write(visitor, "POST", "/reset-password", {"token": token, "password": "short"})
    assert short.status_code == 400
    assert short.get_json()["error"] == "invalid_password"

    done = write(visitor, "POST", "/reset-password", {"token": token, "password": NEW_PASSWORD})
    assert done.status_code == 200, done.get_json()

    # Every existing login is gone, the old password no longer works.
    assert not logged_in(other_device)
    assert other_device.get("/api/players").status_code == 401
    assert login(owner, password=PASSWORD).status_code == 401
    assert login(owner, password=NEW_PASSWORD).status_code == 200

    # The link works once.
    again = write(visitor, "POST", "/reset-password", {"token": token, "password": "Another-password-1"})
    assert again.status_code == 400
    assert again.get_json()["error"] == "invalid_token"
    assert "Link not valid" in visitor.get(f"/reset-password?token={token}").text

    # A "password changed" notice went to the account, and the reset
    # proved the inbox, so the email counts as confirmed.
    assert outbox[-1]["Subject"] == "Your SetMaker password was changed"
    with mail_app.app_context():
        user = db.session.scalar(select(User))
        assert user.email_verified_at is not None
        assert user.session_version == 1


def test_forgot_password_does_not_reveal_accounts_and_has_a_cooldown(mail_app, outbox):
    client = mail_app.test_client()
    register(client)
    outbox.clear()
    expire_cooldown(mail_app, "owner@example.invalid")

    unknown = write(client, "POST", "/forgot-password", {"email": "nobody@example.invalid"})
    known = write(client, "POST", "/forgot-password", {"email": "owner@example.invalid"})
    assert unknown.status_code == known.status_code == 200
    assert unknown.get_json() == known.get_json()
    assert [m["To"] for m in outbox] == ["owner@example.invalid"]

    # Asking again straight away: same reply, no second email.
    again = write(client, "POST", "/forgot-password", {"email": "owner@example.invalid"})
    assert again.get_json() == known.get_json()
    assert len(outbox) == 1

    assert write(client, "POST", "/forgot-password", {"email": "not-an-email"}).status_code == 400


def test_forgot_password_is_rate_limited_per_visitor(mail_app, outbox):
    client = mail_app.test_client()
    statuses = [
        write(client, "POST", "/forgot-password", {"email": f"n{i}@example.invalid"}).status_code
        for i in range(32)
    ]
    assert statuses[:30] == [200] * 30
    assert statuses[30:] == [429, 429]


def test_forgot_password_without_mail_settings_says_nothing_was_sent(no_mail_app, outbox):
    client = no_mail_app.test_client()
    register(client)
    assert client.get("/forgot-password").status_code == 200
    response = write(client, "POST", "/forgot-password", {"email": "owner@example.invalid"})
    assert response.status_code == 503
    assert response.get_json()["error"] == "password_reset_unavailable"
    assert outbox == []


def test_expired_or_tampered_reset_links_are_rejected(mail_app, outbox, monkeypatch):
    client = mail_app.test_client()
    register(client)
    expire_cooldown(mail_app, "owner@example.invalid")
    write(client, "POST", "/forgot-password", {"email": "owner@example.invalid"})
    _, token = link_in(outbox[-1])

    tampered = token[:-2] + ("AA" if not token.endswith("AA") else "BB")
    assert "Link not valid" in client.get(f"/reset-password?token={tampered}").text

    monkeypatch.setattr(account_security, "RESET_TOKEN_MAX_AGE", -1)
    response = write(client, "POST", "/reset-password", {"token": token, "password": NEW_PASSWORD})
    assert response.status_code == 400


# ---------------------------------------------------------------- verification


def test_email_verification_link_and_resend(mail_app, outbox):
    client = mail_app.test_client()
    register(client)
    path, token = link_in(outbox[-1])
    assert path == "/verify-email"
    assert login(client).status_code == 200

    account = client.get("/api/account").get_json()
    assert account["user"]["email_verified"] is False
    assert account["email_configured"] is True

    # Resend is limited to once a minute.
    resend = write(client, "POST", "/api/account/verification-email")
    assert resend.status_code == 429
    expire_cooldown(mail_app, "owner@example.invalid")
    assert write(client, "POST", "/api/account/verification-email").status_code == 202

    page = client.get(f"/verify-email?token={token}")
    assert page.status_code == 200
    assert "Email confirmed" in page.text
    assert client.get("/api/account").get_json()["user"]["email_verified"] is True
    already = write(client, "POST", "/api/account/verification-email")
    assert already.status_code == 200

    assert client.get("/verify-email?token=nonsense").status_code == 400


# ---------------------------------------------------------------- account settings


def test_change_password_keeps_this_device_and_logs_out_others(mail_app, outbox):
    this_device, other_device = mail_app.test_client(), mail_app.test_client()
    register(this_device)
    assert login(this_device).status_code == 200
    assert login(other_device).status_code == 200
    outbox.clear()

    wrong = write(
        this_device,
        "POST",
        "/api/account/password",
        {"current_password": "Not-the-password", "new_password": NEW_PASSWORD},
    )
    assert wrong.status_code == 400
    assert wrong.get_json()["error"] == "wrong_password"

    weak = write(
        this_device,
        "POST",
        "/api/account/password",
        {"current_password": PASSWORD, "new_password": "short"},
    )
    assert weak.status_code == 400

    changed = write(
        this_device,
        "POST",
        "/api/account/password",
        {"current_password": PASSWORD, "new_password": NEW_PASSWORD},
    )
    assert changed.status_code == 200, changed.get_json()
    assert logged_in(this_device)
    assert not logged_in(other_device)
    assert login(other_device, password=NEW_PASSWORD).status_code == 200
    assert [m["Subject"] for m in outbox] == ["Your SetMaker password was changed"]


def approve(client, token):
    csrf = client.get("/csrf-token").get_json()["csrf_token"]
    return client.post(
        "/confirm-email-change", data={"token": token, "csrf_token": csrf}
    )


def test_change_email_is_approved_from_the_current_address(mail_app, outbox):
    owner, someone_else = mail_app.test_client(), mail_app.test_client()
    register(owner)
    register(someone_else, "taken@example.invalid")
    assert login(owner).status_code == 200
    expire_cooldown(mail_app, "owner@example.invalid")
    outbox.clear()

    taken = write(
        owner,
        "POST",
        "/api/account/email",
        {"new_email": "taken@example.invalid", "current_password": PASSWORD},
    )
    assert taken.status_code == 409
    no_password = write(
        owner, "POST", "/api/account/email", {"new_email": "new@example.invalid"}
    )
    assert no_password.status_code == 400

    requested = write(
        owner,
        "POST",
        "/api/account/email",
        {"new_email": "New@Example.invalid", "current_password": PASSWORD},
    )
    assert requested.status_code == 202, requested.get_json()
    # The approval link goes to the CURRENT address, not the new one.
    assert [m["To"] for m in outbox] == ["owner@example.invalid"]
    assert "new@example.invalid" in outbox[0].get_content()
    path, token = link_in(outbox[0])
    assert path == "/confirm-email-change"

    # Opening the link only asks; nothing changes without the button.
    page = owner.get(f"/confirm-email-change?token={token}")
    assert page.status_code == 200
    assert "Approve email change" in page.text
    assert owner.get("/api/account").get_json()["user"]["email"] == "owner@example.invalid"

    # The approval form needs the CSRF token.
    assert owner.post("/confirm-email-change", data={"token": token}).status_code == 400

    done = approve(owner, token)
    assert done.status_code == 200
    assert "Email changed" in done.text
    account = owner.get("/api/account").get_json()["user"]
    assert account["email"] == "new@example.invalid"
    assert account["email_verified"] is False

    # The new address then gets a normal confirmation email.
    assert outbox[-1]["To"] == "new@example.invalid"
    path, verify_token = link_in(outbox[-1])
    assert path == "/verify-email"
    assert approve(owner, token).status_code == 400  # approval link works once
    assert owner.get(f"/verify-email?token={verify_token}").status_code == 200
    assert owner.get("/api/account").get_json()["user"]["email_verified"] is True

    fresh = mail_app.test_client()
    assert login(fresh, "owner@example.invalid").status_code == 401
    assert login(fresh, "new@example.invalid").status_code == 200


def test_email_change_approval_fails_if_the_address_was_taken_meanwhile(mail_app, outbox):
    owner = mail_app.test_client()
    register(owner)
    assert login(owner).status_code == 200
    expire_cooldown(mail_app, "owner@example.invalid")
    write(
        owner,
        "POST",
        "/api/account/email",
        {"new_email": "late@example.invalid", "current_password": PASSWORD},
    )
    _, token = link_in(outbox[-1])
    register(mail_app.test_client(), "late@example.invalid")
    assert owner.get(f"/confirm-email-change?token={token}").status_code == 409
    assert approve(owner, token).status_code == 409
    assert owner.get("/api/account").get_json()["user"]["email"] == "owner@example.invalid"


def test_update_own_name_and_settings_tab(mail_app):
    client = mail_app.test_client()
    register(client)
    assert login(client).status_code == 200
    renamed = write(client, "PATCH", "/api/account", {"full_name": "  Synthetic Renamed "})
    assert renamed.status_code == 200
    assert renamed.get_json()["user"]["full_name"] == "Synthetic Renamed"
    assert write(client, "PATCH", "/api/account", {"role": "owner"}).status_code == 400
    assert write(client, "PATCH", "/api/account", {"full_name": ""}).status_code == 400
    page = client.get("/settings")
    assert page.status_code == 200
    assert "My Account" in page.text
    assert "Synthetic Renamed" in page.text


def test_account_endpoints_need_login(mail_app):
    client = mail_app.test_client()
    assert client.get("/api/account").status_code == 401
    assert write(client, "POST", "/api/account/password", {}).status_code == 401


# ---------------------------------------------------------------- login


def test_login_failures_are_rate_limited_per_visitor_and_email(mail_app):
    client = mail_app.test_client()
    register(client)
    register(mail_app.test_client(), "other@example.invalid")
    for _ in range(10):
        assert login(client, password="Wrong-password").status_code == 401
    # Locked for this email, even with the right password...
    assert login(client).status_code == 429
    # ...but other accounts are unaffected.
    assert login(client, "other@example.invalid").status_code == 200


def test_remember_me_sets_a_lasting_cookie(mail_app):
    client = mail_app.test_client()
    register(client)
    short = login(client)
    assert "Expires=" not in short.headers["Set-Cookie"]
    lasting = login(client, remember=True)
    assert "Expires=" in lasting.headers["Set-Cookie"]


def test_new_passwords_need_eight_characters(mail_app):
    client = mail_app.test_client()
    response = write(
        client,
        "POST",
        "/register",
        {
            "full_name": "Synthetic Owner",
            "email": "short@example.invalid",
            "password": "seven77",
            "club_name": "Synthetic Club",
        },
    )
    assert response.status_code == 400
    assert response.get_json()["error"] == "invalid_password"


def test_sidebar_shows_the_logged_in_user_and_club_on_every_page(mail_app):
    client = mail_app.test_client()
    register(client)
    assert login(client).status_code == 200
    for path in ("/dashboard", "/players", "/sessions", "/history", "/settings", "/support"):
        page = client.get(path)
        assert page.status_code == 200, path
        sidebar = page.text.split('<aside class="sidebar">', 1)[1].split("</aside>", 1)[0]
        assert '<span class="avatar">S</span>' in sidebar, path
        assert "Synthetic Owner" in sidebar and "Synthetic Club" in sidebar, path
        nav = sidebar.split("<nav", 1)[1].split("</nav>", 1)[0]
        assert nav.count('<span class="icon">') == 9, path  # owner also sees Accounts
        assert "admin@setmaker.io" not in page.text and "Club Admin" not in page.text


def test_sidebar_user_menu_has_my_account_and_log_out(mail_app):
    client = mail_app.test_client()
    register(client)
    assert login(client).status_code == 200
    sidebar = client.get("/dashboard").text.split('<aside class="sidebar">', 1)[1]
    sidebar = sidebar.split("</aside>", 1)[0]
    assert 'href="/settings?tab=account"' in sidebar
    assert 'action="/logout"' in sidebar and 'name="csrf_token"' in sidebar
    csrf = client.get("/csrf-token").get_json()["csrf_token"]
    response = client.post("/logout", data={"csrf_token": csrf})
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/login-page?logged_out=1")
    assert not logged_in(client)
    assert client.get("/api/account").status_code == 401


def test_dashboard_greets_the_user_by_first_name(mail_app):
    client = mail_app.test_client()
    register(client)  # full name "Synthetic Owner"
    assert login(client).status_code == 200
    page = client.get("/dashboard").text
    assert "Welcome back, Synthetic" in page
    assert "Welcome back, Admin" not in page


def test_players_table_shows_one_dot_per_rating_point_and_sortable_headings(mail_app):
    client = mail_app.test_client()
    register(client)
    assert login(client).status_code == 200
    for name, skill in (("Synthetic Ten", 10), ("Synthetic Four", 4)):
        response = write(client, "POST", "/api/players", {"name": name, "gender": "F", "skill": skill})
        assert response.status_code == 201, response.get_json()
    page = client.get("/players").text
    for name, skill in (("Synthetic Ten", 10), ("Synthetic Four", 4)):
        row = page.split(f'<span class="player-name-text">{name}</span>', 1)[1].split("</tr>", 1)[0]
        assert row.count('<span class="filled"></span>') == skill, name
    for key in ("name", "rating", "gender"):
        assert f'data-sort="{key}"' in page


def test_page_scripts_find_the_page_form_not_the_sidebar_logout_form():
    # The sidebar now contains the Log Out form, so "the first form on the
    # page" is no longer the court/player/template form.
    from pathlib import Path

    script = Path(__file__).resolve().parents[1] / "app" / "static" / "js" / "integration.js"
    assert 'document.querySelector("form")' not in script.read_text()


def test_create_session_page_has_player_picker_and_one_line_extras(mail_app):
    client = mail_app.test_client()
    register(client)
    assert login(client).status_code == 200
    page = client.get("/sessions/create").text
    assert 'id="playerSearch"' in page and 'id="attendingChips"' in page
    assert 'id="allPlayersToggle"' in page and 'id="allPlayersPanel"' in page
    extras = page.split('class="session-extras"', 1)[1].split('class="attending-players"', 1)[0]
    for name in ('name="session_duration_minutes"', 'name="tea_break_after_round"', 'name="allowRelaxation"'):
        assert name in extras
    assert 'role="tooltip"' in extras
