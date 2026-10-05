"""
helpers.py

Data plumbing only: loading the CSV, pulling a random player subset,
seeding fake sit-out history for demo purposes, and writing output
(console + CSV + seed/state log).

Deliberately contains NO priority-weight or scoring logic — restructuring
any of this should never require touching algorithm.py.
"""

import csv
import glob
import json
import os
import random
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Dict, Optional


@dataclass
class Player:
    name: str
    gender: str
    rating: int
    player_id: Optional[str] = None  # stable identity for history tracking; defaults to name if not supplied
    sit_out_count: int = 0          # seeded fairness history for this run
    last_sit_out_round: int = -1    # round number they last sat out (-1 = never yet); telemetry only
    consecutive_sit_outs: int = 0   # rounds sat out in a row, ending at the most recent round; reset to 0 the instant they play
    partners_played: set = field(default_factory=set)   # player_ids of partners so far this run
    opponents_played: set = field(default_factory=set)  # player_ids of opponents so far this run
    sessions_played: int = 0        # rounds played (the backend passes its rounds_played here)
    # Prototype4 sit-out fairness inputs (all optional, so existing callers keep working):
    session_sit_outs: int = 0       # sit-outs so far in TODAY's session; the caller resets it each session
    recent_sit_outs: Optional[int] = None  # sit-outs within the player's last RATIO_WINDOW_ROUNDS rounds
    recent_rounds: Optional[int] = None    # rounds attended (played + sat out) within that same window

    def __post_init__(self):
        if self.player_id is None:
            # No caller-supplied identity (e.g. the CSV loader has no ID column) --
            # name is the best available fallback, matching every existing call site.
            # A real deployment (e.g. a backend with database row IDs) should pass a
            # real player_id explicitly to avoid collisions between same-named players.
            self.player_id = self.name
        if self.rating is None or isinstance(self.rating, bool) or not isinstance(self.rating, (int, float)):
            raise ValueError(
                f"Player {self.name!r} has an invalid rating: {self.rating!r} "
                f"(type {type(self.rating).__name__}). Ratings must be a number "
                "(int or float) -- provisional/unrated players must be filtered "
                "out before constructing a Player."
            )


