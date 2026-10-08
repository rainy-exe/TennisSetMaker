from copy import deepcopy

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from werkzeug.security import check_password_hash

from app import create_app
from app.extensions import db
from app.models import Club, Invitation, Round, User


@pytest.fixture
def real_app(tmp_path):
    application = create_app(
        {
            "TESTING": True,
            "SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp_path / 'accounts.db'}",
            "RECAPTCHA_SECRET_KEY": None,
            "ALGORITHM_RANDOM_SEED": 9,
        }
    )
    with application.app_context():
        assert db.session.execute(text("PRAGMA foreign_keys")).scalar() == 1
        db.create_all()
    yield application
    with application.app_context():
        db.session.remove()
        db.drop_all()


def write(client, method, path, payload=None):
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
            "email": email,
            "password": "Synthetic-password",
            "club_name": name,
        },
    )
    assert response.status_code == 201, response.get_json()
    return response.get_json()["user"]


def invite(owner, member, role, email):
    response = write(owner, "POST", "/api/invitations", {"role": role, "email": email})
    assert response.status_code == 201, response.get_json()
    payload = response.get_json()
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
    assert created["accept_endpoint"] == "/accept-invitation"
    assert "accept_path" not in created
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


def test_missing_account_frontend_is_explicit_and_no_fake_reset_claim(real_app):
    client = real_app.test_client()
    assert client.get("/").status_code == 200
    for path in ("/login-page", "/signup-page"):
        response = client.get(path)
        assert response.status_code == 503
        assert response.get_json()["error"] == "frontend_unavailable"
    for path in ("/dashboard", "/players", "/courts", "/templates", "/sessions/create"):
        assert client.get(path).status_code == 401
    for path in ("/accounts", "/invitation-page"):
        assert client.get(path).status_code == 404
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
