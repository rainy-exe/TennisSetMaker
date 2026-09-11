import os

import pytest

from app.algorithm_adapter import AlgorithmPlayer, PrototypeAlgorithmEngine


def test_origin_main_prototype3_contract_when_available():
    path = os.environ.get("TENNIS_ALGORITHM_PATH")
    if not path:
        pytest.skip("Set TENNIS_ALGORITHM_PATH to an origin/main Prototype3 export")

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

    result = PrototypeAlgorithmEngine(path).generate(
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
