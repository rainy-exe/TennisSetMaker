from copy import deepcopy

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from werkzeug.security import check_password_hash

from app import create_app
from app.extensions import db
from app.models import Club, Invitation, Round, User
from app.team import send_invitation_email


@pytest.fixture
def real_app(tmp_path, monkeypatch):
    # Record only synthetic mail. Never send real invitations during tests.
    sent = {}
    monkeypatch.setattr(
        "app.team.send_invitation_email",
        lambda invitation: sent.update({invitation.id: invitation.token}),
    )
    application = create_app(
        {
            "TESTING": True,
            "SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp_path / 'accounts.db'}",
            "RECAPTCHA_SECRET_KEY": None,
            "ALGORITHM_RANDOM_SEED": 9,
        }
    )
    application.config["TEST_SENT_INVITATIONS"] = sent
    with application.app_context():
        assert db.session.execute(text("PRAGMA foreign_keys")).scalar() == 1
        db.create_all()
    yield application
    with application.app_context():
        db.session.remove()
        db.drop_all()


def write(client, method, path, payload=None):
    if path == "/accept-invitation" and isinstance(payload, dict):
        payload = {"full_name": "Synthetic Member", **payload}
    token = client.get("/csrf-token").get_json()["csrf_token"]
    return client.open(
        path, method=method, json=payload, headers={"X-CSRFToken": token}
    )


def register(client, email="owner@example.invalid", name="Synthetic Club"):
    response = write(
        client,
        "POST",
        "/register",
        {
            "full_name": "Synthetic Owner",
            "email": email,
            "password": "Synthetic-password",
            "club_name": name,
        },
    )
    assert response.status_code == 201, response.get_json()
    assert client.get("/api/players").status_code == 401
    assert (
        write(
            client, "POST", "/login", {"email": email, "password": "Synthetic-password"}
        ).status_code
        == 200
    )
    return response.get_json()["user"]


def invitation_token(client, payload):
    # Tokens come from our fake mail transport, never from the public API.
    return client.application.config["TEST_SENT_INVITATIONS"][
        payload["invitation"]["id"]
    ]


def invite(owner, member, role, email):
    response = write(owner, "POST", "/api/invitations", {"role": role, "email": email})
    assert response.status_code == 201, response.get_json()
    payload = response.get_json()
    assert "token" not in payload
    payload = {**payload, "token": invitation_token(owner, payload)}
    accepted = write(
        member,
        "POST",
        "/accept-invitation",
        {
            "token": payload["token"],
            "password": "Synthetic-password",
        },
    )
    assert accepted.status_code == 201, accepted.get_json()
    return accepted.get_json()["user"], payload


def test_real_login_csrf_password_storage_and_logout(real_app):
    client = real_app.test_client()
    assert client.post("/register", json={}).status_code == 400
    token = client.get("/csrf-token").get_json()["csrf_token"]
    user = register(client)
    with real_app.app_context():
        row = db.session.get(User, user["id"])
        assert row.password_hash != "Synthetic-password"
        assert check_password_hash(row.password_hash, "Synthetic-password")
    payload = {"name": "Synthetic Player", "gender": "F", "skill": 5}
    assert (
        client.post("/api/players", json=payload).get_json()["error"] == "csrf_failed"
    )
    assert (
        client.post(
            "/api/players", json=payload, headers={"X-CSRFToken": token}
        ).status_code
        == 400
    )
    assert write(client, "POST", "/api/players", payload).status_code == 201
    assert client.get("/logout").status_code == 405
    assert write(client, "POST", "/logout").status_code == 302
    assert client.get("/api/players").status_code == 401
    assert (
        write(
            client, "POST", "/login", {"email": user["email"], "password": "wrong"}
        ).status_code
        == 401
    )
    assert (
        write(
            client,
            "POST",
            "/login",
            {"email": user["email"], "password": "Synthetic-password"},
        ).status_code
        == 200
    )
    assert client.get("/check-session").get_json()["logged_in"] is True
    assert client.get("/api/players").headers["Cache-Control"] == "no-store"


