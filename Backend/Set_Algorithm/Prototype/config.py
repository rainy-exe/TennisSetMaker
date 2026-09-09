"""
config.py

Collects the static, per-run inputs for the TennisSetMaker prototype.
Nothing about scoring/priority logic lives here — only the knobs that
control a given run (court/player/round counts, feature toggles, seed).

Kept deliberately separate from algorithm.py and helpers.py so that
changing how inputs are gathered (CLI prompts now, a web form later)
never touches the algorithm itself.
"""

from dataclasses import dataclass
from typing import Optional


@dataclass
class RunConfig:
    csv_path: str
    num_players: int
    num_courts: int
    num_rounds: int
    mixed_mode: int          # 0 = ignore gender, 1 = enforce mixed pairing (with relaxation)
    variety_mode: int        # 1 = hard avoid repeats, 2 = soft penalty, 3 = round-robin bias
    seed: Optional[int]      # None = generate a fresh random seed and report it


def _prompt_int(label: str, default: int = None, min_val: int = None, max_val: int = None) -> int:
    while True:
        suffix = f" [{default}]" if default is not None else ""
        raw = input(f"{label}{suffix}: ").strip()
        if raw == "" and default is not None:
            value = default
        else:
            try:
                value = int(raw)
            except ValueError:
                print("  Please enter a whole number.")
                continue
        if min_val is not None and value < min_val:
            print(f"  Must be at least {min_val}.")
            continue
        if max_val is not None and value > max_val:
            print(f"  Must be at most {max_val}.")
            continue
        return value


def _prompt_choice(label: str, choices: dict, default: int) -> int:
    """choices: {int_value: description}"""
    desc = ", ".join(f"{k}={v}" for k, v in choices.items())
    while True:
        raw = input(f"{label} ({desc}) [{default}]: ").strip()
        if raw == "":
            return default
        try:
            value = int(raw)
        except ValueError:
            print("  Please enter one of the listed numbers.")
            continue
        if value not in choices:
            print("  Please enter one of the listed numbers.")
            continue
        return value


def gather_config_interactive(csv_path: str = "NTC_ratings_data.csv") -> RunConfig:
    """Prompts the user for all run inputs. Swap this out freely without
    touching the algorithm — RunConfig is the only contract that matters."""

    print("=== TennisSetMaker Prototype — Run Configuration ===\n")

    num_players = _prompt_int("Number of players to pull from dataset", default=20, min_val=4)
    num_courts = _prompt_int("Number of active courts", default=5, min_val=1)
    num_rounds = _prompt_int("Number of rounds", default=4, min_val=1)

    mixed_mode = _prompt_choice(
        "Mixed mode", {0: "open/any gender", 1: "enforce mixed pairing"}, default=0
    )
    variety_mode = _prompt_choice(
        "Variety mode",
        {1: "hard avoid repeats", 2: "soft penalty for repeats", 3: "round-robin bias"},
        default=2,
    )

    seed_raw = input("Random seed (blank = generate new random seed): ").strip()
    seed = int(seed_raw) if seed_raw != "" else None

    print()
    return RunConfig(
        csv_path=csv_path,
        num_players=num_players,
        num_courts=num_courts,
        num_rounds=num_rounds,
        mixed_mode=mixed_mode,
        variety_mode=variety_mode,
        seed=seed,
    )
