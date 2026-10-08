import json
import re
from copy import deepcopy
from pathlib import Path

from flask_migrate import upgrade

from app import create_app
from app.algorithm_adapter import get_algorithm_engine
from app.extensions import db
from app.models import Player
from app.permissions import Role


def page_data(response):
    assert response.status_code == 200, response.data
    encoded = re.search(
        rb'<script id="setmaker-page-data" type="application/json">(.*?)</script>',
        response.data,
        re.DOTALL,
    )
    assert encoded
    return json.loads(encoded.group(1))


def test_existing_management_pages_use_real_club_scoped_data(
    client, session_setup, set_actor
):
    session, players, courts = session_setup(player_count=5, court_count=1)
    roster = page_data(client.get("/players"))
    assert {player["id"] for player in roster["players"]} == {
        player["id"] for player in players
    }
    assert (
        page_data(client.get(f"/players/{players[0]['id']}/edit"))["player"]["id"]
        == players[0]["id"]
    )
    assert page_data(client.get("/courts"))["courts"][0]["id"] == courts[0]["id"]
    assert (
        page_data(client.get(f"/sessions/{session['id']}"))["session"]["id"]
        == session["id"]
    )
    for url in (
        "/dashboard",
        "/sessions/create",
        "/players/add",
        "/courts/add",
        "/templates",
        "/templates/add",
    ):
        assert "integration.js" in client.get(url).text
    assert client.post("/sessions/create").status_code == 405
    assert client.get("/sessions/active").status_code == 400
    set_actor(club_id="another-club")
    assert page_data(client.get("/players"))["players"] == []
    for url in (
        f"/players/{players[0]['id']}/edit",
        f"/courts/{courts[0]['id']}/edit",
        f"/sessions/{session['id']}",
    ):
        assert client.get(url).status_code == 404


def test_page_permissions_and_delete_preserve_history(
    app, client, session_setup, set_actor
):
    session, players, _ = session_setup(player_count=5, court_count=1)
    first = client.post(f"/api/sessions/{session['id']}/rounds/generate").get_json()[
        "round"
    ]
    assert client.post(f"/api/rounds/{first['id']}/complete").status_code == 200
    set_actor(Role.STAFF)
    assert page_data(client.get("/players"))["can_manage_skill"] is False
    assert "delete-form" not in client.get("/players").text.split("<script>")[0]
    assert client.post(f"/players/{players[0]['id']}/delete").status_code == 403
    for url in ("/courts/add", "/templates/add"):
        assert client.get(url).status_code == 403
    set_actor(Role.ADMIN)
    assert client.post(f"/players/{players[0]['id']}/delete").status_code == 302
    with app.app_context():
        assert db.session.get(Player, players[0]["id"]).active is False
    assert (
        client.get(f"/api/rounds/{first['id']}").get_json()["round"]["final"]
        == first["final"]
    )


def test_new_details_validate_persist_and_do_not_accept_partial_write(
    client, create_player
):
    player = create_player(skill=10)
    update = {"notes": "Synthetic note", "court_preference": "Hard"}
    assert client.patch(f"/api/players/{player['id']}", json=update).status_code == 200
    reloaded = client.get(f"/api/players/{player['id']}").get_json()["player"]
    assert all(reloaded[key] == value for key, value in update.items())
    assert (
        client.patch(
            f"/api/players/{player['id']}",
            json={"notes": "Must roll back", "court_preference": "invalid"},
        ).status_code
        == 400
    )
    assert (
        client.get(f"/api/players/{player['id']}").get_json()["player"]["notes"]
        == update["notes"]
    )
    response = client.post(
        "/api/courts", json={"number": 10, "court_type": "Hard", "cover": "Shade"}
    )
    assert response.status_code == 201
    court = response.get_json()["court"]
    assert court["cover"] == "Shade"
    assert (
        client.patch(f"/api/courts/{court['id']}", json={"cover": ""}).get_json()[
            "court"
        ]["cover"]
        is None
    )


