from copy import deepcopy

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from werkzeug.security import check_password_hash

from app import create_app
from app.extensions import db
from app.models import Club, Invitation, Round, User


def test_management_pages_use_real_club_data_and_stable_ids(real_app):
    owner = real_app.test_client()
    register(owner)
    for path in ("/players", "/courts", "/templates", "/accounts", "/sessions/create"):
        response = owner.get(path)
        assert response.status_code == 200, (path, response.data)
        assert response.headers["Cache-Control"] == "no-store"
    assert owner.get("/frontend/styles.css").status_code == 200
    assert owner.get("/frontend/../app/config.py").status_code == 404
    player = write(
        owner,
        "POST",
        "/api/players",
        {
            "name": '<script>alert("synthetic")</script>',
            "gender": "F",
            "skill": 10,
        },
    ).get_json()["player"]
    court = write(
        owner,
        "POST",
        "/api/courts",
        {
            "number": 8,
            "court_type": "Synthetic hard",
            "priority": 2,
        },
    ).get_json()["court"]
    template = write(
        owner,
        "POST",
        "/api/session-templates",
        {
            "name": "Synthetic template",
            "court_ids": [court["id"]],
        },
    ).get_json()["session_template"]
    roster = owner.get("/players").get_data(as_text=True)
    assert player["id"] in roster
    assert '<script>alert("synthetic")</script>' not in roster
    assert "&lt;script&gt;" in roster
    for path in (
        "/players/add",
        f"/players/{player['id']}/edit",
        "/courts/add",
        f"/courts/{court['id']}/edit",
        "/templates/add",
        f"/templates/{template['id']}/edit",
        f"/sessions/create?template={template['id']}",
    ):
        assert owner.get(path).status_code == 200, path
    assert "/api/players" in owner.get("/players/add").get_data(as_text=True)
    assert 'max="50"' in owner.get("/templates/add").get_data(as_text=True)
    other = real_app.test_client()
    register(other, "other@example.invalid", "Other synthetic club")
    for path in (
        f"/players?selected={player['id']}",
        f"/players/{player['id']}/edit",
        f"/courts/{court['id']}/edit",
        f"/templates/{template['id']}/edit",
        f"/sessions/create?template={template['id']}",
    ):
        assert other.get(path).status_code == 404, path
    assert "Synthetic template" not in other.get("/templates").get_data(as_text=True)


def test_management_pages_keep_kate_staff_permissions(real_app):
    owner, staff = real_app.test_client(), real_app.test_client()
    register(owner)
    invite(owner, staff, "staff", "staff@example.invalid")
    for path in (
        "/players",
        "/players/add",
        "/courts",
        "/templates",
        "/sessions/create",
    ):
        assert staff.get(path).status_code == 200, path
    for path in ("/courts/add", "/templates/add", "/accounts"):
        assert staff.get(path).status_code == 403, path
    form = staff.get("/players/add").get_data(as_text=True)
    assert 'name="skill" data-number disabled' in form
    assert "Owner or Admin must assign skill" in form
    assert "Deactivate Player" not in staff.get("/players").get_data(as_text=True)
    anonymous = real_app.test_client()
    for path in ("/players", "/courts", "/templates", "/accounts", "/sessions/create"):
        response = anonymous.get(path)
        assert response.status_code == 302
        assert response.headers["Location"].endswith("/login-page")


def test_session_page_reload_uses_saved_final_round_and_lock(real_app):
    client = real_app.test_client()
    register(client)
    players = []
    for name, gender in [
        ("Aster", "F"),
        ("Birch", "M"),
        ("Cedar", "F"),
        ("Dogwood", "M"),
        ("Elm", "F"),
    ]:
        response = write(
            client, "POST", "/api/players", {"name": name, "gender": gender, "skill": 5}
        )
        assert response.status_code == 201
        players.append(response.get_json()["player"])
    court = write(
        client, "POST", "/api/courts", {"number": 1, "court_type": "hard"}
    ).get_json()["court"]
    session = write(
        client,
        "POST",
        "/api/sessions",
        {
            "name": "Synthetic Social",
            "starts_at": "2026-10-03T13:00:00",
            "number_of_rounds": 2,
            "round_duration_minutes": 20,
            "court_ids": [court["id"]],
            "player_ids": [player["id"] for player in players],
        },
    ).get_json()["session"]
    page_path = f"/sessions/{session['id']}"
    assert client.get(page_path).status_code == 200
    round_ = write(
        client, "POST", f"/api/sessions/{session['id']}/rounds/generate"
    ).get_json()["round"]
    final = deepcopy(round_["final"])
    former_playing = final["assignments"][0]["team_a_player_ids"][0]
    former_sitting = final["sit_out_player_ids"][0]
    final["assignments"][0]["team_a_player_ids"][0] = former_sitting
    final["sit_out_player_ids"][0] = former_playing
    assert (
        write(client, "PUT", f"/api/rounds/{round_['id']}/final", final).status_code
        == 200
    )
    page = client.get(page_path).get_data(as_text=True)
    assert "override revision 1" in page
    assert f'value="{former_sitting}" selected' in page
    assert f'data-sit-out value="{former_playing}" checked' in page
    assert "Original generated result (audit)" in page
    assert "Save Final Round" in page
    assert (
        write(client, "POST", f"/api/rounds/{round_['id']}/complete").status_code == 200
    )
    page = client.get(page_path).get_data(as_text=True)
    assert "Save Final Round" not in page
    assert "Reopen Round 1" in page
    assert "Generate Next Round" in page
    other = real_app.test_client()
    register(other, "different@example.invalid")
    assert other.get(page_path).status_code == 404


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


def test_account_pages_and_no_fake_reset_claim(real_app):
    client = real_app.test_client()
    for path in (
        "/login-page",
        "/signup-page",
        "/forgot-password",
        "/invitation-page?token=synthetic",
    ):
        assert client.get(path).status_code == 200
    assert client.get("/dashboard").status_code == 302
    register(client)
    assert client.get("/dashboard").status_code == 200
    response = write(
        client, "POST", "/forgot-password", {"email": "owner@example.invalid"}
    )
    assert response.status_code == 503
    assert b"No reset email has been sent" in response.data
