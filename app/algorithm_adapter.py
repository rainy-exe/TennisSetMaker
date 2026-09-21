import importlib.util
import sys
from dataclasses import dataclass, field
from pathlib import Path

from flask import current_app

from app.errors import ApiError


@dataclass
class AlgorithmPlayer:
    id: str
    gender: str
    rating: int
    sit_out_count: int = 0
    last_sit_out_round: int = -1
    partner_ids: set = field(default_factory=set)
    opponent_ids: set = field(default_factory=set)


@dataclass
class CourtAssignment:
    court_id: str
    team_a: list
    team_b: list


@dataclass
class AlgorithmRound:
    assignments: list
    sit_out_player_ids: list


class PrototypeAlgorithmEngine:
    """Adapter for the current Prototype3 contract on origin/main.

    Stable database IDs are placed in the prototype's ``name`` field because
    that field is also its history identity. Display names never cross this
    boundary, so two real players with the same name remain distinct.
    """

    def __init__(self, algorithm_path):
        self.algorithm_path = Path(algorithm_path) if algorithm_path else None
        self._helpers = None
        self._algorithm = None

    def _load(self):
        if self._algorithm is not None:
            return
        if self.algorithm_path is None or not self.algorithm_path.is_dir():
            raise ApiError(
                "The matching algorithm is not available in this checkout.",
                503,
                "algorithm_unavailable",
                {"dependency": "origin/main:Backend/Set_Algorithm/Prototype3"},
            )

        path = self.algorithm_path.resolve()
        suffix = str(abs(hash(str(path))))
        helper_name = f"_tennissetmaker_prototype_helpers_{suffix}"
        algorithm_name = f"_tennissetmaker_prototype_algorithm_{suffix}"
        helper_spec = importlib.util.spec_from_file_location(
            helper_name, path / "helpers.py"
        )
        algorithm_spec = importlib.util.spec_from_file_location(
            algorithm_name, path / "algorithm.py"
        )
        if helper_spec is None or algorithm_spec is None:
            raise ApiError(
                "The matching algorithm files could not be loaded.",
                503,
                "algorithm_unavailable",
            )

        helpers = importlib.util.module_from_spec(helper_spec)
        algorithm = importlib.util.module_from_spec(algorithm_spec)
        sys.modules[helper_name] = helpers
        sys.modules[algorithm_name] = algorithm
        previous_helpers = sys.modules.get("helpers")
        try:
            helper_spec.loader.exec_module(helpers)
            # Prototype3 uses a bare ``from helpers import Player`` import.
            sys.modules["helpers"] = helpers
            algorithm_spec.loader.exec_module(algorithm)
        finally:
            if previous_helpers is None:
                sys.modules.pop("helpers", None)
            else:
                sys.modules["helpers"] = previous_helpers
        self._helpers = helpers
        self._algorithm = algorithm

    def generate(self, players, court_ids, round_number, mixed_mode, strict_format):
        self._load()
        temporary = []
        for source in players:
            player = self._helpers.Player(
                name=source.id,
                gender=source.gender,
                rating=source.rating,
                sit_out_count=source.sit_out_count,
                last_sit_out_round=source.last_sit_out_round,
                partners_played=set(source.partner_ids),
                opponents_played=set(source.opponent_ids),
            )
            temporary.append(player)

        capped = min(len(temporary), len(court_ids) * 4)
        num_to_play = (capped // 4) * 4
        playing, sitting = self._algorithm.select_round_players(
            temporary, num_to_play, round_num=round_number
        )
        generated = self._algorithm.assign_courts_for_round(
            playing,
            len(court_ids),
            1 if mixed_mode else 0,
            2,
        )

        assignments = []
        for result in generated:
            court_id = court_ids[result["court"] - 1]
            team_a = [player.name for player in result["team_a"]]
            team_b = [player.name for player in result["team_b"]]
            if mixed_mode and strict_format:
                by_id = {player.id: player for player in players}
                for team in (team_a, team_b):
                    if len({by_id[player_id].gender for player_id in team}) != 2:
                        raise ApiError(
                            "No result satisfies the strict mixed-team format.",
                            422,
                            "no_valid_solution",
                        )
            assignments.append(CourtAssignment(court_id, team_a, team_b))

        return AlgorithmRound(assignments, [player.name for player in sitting])


def get_algorithm_engine():
    configured = current_app.config.get("ALGORITHM_ENGINE")
    if configured is not None:
        return configured
    return PrototypeAlgorithmEngine(current_app.config.get("ALGORITHM_PATH"))
