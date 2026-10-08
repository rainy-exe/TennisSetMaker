from copy import deepcopy

import pytest
from sqlalchemy.exc import SQLAlchemyError

from app.extensions import db
from app.models import Round
from app.permissions import Role


def assignment_payload(round_payload):
    return {
        "assignments": deepcopy(round_payload["final"]["assignments"]),
        "sit_out_player_ids": list(round_payload["final"]["sit_out_player_ids"]),
    }


def test_odd_count_round_save_override_reload_and_final_history(
    app, client, session_setup, create_player
):
    session, _, _ = session_setup(player_count=9, court_count=2, rounds=3)
    first_response = client.post(f"/api/sessions/{session['id']}/rounds/generate")
    assert first_response.status_code == 201
    first = first_response.get_json()["round"]
    assert len(first["generated"]["assignments"]) == 2
    assert len(first["generated"]["sit_out_player_ids"]) == 1
    original_generated = deepcopy(first["generated"])

    payload = assignment_payload(first)
    old_sitout = payload["sit_out_player_ids"][0]
    replacement = payload["assignments"][0]["team_a_player_ids"][1]
    payload["assignments"][0]["team_a_player_ids"][1] = old_sitout
    payload["sit_out_player_ids"] = [replacement]
    # Also change the actual partnership on court 2.
    a = payload["assignments"][1]["team_a_player_ids"][1]
    b = payload["assignments"][1]["team_b_player_ids"][0]
    payload["assignments"][1]["team_a_player_ids"][1] = b
    payload["assignments"][1]["team_b_player_ids"][0] = a

    overridden = client.put(f"/api/rounds/{first['id']}/final", json=payload)
    assert overridden.status_code == 200
    assert overridden.get_json()["round"]["generated"] == original_generated
    assert overridden.get_json()["round"]["final"] == payload

    reloaded = client.get(f"/api/rounds/{first['id']}").get_json()["round"]
    assert reloaded["final"] == payload
    assert client.post(f"/api/rounds/{first['id']}/complete").status_code == 200

    second = client.post(f"/api/sessions/{session['id']}/rounds/generate")
    assert second.status_code == 201
    history = {player.id: player for player in app.fake_algorithm.calls[-1]["players"]}
    assert history[replacement].sit_out_count == 1
    assert history[replacement].session_sit_outs == 1
    assert history[replacement].recent_sit_outs == 1
    assert history[replacement].recent_rounds == 1
    assert history[old_sitout].sit_out_count == 0
    assert history[old_sitout].session_sit_outs == 0
    assert history[old_sitout].recent_sit_outs == 0
    assert history[old_sitout].recent_rounds == 1
    first_team = payload["assignments"][1]["team_a_player_ids"]
    assert first_team[1] in history[first_team[0]].partner_ids


def test_late_arrival_and_unavailable_change_only_future_pool(
    app, client, session_setup, create_player
):
    session, players, _ = session_setup(player_count=8, court_count=2, rounds=3)
    first = client.post(f"/api/sessions/{session['id']}/rounds/generate").get_json()[
        "round"
    ]
    client.post(f"/api/rounds/{first['id']}/complete")

    late_player = create_player("Late Player", "F", 5)
    joined = client.post(
        f"/api/sessions/{session['id']}/players", json={"player_id": late_player["id"]}
    )
    assert joined.status_code == 201
    unavailable = client.patch(
        f"/api/sessions/{session['id']}/players/{players[0]['id']}",
        json={"reason": "Left early"},
    )
    assert unavailable.status_code == 200

    second = client.post(f"/api/sessions/{session['id']}/rounds/generate")
    assert second.status_code == 201
    input_ids = {player.id for player in app.fake_algorithm.calls[-1]["players"]}
    assert late_player["id"] in input_ids
    assert players[0]["id"] not in input_ids
    history = {player.id: player for player in app.fake_algorithm.calls[-1]["players"]}
    assert history[late_player["id"]].session_sit_outs == 0
    assert history[late_player["id"]].recent_rounds == 0
    statuses = {
        item["player_id"]: item["status"]
        for item in second.get_json()["round"]["eligibility"]
    }
    assert statuses[players[0]["id"]] == "unavailable"
    assert (
        players[0]["id"]
        not in second.get_json()["round"]["final"]["sit_out_player_ids"]
    )


def test_invalid_override_is_atomic_and_never_assigns_player_twice(
    client, session_setup
):
    session, _, _ = session_setup(player_count=8, court_count=2)
    generated = client.post(
        f"/api/sessions/{session['id']}/rounds/generate"
    ).get_json()["round"]
    before = deepcopy(generated["final"])
    invalid = assignment_payload(generated)
    duplicate = invalid["assignments"][0]["team_a_player_ids"][0]
    invalid["assignments"][0]["team_a_player_ids"][1] = duplicate

    response = client.put(f"/api/rounds/{generated['id']}/final", json=invalid)
    assert response.status_code == 400
    after = client.get(f"/api/rounds/{generated['id']}").get_json()["round"]
    assert after["final"] == before
    assert after["override_revision"] == 0