def test_invite_role_change_removal_and_owner_protection(real_app):
    owner, admin, staff = [real_app.test_client() for _ in range(3)]
    owner_user = register(owner)
    admin_user, _ = invite(owner, admin, "admin", "admin@example.invalid")
    staff_user, _ = invite(admin, staff, "staff", "staff@example.invalid")
    assert (
        write(
            admin,
            "POST",
            "/api/invitations",
            {"role": "admin", "email": "another@example.invalid"},
        ).status_code
        == 403
    )
    assert (
        write(
            owner,
            "POST",
            "/api/invitations",
            {"role": "owner", "email": "another@example.invalid"},
        ).status_code
        == 403
    )
    assert staff.get("/api/users").status_code == 403
    assert (
        write(
            staff,
            "POST",
            "/api/invitations",
            {"role": "staff", "email": "another@example.invalid"},
        ).status_code
        == 403
    )
    assert write(admin, "DELETE", f"/api/users/{owner_user['id']}").status_code == 403
    assert write(owner, "DELETE", f"/api/users/{owner_user['id']}").status_code == 403
    assert (
        write(
            admin, "PATCH", f"/api/users/{staff_user['id']}", {"role": "admin"}
        ).status_code
        == 403
    )
    assert (
        write(
            owner, "PATCH", f"/api/users/{staff_user['id']}", {"role": "admin"}
        ).status_code
        == 200
    )
    assert (
        staff.get("/api/users").status_code == 200
    )  # Current DB role, not a cached session role.
    assert write(owner, "DELETE", f"/api/users/{admin_user['id']}").status_code == 204
    assert admin.get("/api/players").status_code == 401
    assert admin.get("/check-session").get_json()["logged_in"] is False


def test_invitation_duplicate_expiry_revoke_one_use_and_no_token_leak(real_app):
    owner, member = real_app.test_client(), real_app.test_client()
    register(owner)
    data = {"role": "staff", "email": "member@example.invalid"}
    created = write(owner, "POST", "/api/invitations", data).get_json()
    assert "token" not in created
    assert "accept_endpoint" not in created
    created["token"] = invitation_token(owner, created)
    assert write(owner, "POST", "/api/invitations", data).status_code == 409
    assert created["token"] not in owner.get("/api/invitations").get_data(as_text=True)
    assert "password_hash" not in owner.get("/api/users").get_data(as_text=True)
    assert (
        write(
            owner, "DELETE", f"/api/invitations/{created['invitation']['id']}"
        ).status_code
        == 204
    )
    assert (
        write(
            member,
            "POST",
            "/accept-invitation",
            {"token": created["token"], "password": "Synthetic-password"},
        ).status_code
        == 410
    )
    user, accepted = invite(owner, member, "staff", data["email"])
    assert (
        write(
            member,
            "POST",
            "/accept-invitation",
            {"token": accepted["token"], "password": "Synthetic-password"},
        ).status_code
        == 409
    )
    assert user["role"] == "staff"


def test_disabled_club_blocks_login_existing_cookie_and_invitation(real_app):
    owner, member = real_app.test_client(), real_app.test_client()
    user = register(owner)
    created = write(
        owner,
        "POST",
        "/api/invitations",
        {"role": "staff", "email": "member@example.invalid"},
    ).get_json()
    created["token"] = invitation_token(owner, created)
    with real_app.app_context():
        db.session.get(Club, user["club_id"]).active = False
        db.session.commit()
    assert owner.get("/api/players").status_code == 401
    assert owner.get("/check-session").get_json()["logged_in"] is False
    assert (
        write(
            owner,
            "POST",
            "/login",
            {"email": user["email"], "password": "Synthetic-password"},
        ).status_code
        == 401
    )
    assert (
        write(
            member,
            "POST",
            "/accept-invitation",
            {"token": created["token"], "password": "Synthetic-password"},
        ).status_code
        == 410
    )


def test_team_isolation_and_removed_inviter(real_app):
    owner, admin, outsider, invited = [real_app.test_client() for _ in range(4)]
    first_owner = register(owner)
    admin_user, _ = invite(owner, admin, "admin", "admin@example.invalid")
    other_owner = register(outsider, "outsider@example.invalid", "Other Club")
    invitation = write(
        admin,
        "POST",
        "/api/invitations",
        {"role": "staff", "email": "invited@example.invalid"},
    ).get_json()
    invitation["token"] = invitation_token(admin, invitation)
    assert (
        write(outsider, "DELETE", f"/api/users/{admin_user['id']}").status_code == 404
    )
    assert (
        write(
            outsider, "PATCH", f"/api/users/{first_owner['id']}", {"role": "staff"}
        ).status_code
        == 404
    )
    assert (
        write(
            outsider, "DELETE", f"/api/invitations/{invitation['invitation']['id']}"
        ).status_code
        == 404
    )
    assert (
        owner.get("/api/users").get_json()["users"][0]["club_id"]
        != other_owner["club_id"]
    )
    assert write(owner, "DELETE", f"/api/users/{admin_user['id']}").status_code == 204
    assert (
        write(
            invited,
            "POST",
            "/accept-invitation",
            {"token": invitation["token"], "password": "Synthetic-password"},
        ).status_code
        == 410
    )


