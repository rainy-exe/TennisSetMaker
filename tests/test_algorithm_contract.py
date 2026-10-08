import os
import sys
from copy import deepcopy
from pathlib import Path

import pytest

from app.algorithm_adapter import AlgorithmPlayer, PrototypeAlgorithmEngine


def test_origin_main_prototype3_contract_when_available():
    path = os.environ.get("TENNIS_ALGORITHM_PATH") or str(
        Path(__file__).resolve().parents[1] / "Backend" / "Set_Algorithm" / "Prototype3"
    )
    assert Path(path).is_dir(), "Prototype3 must be present for the integration test"

    players = [
        AlgorithmPlayer(
            id=f"stable-{index}",
            gender="M" if index % 2 else "F",
            rating=(index % 10) + 1,
        )
        for index in range(1, 10)
    ]
    players[0].partner_ids.add(players[1].id)
    players[0].opponent_ids.add(players[2].id)
    players[-1].sit_out_count = 2
    players[-1].consecutive_sit_outs = 1
    players[-1].session_sit_outs = 1
    players[-1].recent_sit_outs = 2
    players[-1].recent_rounds = 10
    before = deepcopy(players)
    aliases = {
        name: sys.modules.get(name) for name in ("helpers", "refinement", "algorithm")
    }

    result = PrototypeAlgorithmEngine(path, seed=17).generate(
        players,
        ["court-near", "court-far"],
        round_number=2,
        mixed_mode=True,
        strict_format=False,
    )

    returned = [
        player_id
        for assignment in result.assignments
        for player_id in assignment.team_a + assignment.team_b
    ] + result.sit_out_player_ids
    assert set(returned) == {player.id for player in players}
    assert len(returned) == len(set(returned))
    assert {assignment.court_id for assignment in result.assignments} <= {
        "court-near",
        "court-far",
    }
    assert players == before  # Prototype mutations never reach persisted input.
    assert aliases == {name: sys.modules.get(name) for name in aliases}


def test_missing_algorithm_fails_cleanly(tmp_path):
    from app.errors import ApiError

    with pytest.raises(ApiError) as error:
        PrototypeAlgorithmEngine(tmp_path).generate([], [], 1, False, False)
    assert error.value.code == "algorithm_unavailable"


@pytest.fixture
def prototype4_path():
    path = os.environ.get("TENNIS_PROTOTYPE4_PATH") or str(
        Path(__file__).resolve().parents[1] / "Backend" / "Set_Algorithm" / "Prototype4"
    )
    assert (Path(path) / "algorithm.py").is_file()
    return path


def test_prototype4_receives_actual_history_without_mutating_input(
    prototype4_path, monkeypatch
):
    engine = PrototypeAlgorithmEngine(prototype4_path, seed=17)
    engine._load()
    select_players = engine._algorithm.select_round_players
    captured = []

    def capture(players, num_to_play, rng):
        captured.extend(deepcopy(players))
        return select_players(players, num_to_play, rng)

    monkeypatch.setattr(engine._algorithm, "select_round_players", capture)
    players = [
        AlgorithmPlayer(
            id=f"stable-{index}",
            gender="M",
            rating=5,
            session_sit_outs=1,
            recent_sit_outs=7,
            recent_rounds=50,
        )
        for index in range(5)
    ]
    # A high historical total must not force the only player with no sit-out
    # this session to keep playing while somebody else sits a second time.
    players[0].session_sit_outs = 0
    players[0].sit_out_count = 100
    players[0].partner_ids = {"departed-player"}
    players[0].opponent_ids = {"another-departed-player"}
    before = deepcopy(players)
    result = engine.generate(players, ["stable-court"], 5, False, False)
    assert result.sit_out_player_ids == [players[0].id]
    assert captured[0].player_id == players[0].id
    assert captured[0].session_sit_outs == 0
    assert captured[0].recent_sit_outs == 7
    assert captured[0].recent_rounds == 50
    assert captured[0].partners_played == {"departed-player"}
    assert captured[0].opponents_played == {"another-departed-player"}
    assert players == before


def test_prototype4_full_session_uses_overridden_sitout_history(
    prototype4_path,
    app,
    client,
    session_setup,
):
    app.config["ALGORITHM_ENGINE"] = PrototypeAlgorithmEngine(prototype4_path, seed=17)
    session, players, _ = session_setup(player_count=5, court_count=1, rounds=6)
    seen = set()
    previous = set()
    for number in range(1, 7):
        response = client.post(f"/api/sessions/{session['id']}/rounds/generate")
        assert response.status_code == 201, response.get_json()
        round_ = response.get_json()["round"]
        if number == 1:
            original = deepcopy(round_["generated"])
            final = deepcopy(round_["final"])
            old_sitout = final["sit_out_player_ids"][0]
            replacement = final["assignments"][0]["team_a_player_ids"][0]
            final["assignments"][0]["team_a_player_ids"][0] = old_sitout
            final["sit_out_player_ids"] = [replacement]
            response = client.put(f"/api/rounds/{round_['id']}/final", json=final)
            assert response.status_code == 200
            round_ = client.get(f"/api/rounds/{round_['id']}").get_json()["round"]
            assert round_["generated"] == original
            assert round_["final"] == final
        sitting = set(round_["final"]["sit_out_player_ids"])
        assert not (previous & sitting), "Avoidable back-to-back sit-out"
        previous = sitting
        if number <= 5:
            assert not (seen & sitting), "A player sat twice before everyone sat once"
        seen |= sitting
        assert client.post(f"/api/rounds/{round_['id']}/complete").status_code == 200
    assert seen == {player["id"] for player in players}
    assert (
        client.get(f"/api/sessions/{session['id']}").get_json()["session"]["status"]
        == "completed"
    )