def test_algorithm_invalid_result_rolls_back_whole_round(app, client, session_setup):
    session, _, _ = session_setup(player_count=8, court_count=2)
    app.fake_algorithm.invalid_result = True
    response = client.post(f"/api/sessions/{session['id']}/rounds/generate")
    assert response.status_code == 502
    with app.app_context():
        assert db.session.query(Round).count() == 0


def test_database_failure_rolls_back_override(app, client, session_setup, monkeypatch):
    session, _, _ = session_setup(player_count=8, court_count=2)
    generated = client.post(
        f"/api/sessions/{session['id']}/rounds/generate"
    ).get_json()["round"]
    before = deepcopy(generated["final"])
    changed = assignment_payload(generated)
    first = changed["assignments"][0]
    first["team_a_player_ids"][1], first["team_b_player_ids"][0] = (
        first["team_b_player_ids"][0],
        first["team_a_player_ids"][1],
    )

    def fail_commit():
        raise SQLAlchemyError("synthetic write failure")

    with app.app_context():
        monkeypatch.setattr(db.session, "commit", fail_commit)
        response = client.put(f"/api/rounds/{generated['id']}/final", json=changed)
        assert response.status_code == 500
        monkeypatch.undo()
    reloaded = client.get(f"/api/rounds/{generated['id']}").get_json()["round"]
    assert reloaded["final"] == before
    assert reloaded["override_revision"] == 0


def test_completed_round_lock_and_manager_only_reopen(client, session_setup, set_actor):
    session, _, _ = session_setup(player_count=8, court_count=2)
    generated = client.post(
        f"/api/sessions/{session['id']}/rounds/generate"
    ).get_json()["round"]
    client.post(f"/api/rounds/{generated['id']}/complete")
    payload = assignment_payload(generated)
    assert (
        client.put(f"/api/rounds/{generated['id']}/final", json=payload).status_code
        == 409
    )

    set_actor(Role.STAFF)
    assert client.post(f"/api/rounds/{generated['id']}/reopen").status_code == 403
    set_actor(Role.ADMIN)
    assert client.post(f"/api/rounds/{generated['id']}/reopen").status_code == 200
    assert (
        client.put(f"/api/rounds/{generated['id']}/final", json=payload).status_code
        == 200
    )


def test_inactive_court_is_not_sent_to_generation(app, client, session_setup):
    session, _, courts = session_setup(player_count=8, court_count=2)
    client.delete(f"/api/courts/{courts[0]['id']}")
    response = client.post(f"/api/sessions/{session['id']}/rounds/generate")
    assert response.status_code == 201
    assert app.fake_algorithm.calls[-1]["court_ids"] == [courts[1]["id"]]
    assert len(response.get_json()["round"]["final"]["sit_out_player_ids"]) == 4


def test_history_keeps_a_partner_who_left_before_the_next_round(
    app, client, session_setup
):
    session, _, _ = session_setup(player_count=8, court_count=2)
    first = client.post(f"/api/sessions/{session['id']}/rounds/generate").get_json()[
        "round"
    ]
    staying, leaving = first["final"]["assignments"][0]["team_a_player_ids"]
    client.post(f"/api/rounds/{first['id']}/complete")
    client.patch(
        f"/api/sessions/{session['id']}/players/{leaving}",
        json={"reason": "Left early"},
    )
    assert (
        client.post(f"/api/sessions/{session['id']}/rounds/generate").status_code == 201
    )
    history = {player.id: player for player in app.fake_algorithm.calls[-1]["players"]}
    assert leaving not in history
    assert leaving in history[staying].partner_ids


@pytest.mark.parametrize(
    "bad",
    [
        {"assignments": [None], "sit_out_player_ids": []},
        {"assignments": [{"court_id": {}}], "sit_out_player_ids": []},
        {"assignments": [], "sit_out_player_ids": [{}]},
    ],
)
def test_malformed_override_leaves_final_unchanged(client, session_setup, bad):
    session, _, _ = session_setup(player_count=8, court_count=2)
    first = client.post(f"/api/sessions/{session['id']}/rounds/generate").get_json()[
        "round"
    ]
    assert client.put(f"/api/rounds/{first['id']}/final", json=bad).status_code == 400
    assert (
        client.get(f"/api/rounds/{first['id']}").get_json()["round"]["final"]
        == first["final"]
    )