def load_players_from_csv(csv_path: str) -> List[Dict]:
    """Reads the raw CSV into plain dicts: {name, gender, rating}."""
    rows = []
    with open(csv_path, newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            rows.append({
                "name": row["Name"].strip(),
                "gender": row["Gender"].strip().upper(),
                "rating": int(row["Rating"]),
            })
    return rows


def pull_random_players(all_rows: List[Dict], num_players: int, rng: random.Random) -> List[Player]:
    """Randomly selects num_players from the full dataset and wraps them
    as Player objects. Raises if the dataset is smaller than requested."""
    if num_players > len(all_rows):
        raise ValueError(
            f"Requested {num_players} players but dataset only has {len(all_rows)}."
        )
    chosen = rng.sample(all_rows, num_players)
    return [Player(name=r["name"], gender=r["gender"], rating=r["rating"]) for r in chosen]


def seed_sit_out_history(players: List[Player], rng: random.Random, max_prior: int = 4) -> None:
    """Prototype-only: gives each player a plausible fake sit-out count
    from past sessions (0..max_prior), so priority-1 (sit-out fairness)
    has a visible effect from round 1 onward. In the real system this
    would instead be pulled from actual stored history, starting at 0."""
    for p in players:
        p.sit_out_count = rng.randint(0, max_prior)


def write_seed_log(path: str, seed: int, config: dict, players: List[Player]) -> None:
    """Writes the exact seed + starting state to a JSON file so a demo
    run can be reproduced exactly by re-running with the same seed."""
    payload = {
        "seed": seed,
        "config": config,
        "starting_players": [
            {
                "name": p.name,
                "gender": p.gender,
                "rating": p.rating,
                "seeded_sit_out_count": p.sit_out_count,
            }
            for p in players
        ],
    }
    with open(path, "w") as f:
        json.dump(payload, f, indent=2)


def print_round_summary(round_num: int, court_results: List[dict], sit_outs: List[Player]) -> None:
    """court_results: the list of dicts returned by assign_courts_for_round
    (must use the algorithm's actual team_a/team_b split, not a recomputed
    one, or the printed sums won't match the real assignment)."""
    print(f"\n--- Round {round_num} ---")
    for r in court_results:
        team_a, team_b = r["team_a"], r["team_b"]
        a_names = " & ".join(f"{p.name}({p.rating})" for p in team_a)
        b_names = " & ".join(f"{p.name}({p.rating})" for p in team_b)
        a_sum, b_sum = r["team_a_sum"], r["team_b_sum"]
        print(f"  Court {r['court']}: [{a_names}] vs [{b_names}]   "
              f"[Team A={a_sum} vs Team B={b_sum}, diff={abs(a_sum-b_sum)}]")
    if sit_outs:
        print(f"  Sit-outs: {', '.join(p.name for p in sit_outs)}")
    else:
        print("  Sit-outs: none")


def write_results_csv(path: str, all_rounds: List[dict]) -> None:
    """all_rounds: list of {round, court, team_a: [Player x2], team_b: [Player x2],
    team_a_sum, team_b_sum}. Columns explicitly show the team split the
    algorithm chose, not just the four names."""
    with open(path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow([
            "Round", "Court",
            "TeamA_Player1", "TeamA_Player2", "TeamB_Player1", "TeamB_Player2",
            "TeamA_Sum", "TeamB_Sum", "RatingDiff",
        ])
        for entry in all_rounds:
            ta, tb = entry["team_a"], entry["team_b"]
            writer.writerow([
                entry["round"], entry["court"],
                ta[0].name, ta[1].name, tb[0].name, tb[1].name,
                entry["team_a_sum"], entry["team_b_sum"],
                abs(entry["team_a_sum"] - entry["team_b_sum"]),
            ])


def write_sit_out_csv(path: str, sit_out_log: List[dict]) -> None:
    """sit_out_log: list of {round, name, cumulative_sit_out_count}"""
    with open(path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["Round", "Player", "Cumulative_SitOut_Count"])
        for entry in sit_out_log:
            writer.writerow([entry["round"], entry["name"], entry["cumulative_sit_out_count"]])


# ---------------------------------------------------------------------------
# Cleanup
# ---------------------------------------------------------------------------

# Patterns for files this program generates each run. Deliberately does NOT
# match the source dataset (e.g. NTC_ratings_data.csv) or the .py files —
# only output the program itself wrote.
GENERATED_FILE_PATTERNS = [
    "results_*.csv",
    "sitouts_*.csv",
    "seed_*.json",
]


def find_generated_files(directory: str = ".") -> List[str]:
    """Returns paths of all previously generated result/sit-out/seed files
    in the given directory, matched by the same naming pattern main.py uses
    when writing them."""
    found = []
    for pattern in GENERATED_FILE_PATTERNS:
        found.extend(glob.glob(os.path.join(directory, pattern)))
    return sorted(found)


def cleanup_generated_files(directory: str = ".", confirm: bool = True) -> List[str]:
    """Deletes all previously generated result/sit-out/seed files.

    If confirm=True, lists the files and asks for a y/n before deleting
    (safer default for an interactive CLI). Returns the list of files
    actually deleted (empty list if none found or user declines).
    """
    targets = find_generated_files(directory)

    if not targets:
        print("No generated files found to clean up.")
        return []

    print(f"Found {len(targets)} generated file(s):")
    for t in targets:
        print(f"  {t}")

    if confirm:
        answer = input("Delete these files? [y/N]: ").strip().lower()
        if answer != "y":
            print("Cleanup cancelled.")
            return []

    deleted = []
    for t in targets:
        os.remove(t)
        deleted.append(t)
    print(f"Deleted {len(deleted)} file(s).")
    return deleted