def test_stale_override_does_not_overwrite_saved_result(client, session_setup):
    session, _, _ = session_setup(player_count=5, court_count=1)
    round_ = client.post(f"/api/sessions/{session['id']}/rounds/generate").get_json()[
        "round"
    ]
    payload = deepcopy(round_["final"])
    payload["expected_revision"] = 0
    assert (
        client.put(f"/api/rounds/{round_['id']}/final", json=payload).status_code == 200
    )
    response = client.put(f"/api/rounds/{round_['id']}/final", json=payload)
    assert response.status_code == 409
    assert response.get_json()["error"] == "stale_round"
    assert (
        client.get(f"/api/rounds/{round_['id']}").get_json()["round"][
            "override_revision"
        ]
        == 1
    )


def test_migrated_real_login_pages_and_default_prototype4_flow(tmp_path):
    application = create_app(
        {
            "TESTING": True,
            "SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp_path / 'integrated.db'}",
            "RECAPTCHA_SECRET_KEY": None,
            "ALGORITHM_RANDOM_SEED": 17,
        }
    )
    with application.app_context():
        upgrade(directory=str(Path(__file__).resolve().parents[1] / "migrations"))
        engine = get_algorithm_engine()
        assert engine.algorithm_path.name == "Prototype4"
        engine._load()
        assert "recent_rounds" in engine._helpers.Player.__dataclass_fields__
    client = application.test_client()

    def write(method, path, payload=None):
        token = client.get("/csrf-token").get_json()["csrf_token"]
        return client.open(
            path, method=method, json=payload, headers={"X-CSRFToken": token}
        )

    assert (
        write(
            "POST",
            "/register",
            {
                "email": "integrated@example.invalid",
                "password": "Synthetic-only",
                "club_name": "Synthetic Integration",
            },
        ).status_code
        == 201
    )
    assert client.post("/api/players", json={"name": "Missing CSRF"}).status_code == 400
    assert write("POST", "/logout").status_code == 302
    assert (
        write(
            "POST",
            "/login",
            {"email": "integrated@example.invalid", "password": "Synthetic-only"},
        ).status_code
        == 200
    )
    players = [
        write(
            "POST",
            "/api/players",
            {"name": f"Synthetic {index}", "gender": "M", "skill": 5},
        ).get_json()["player"]
        for index in range(6)
    ]
    court = write(
        "POST", "/api/courts", {"number": 1, "court_type": "Hard"}
    ).get_json()["court"]
    response = write(
        "POST",
        "/api/sessions",
        {
            "name": "Integrated Social",
            "starts_at": "2026-10-09T13:00:00",
            "number_of_rounds": 3,
            "round_duration_minutes": 30,
            "player_ids": [p["id"] for p in players[:5]],
            "court_ids": [court["id"]],
        },
    )
    assert response.status_code == 201, response.get_json()
    session = response.get_json()["session"]
    generated = write(
        "POST", f"/api/sessions/{session['id']}/rounds/generate"
    ).get_json()["round"]
    final = deepcopy(generated["final"])
    original = final["sit_out_player_ids"][0]
    replacement = final["assignments"][0]["team_a_player_ids"][0]
    final["assignments"][0]["team_a_player_ids"][0] = original
    final["sit_out_player_ids"] = [replacement]
    assert (
        write("PUT", f"/api/rounds/{generated['id']}/final", final).status_code == 200
    )
    saved = page_data(client.get(f"/sessions/{session['id']}"))["session"]["rounds"][0]
    assert saved["generated"] == generated["generated"]
    assert saved["final"] == final
    assert write("POST", f"/api/rounds/{generated['id']}/complete").status_code == 200
    assert (
        write(
            "POST",
            f"/api/sessions/{session['id']}/players",
            {"player_id": players[5]["id"]},
        ).status_code
        == 201
    )
    assert (
        write(
            "PATCH",
            f"/api/sessions/{session['id']}/players/{players[4]['id']}",
            {"reason": "Leaving"},
        ).status_code
        == 200
    )
    next_round = write(
        "POST", f"/api/sessions/{session['id']}/rounds/generate"
    ).get_json()["round"]
    eligible = {
        e["player_id"] for e in next_round["eligibility"] if e["status"] == "eligible"
    }
    assert players[5]["id"] in eligible
    assert players[4]["id"] not in eligible
    assert replacement not in next_round["final"]["sit_out_player_ids"]
    assert (
        client.get(f"/sessions/{session['id']}").headers["Cache-Control"] == "no-store"
    )
    with application.app_context():
        db.session.remove()
        db.drop_all()
