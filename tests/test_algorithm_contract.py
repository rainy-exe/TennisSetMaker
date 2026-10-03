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