@pytest.mark.parametrize(
    "payload",
    [
        [],
        {"email": 9},
        {"email": "bad", "password": "x", "club_name": "Club"},
        {"email": "valid@example.invalid", "password": [], "club_name": "Club"},
        {"email": "valid@example.invalid", "password": "x", "club_name": []},
    ],
)
def test_invalid_account_payloads_are_atomic(real_app, payload):
    # Supply the new required field so these still exercise the original
    # malformed email/password/club cases, rather than failing early on name.
    if isinstance(payload, dict):
        payload = {"full_name": "Synthetic Owner", **payload}
    response = write(real_app.test_client(), "POST", "/register", payload)
    assert response.status_code == 400
    with real_app.app_context():
        assert db.session.scalar(select(User)) is None
        assert db.session.scalar(select(Club)) is None


def test_invitation_commit_failure_does_not_consume_token(real_app, monkeypatch):
    owner, member = real_app.test_client(), real_app.test_client()
    register(owner)
    created = write(
        owner,
        "POST",
        "/api/invitations",
        {"role": "staff", "email": "member@example.invalid"},
    ).get_json()
    created["token"] = invitation_token(owner, created)

    def fail():
        raise IntegrityError("synthetic conflict", {}, Exception())

    with real_app.app_context():
        with monkeypatch.context() as patch:
            patch.setattr(db.session, "commit", fail)
            assert (
                write(
                    member,
                    "POST",
                    "/accept-invitation",
                    {"token": created["token"], "password": "Synthetic-password"},
                ).status_code
                == 409
            )
        invitation = db.session.get(Invitation, created["invitation"]["id"])
        assert invitation.accepted_at is None
    assert (
        write(
            member,
            "POST",
            "/accept-invitation",
            {"token": created["token"], "password": "Synthetic-password"},
        ).status_code
        == 201
    )


