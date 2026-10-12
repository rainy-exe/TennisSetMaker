from app.permissions import Role


def test_court_crud_active_priority_and_permissions(client, set_actor):
    created = client.post(
        "/api/courts",
        json={"number": 3, "court_type": "synthetic grass", "priority": 2},
    )
    assert created.status_code == 201
    court = created.get_json()["court"]
    assert court["priority"] == 2

    set_actor(Role.STAFF)
    assert (
        client.post("/api/courts", json={"number": 4, "court_type": "hard"}).status_code
        == 403
    )
    assert client.get("/api/courts").status_code == 200

    set_actor(Role.ADMIN)
    updated = client.patch(f"/api/courts/{court['id']}", json={"active": False})
    assert updated.status_code == 200
    assert updated.get_json()["court"]["active"] is False


def test_template_persistence_and_validation(client, set_actor):
    court = client.post(
        "/api/courts", json={"number": 1, "court_type": "hard"}
    ).get_json()["court"]
    response = client.post(
        "/api/session-templates",
        json={
            "name": "Saturday Social",
            "number_of_rounds": 6,
            "round_duration_minutes": 25,
            "start_time": "13:00",
            "round_format": "mixed",
            "strict_format": False,
            "tea_break_after_round": 3,
            "court_ids": [court["id"]],
        },
    )
    assert response.status_code == 201
    assert response.get_json()["session_template"]["tea_break_after_round"] == 3
    assert response.get_json()["session_template"]["session_duration_minutes"] == 180
    assert response.get_json()["session_template"]["court_ids"] == [court["id"]]

    invalid = client.post(
        "/api/session-templates",
        json={"name": "Invalid", "number_of_rounds": 2, "tea_break_after_round": 2},
    )
    assert invalid.status_code == 400

    set_actor(Role.STAFF)
    assert (
        client.patch(
            f"/api/session-templates/{response.get_json()['session_template']['id']}",
            json={"number_of_rounds": 7},
        ).status_code
        == 403
    )


def test_session_can_use_template_default_courts(client, create_player):
    players = [create_player() for _ in range(4)]
    court = client.post(
        "/api/courts", json={"number": 7, "court_type": "soft", "priority": 1}
    ).get_json()["court"]
    template = client.post(
        "/api/session-templates",
        json={"name": "Winter Social", "court_ids": [court["id"]]},
    ).get_json()["session_template"]
    response = client.post(
        "/api/sessions",
        json={
            "name": "From template",
            "template_id": template["id"],
            "starts_at": "2026-09-12T13:00:00",
            "player_ids": [player["id"] for player in players],
        },
    )
    assert response.status_code == 201
    assert response.get_json()["session"]["court_ids"] == [court["id"]]


def test_session_rejects_inactive_court_and_invalid_configuration(
    client, create_player
):
    players = [create_player() for _ in range(4)]
    court_response = client.post(
        "/api/courts", json={"number": 1, "court_type": "hard"}
    )
    court = court_response.get_json()["court"]
    client.delete(f"/api/courts/{court['id']}")
    response = client.post(
        "/api/sessions",
        json={
            "name": "Bad session",
            "starts_at": "2026-09-12T13:00:00",
            "number_of_rounds": 0,
            "round_duration_minutes": 30,
            "court_ids": [court["id"]],
            "player_ids": [player["id"] for player in players],
        },
    )
    assert response.status_code == 400
    assert "active courts" in response.get_json()["message"]


def test_user_can_create_session_but_provisional_player_is_not_algorithm_eligible(
    client, create_player, set_actor, app
):
    players = [create_player() for _ in range(4)]
    set_actor(Role.STAFF)
    provisional = client.post(
        "/api/players", json={"name": "Needs rating", "gender": "F"}
    ).get_json()["player"]
    set_actor(Role.OWNER)
    court = client.post(
        "/api/courts", json={"number": 1, "court_type": "hard"}
    ).get_json()["court"]
    set_actor(Role.STAFF)
    session = client.post(
        "/api/sessions",
        json={
            "name": "STAFF session",
            "starts_at": "2026-09-12T13:00:00",
            "number_of_rounds": 2,
            "round_duration_minutes": 30,
            "court_ids": [court["id"]],
            "player_ids": [player["id"] for player in players] + [provisional["id"]],
        },
    ).get_json()["session"]
    generated = client.post(f"/api/sessions/{session['id']}/rounds/generate")
    assert generated.status_code == 201
    eligibility = generated.get_json()["round"]["eligibility"]
    assert {
        item["status"] for item in eligibility if item["player_id"] == provisional["id"]
    } == {"missing_skill"}
    assert len(app.fake_algorithm.calls[-1]["players"]) == 4


