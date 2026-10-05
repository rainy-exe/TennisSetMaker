import importlib.util
import inspect
import random
import sys
from dataclasses import dataclass, field
from pathlib import Path
from threading import Lock

from flask import current_app

from app.errors import ApiError

_load_lock = Lock()


@dataclass
class AlgorithmPlayer:
    id: str
    gender: str
    rating: int
    sit_out_count: int = 0
    last_sit_out_round: int = -1
    partner_ids: set = field(default_factory=set)
    opponent_ids: set = field(default_factory=set)
    consecutive_sit_outs: int = 0
    rounds_played: int = 0
    session_sit_outs: int = 0
    recent_sit_outs: int | None = None
    recent_rounds: int | None = None


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
    """Adapter for Prototype3 and the optional Prototype4 history fields.

    Stable database IDs are explicitly supplied as ``player_id``. The
    prototype's display name is also an ID; client names never cross this
    boundary. Bare prototype imports are scoped to loading, not left in the
    process-wide module namespace.
    """

    def __init__(self, algorithm_path, seed=None):
        self.algorithm_path = Path(algorithm_path) if algorithm_path else None
        self.seed = seed
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
        if any(
            not (path / f"{name}.py").is_file()
            for name in ("helpers", "refinement", "algorithm")
        ):
            raise ApiError(
                "The matching algorithm files could not be loaded.",
                503,
                "algorithm_unavailable",
            )

        # Both algorithm and refinement import helpers by a bare module name.
        # Restore all aliases even on failure, and serialize adapter loads.
        with _load_lock:
            names = ("helpers", "refinement", "algorithm")
            previous = {name: sys.modules.get(name) for name in names}
            loaded = {}
            try:
                for name in names:
                    spec = importlib.util.spec_from_file_location(
                        name, path / f"{name}.py"
                    )
                    module = importlib.util.module_from_spec(spec)
                    sys.modules[name] = module
                    spec.loader.exec_module(module)
                    loaded[name] = module
            except (ImportError, OSError) as error:
                raise ApiError(
                    "The matching algorithm could not be loaded.",
                    503,
                    "algorithm_unavailable",
                ) from error
            finally:
                for name, module in previous.items():
                    if module is None:
                        sys.modules.pop(name, None)
                    else:
                        sys.modules[name] = module
            self._helpers = loaded["helpers"]
            self._algorithm = loaded["algorithm"]

    def generate(self, players, court_ids, round_number, mixed_mode, strict_format):
        self._load()
        temporary = []
        player_fields = inspect.signature(self._helpers.Player).parameters
        for source in players:
            # Prototype3 does not accept these fields. Keep its constructor
            # working while supplying actual facts to compatible Prototype4.
            recent_history = {
                name: getattr(source, name)
                for name in ("session_sit_outs", "recent_sit_outs", "recent_rounds")
                if name in player_fields
            }
            player = self._helpers.Player(
                name=source.id,
                player_id=source.id,
                gender=source.gender,
                rating=source.rating,
                sit_out_count=source.sit_out_count,
                last_sit_out_round=source.last_sit_out_round,
                partners_played=set(source.partner_ids),
                opponents_played=set(source.opponent_ids),
                consecutive_sit_outs=source.consecutive_sit_outs,
                sessions_played=source.rounds_played,
                **recent_history,
            )
            temporary.append(player)

        capped = min(len(temporary), len(court_ids) * 4)
        num_to_play = (capped // 4) * 4
        playing, sitting = self._algorithm.select_round_players(
            temporary, num_to_play, rng=random.Random(self.seed)
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
            team_a = [player.player_id for player in result["team_a"]]
            team_b = [player.player_id for player in result["team_b"]]
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

        return AlgorithmRound(assignments, [player.player_id for player in sitting])


def get_algorithm_engine():
    configured = current_app.config.get("ALGORITHM_ENGINE")
    if configured is not None:
        return configured
    return PrototypeAlgorithmEngine(
        current_app.config.get("ALGORITHM_PATH"),
        seed=current_app.config.get("ALGORITHM_RANDOM_SEED"),
    )