def test_real_session_generation_override_live_changes_and_isolation(real_app):
    owner, staff, outsider = [real_app.test_client() for _ in range(3)]
    register(owner)
    invite(owner, staff, "staff", "staff@example.invalid")
    register(outsider, "outsider@example.invalid", "Other synthetic club")
    players = []
    for index in range(9):
        result = write(
            owner,
            "POST",
            "/api/players",
            {
                "name": f"Synthetic {index}",
                "gender": "M" if index % 2 else "F",
                "skill": 5,
            },
        )
        assert result.status_code == 201
        players.append(result.get_json()["player"])
    court = write(
        owner, "POST", "/api/courts", {"number": 1, "court_type": "hard"}
    ).get_json()["court"]
    assert (
        write(
            staff, "POST", "/api/courts", {"number": 2, "court_type": "hard"}
        ).status_code
        == 403
    )
    assert (
        write(
            staff, "PATCH", f"/api/players/{players[0]['id']}", {"skill": 7}
        ).status_code
        == 403
    )
    assert (
        staff.get(f"/api/players/{players[0]['id']}").get_json()["player"]["skill"] == 5
    )
    assert outsider.get(f"/api/players/{players[0]['id']}").status_code == 404
    response = write(
        staff,
        "POST",
        "/api/sessions",
        {
            "name": "Synthetic social",
            "starts_at": "2026-10-02T13:00:00",
            "number_of_rounds": 3,
            "round_duration_minutes": 30,
            "court_ids": [court["id"]],
            "player_ids": [p["id"] for p in players[:8]],
        },
    )
    assert response.status_code == 201, response.get_json()
    session_id = response.get_json()["session"]["id"]
    generated = write(staff, "POST", f"/api/sessions/{session_id}/rounds/generate")
    assert generated.status_code == 201, generated.get_json()
    round_ = generated.get_json()["round"]
    original = deepcopy(round_["generated"])
    final = deepcopy(round_["final"])
    sitting = final["sit_out_player_ids"][0]
    replaced = final["assignments"][0]["team_a_player_ids"][0]
    final["assignments"][0]["team_a_player_ids"][0] = sitting
    final["sit_out_player_ids"][0] = replaced
    invalid = deepcopy(final)
    invalid["assignments"][0]["team_a_player_ids"][1] = sitting
    assert (
        write(staff, "PUT", f"/api/rounds/{round_['id']}/final", invalid).status_code
        == 400
    )
    assert (
        write(staff, "PUT", f"/api/rounds/{round_['id']}/final", final).status_code
        == 200
    )
    reloaded = staff.get(f"/api/rounds/{round_['id']}").get_json()["round"]
    assert reloaded["generated"] == original
    assert reloaded["final"] == final
    assert outsider.get(f"/api/rounds/{round_['id']}").status_code == 404
    assert (
        write(staff, "POST", f"/api/rounds/{round_['id']}/complete").status_code == 200
    )
    assert write(staff, "POST", f"/api/rounds/{round_['id']}/reopen").status_code == 403
    assert (
        write(
            staff,
            "POST",
            f"/api/sessions/{session_id}/players",
            {"player_id": players[8]["id"]},
        ).status_code
        == 201
    )
    assert (
        write(
            staff,
            "PATCH",
            f"/api/sessions/{session_id}/players/{players[0]['id']}",
            {"reason": "Left early"},
        ).status_code
        == 200
    )
    with real_app.app_context():
        from app.models import Player, TennisSession
        from app.round_service import _history_for

        session_row = db.session.get(TennisSession, session_id)
        history = {
            p.id: p
            for p in _history_for(
                session_row, db.session.scalars(select(Player)).all(), 2
            )
        }
        assert history[replaced].sit_out_count == 1
        assert history[replaced].consecutive_sit_outs == 1
        assert history[sitting].sit_out_count == 0
        team = final["assignments"][0]["team_a_player_ids"]
        assert team[1] in history[team[0]].partner_ids
    assert (
        write(staff, "POST", f"/api/sessions/{session_id}/rounds/generate").status_code
        == 201
    )
    with real_app.app_context():
        assert not db.session.execute(text("PRAGMA foreign_key_check")).all()
        assert db.session.query(Round).count() == 2


def test_account_frontend_available_and_no_fake_reset_claim(real_app):
    client = real_app.test_client()
    assert client.get("/").status_code == 200
    for path in ("/login-page", "/signup-page"):
        response = client.get(path)
        assert response.status_code == 200
        assert "csrf" in response.text.lower()
    for path in ("/dashboard", "/players", "/courts", "/templates", "/sessions/create"):
        assert client.get(path).status_code == 401
    assert client.get("/accounts").status_code == 401
    assert client.get("/invitation-page").status_code == 404
    assert client.get("/forgot-password").status_code == 503
    response = write(
        client, "POST", "/forgot-password", {"email": "owner@example.invalid"}
    )
    assert response.status_code == 503
    assert b"No reset email has been sent" in response.data


def test_login_rejects_get_and_missing_csrf(real_app):
    client = real_app.test_client()
    assert client.get("/login").status_code == 405
    response = client.post(
        "/login",
        json={"email": "owner@example.invalid", "password": "Synthetic-password"},
    )
    assert response.status_code == 400
    assert response.get_json()["error"] == "csrf_failed"


def test_invitation_delivery_failure_expires_token_and_allows_retry(
    real_app, monkeypatch
):
    from app.models import utc_now

    owner, member = real_app.test_client(), real_app.test_client()
    register(owner)
    delivered = []

    def fail_delivery(invitation):
        delivered.append(invitation.token)
        raise RuntimeError("Synthetic SMTP failure")

    monkeypatch.setattr("app.team.send_invitation_email", fail_delivery)
    data = {"email": "retry@example.invalid", "role": "staff"}
    response = write(owner, "POST", "/api/invitations", data)
    assert response.status_code == 502
    assert response.get_json()["error"] == "email_delivery_failed"
    assert delivered[0] not in response.text
    with real_app.app_context():
        invitation = db.session.scalar(
            select(Invitation).where(Invitation.email == data["email"])
        )
        assert invitation.expires_at < utc_now()
    assert (
        write(
            member,
            "POST",
            "/accept-invitation",
            {"token": delivered[0], "password": "Synthetic-password"},
        ).status_code
        == 410
    )
    monkeypatch.setattr(
        "app.team.send_invitation_email",
        lambda invitation: real_app.config["TEST_SENT_INVITATIONS"].update(
            {invitation.id: invitation.token}
        ),
    )
    user, _ = invite(owner, member, "staff", data["email"])
    assert user["full_name"] == "Synthetic Member"