def test_draft_session_configuration_can_change_but_locks_after_generation(
    client, session_setup
):
    session, _, _ = session_setup(player_count=8, court_count=2, rounds=3)
    changed = client.patch(
        f"/api/sessions/{session['id']}",
        json={
            "number_of_rounds": 6,
            "session_duration_minutes": 210,
            "tea_break_after_round": 3,
            "court_ids": session["court_ids"],
            "player_ids": [item["player_id"] for item in session["participants"]],
        },
    )
    assert changed.status_code == 200
    assert changed.get_json()["session"]["session_duration_minutes"] == 210
    assert (
        client.post(f"/api/sessions/{session['id']}/rounds/generate").status_code == 201
    )
    locked = client.patch(
        f"/api/sessions/{session['id']}", json={"number_of_rounds": 7}
    )
    assert locked.status_code == 409


def test_invalid_draft_edit_rolls_back_all_pending_changes(client, session_setup):
    session, _, _ = session_setup(player_count=8, court_count=2, rounds=3)
    invalid = client.patch(
        f"/api/sessions/{session['id']}",
        json={"name": "Should not persist", "tea_break_after_round": 3},
    )
    assert invalid.status_code == 400
    reloaded = client.get(f"/api/sessions/{session['id']}").get_json()["session"]
    assert reloaded["name"] == "Saturday Social"
    assert reloaded["tea_break_after_round"] is None


def test_strict_mixed_format_rejects_a_non_mixed_result(client, create_player):
    players = [
        create_player(name, "M", 5) for name in ("Adam", "Ben", "Chris", "Dylan")
    ]
    court = client.post(
        "/api/courts", json={"number": 1, "court_type": "hard"}
    ).get_json()["court"]
    session = client.post(
        "/api/sessions",
        json={
            "name": "Strict mixed",
            "starts_at": "2026-09-12T13:00:00",
            "number_of_rounds": 1,
            "round_duration_minutes": 30,
            "round_format": "mixed",
            "strict_format": True,
            "court_ids": [court["id"]],
            "player_ids": [player["id"] for player in players],
        },
    ).get_json()["session"]
    response = client.post(f"/api/sessions/{session['id']}/rounds/generate")
    assert response.status_code == 422
    assert response.get_json()["error"] == "no_valid_solution"


def test_pairing_mode_flows_from_template_to_session_to_algorithm(client, create_player, app):
    players = [create_player() for _ in range(4)]
    court = client.post(
        "/api/courts", json={"number": 3, "court_type": "hard"}
    ).get_json()["court"]
    assert client.post(
        "/api/session-templates", json={"name": "Bad mode", "variety_mode": 4}
    ).status_code == 400
    template = client.post(
        "/api/session-templates",
        json={"name": "Round robin night", "variety_mode": 3, "court_ids": [court["id"]]},
    ).get_json()["session_template"]
    assert template["variety_mode"] == 3

    payload = {
        "template_id": template["id"],
        "starts_at": "2026-09-12T13:00:00",
        "player_ids": [player["id"] for player in players],
    }
    inherited = client.post("/api/sessions", json={**payload, "name": "Inherits"}).get_json()["session"]
    assert inherited["variety_mode"] == 3
    chosen = client.post(
        "/api/sessions", json={**payload, "name": "Overrides", "variety_mode": 1}
    ).get_json()["session"]
    assert chosen["variety_mode"] == 1

    assert client.post(f"/api/sessions/{chosen['id']}/rounds/generate").status_code == 201
    assert app.fake_algorithm.calls[-1]["variety_mode"] == 1

    plain = client.post(
        "/api/sessions",
        json={"name": "Default", "starts_at": "2026-09-12T13:00:00", "court_ids": [court["id"]],
              "number_of_rounds": 2, "round_duration_minutes": 30,
              "player_ids": [player["id"] for player in players]},
    ).get_json()["session"]
    assert plain["variety_mode"] == 2  # balanced skill by default