def test_smtp_message_targets_join_page_and_keeps_token_out_of_api(
    real_app, monkeypatch
):
    from urllib.parse import parse_qs, urlsplit

    owner = real_app.test_client()
    register(owner)
    response = write(
        owner,
        "POST",
        "/api/invitations",
        {"email": "mail@example.invalid", "role": "staff"},
    )
    assert response.status_code == 201
    payload = response.get_json()
    assert "token" not in payload
    sent = []
    options = []

    class SyntheticSMTP:
        def __init__(self, host, port, **kwargs):
            options.append((host, port, kwargs))

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def login(self, username, password):
            assert username == "synthetic-smtp-user"
            assert password == "synthetic-not-a-real-secret"

        def send_message(self, message):
            sent.append(message)

    for key, value in {
        "SMTP_HOST": "smtp.example.invalid",
        "SMTP_PORT": "465",
        "SMTP_USERNAME": "synthetic-smtp-user",
        "SMTP_PASSWORD": "synthetic-not-a-real-secret",
        "SMTP_FROM": "sender@example.invalid",
        "PUBLIC_BASE_URL": "https://setmaker.example.invalid/",
    }.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setattr("app.team.smtplib.SMTP_SSL", SyntheticSMTP)
    with real_app.test_request_context():
        invitation = db.session.get(Invitation, payload["invitation"]["id"])
        send_invitation_email(invitation)
        content = sent[0].get_content()
        link = next(
            line for line in content.splitlines() if line.startswith("https://")
        )
        assert urlsplit(link).path == "/join"
        assert parse_qs(urlsplit(link).query)["token"] == [invitation.token]
        assert "Synthetic Club" in content
        assert invitation.token not in owner.get("/api/invitations").text
    assert sent[0]["To"] == "mail@example.invalid"
    assert options[0][2]["timeout"] == 10
    assert options[0][2]["context"].check_hostname is True


def test_settings_and_management_pages_use_authenticated_club_data(real_app):
    owner, outsider, staff = [real_app.test_client() for _ in range(3)]
    own_user = register(owner)
    register(outsider, "outsider@example.invalid", "Synthetic Other Club")
    invite(owner, staff, "staff", "staff@example.invalid")
    page = owner.get("/settings")
    assert page.status_code == 200
    assert "Synthetic Club" in page.text
    assert own_user["email"] in page.text
    assert "outsider@example.invalid" not in page.text
    assert owner.get("/accounts").status_code == 200
    assert staff.get("/accounts").status_code == 403
    assert (
        staff.post("/settings/club", data={"club_name": "Must not change"}).status_code
        == 400
    )
    token = staff.get("/csrf-token").get_json()["csrf_token"]
    assert (
        staff.post(
            "/settings/club", data={"club_name": "Must not change", "csrf_token": token}
        ).status_code
        == 403
    )
    token = owner.get("/csrf-token").get_json()["csrf_token"]
    assert (
        owner.post(
            "/settings/club",
            data={"club_name": "Synthetic Updated", "csrf_token": token},
        ).status_code
        == 302
    )
    assert "Synthetic Updated" in owner.get("/settings").text
    assert "Synthetic Updated" not in outsider.get("/settings").text


@pytest.mark.parametrize("full_name", [None, "", " " * 4, "x" * 121, 123])
def test_register_rejects_missing_or_invalid_full_name_without_partial_account(
    real_app, full_name
):
    client = real_app.test_client()
    response = write(
        client,
        "POST",
        "/register",
        {
            "full_name": full_name,
            "email": "invalid-name@example.invalid",
            "password": "Synthetic-password",
            "club_name": "Synthetic",
        },
    )
    assert response.status_code == 400
    with real_app.app_context():
        assert db.session.scalar(select(User)) is None
        assert db.session.scalar(select(Club)) is None
