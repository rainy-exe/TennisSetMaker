"""
state_io.py

Converts between the real Prototype3 `Player` dataclass (which holds
`partners_played`/`opponents_played` as Python sets -- not JSON-safe) and a
plain dict that can be written to / read back from a state file on disk.

This is the thing that lets `generate_round.py` be called ONCE PER ROUND as
a separate process (mirroring "front-end asks back-end to generate a new
set") and still remember everything from previous rounds: every call reads
state.json, mutates in-memory Player objects via the real algorithm, then
writes state.json back out before exiting.
"""

import json
import os
from typing import Dict, List

from helpers import Player


def load_state(path: str) -> Dict[str, dict]:
    """Returns {player_id: state_dict}. Empty dict if the file doesn't exist
    yet (i.e. this is round 1 of a brand new season)."""
    if not os.path.exists(path):
        return {}
    with open(path) as f:
        return json.load(f)


def save_state(path: str, state: Dict[str, dict]) -> None:
    with open(path, "w") as f:
        json.dump(state, f, indent=2, sort_keys=True)


def player_to_state(p: Player) -> dict:
    return {
        "name": p.name,
        "gender": p.gender,
        "rating": p.rating,
        "sit_out_count": p.sit_out_count,
        "last_sit_out_round": p.last_sit_out_round,
        "consecutive_sit_outs": p.consecutive_sit_outs,
        "partners_played": sorted(p.partners_played),
        "opponents_played": sorted(p.opponents_played),
        "sessions_played": p.sessions_played,
    }


def state_to_player(player_id: str, s: dict) -> Player:
    return Player(
        name=s["name"],
        gender=s["gender"],
        rating=s["rating"],
        player_id=player_id,
        sit_out_count=s.get("sit_out_count", 0),
        last_sit_out_round=s.get("last_sit_out_round", -1),
        consecutive_sit_outs=s.get("consecutive_sit_outs", 0),
        partners_played=set(s.get("partners_played", [])),
        opponents_played=set(s.get("opponents_played", [])),
        sessions_played=s.get("sessions_played", 0),
    )


def build_players_for_round(roster_rows: List[dict], signed_up_ids: List[str], state: Dict[str, dict]) -> List[Player]:
    """roster_rows: the static roster (player_id, name, gender, rating,
    starting_sit_out_count, court_pref), as loaded from roster.csv.
    signed_up_ids: player_ids who signed up for THIS round only -- this is
    the round-based signup model, not "everyone in the club".

    For each signed-up player: if they already have state (they've been
    through at least one earlier round), restore it exactly as the real
    algorithm left it. If this is the FIRST time we've ever seen this
    player_id (a brand-new signup, or a new club member added mid-season),
    initialise them fresh from roster.csv's starting_sit_out_count (defaults
    to 0) -- this is exactly how adding a new player after round 2 of 6
    gets picked up with zero code changes: just add their row to roster.csv
    and list their player_id in signups.csv from whichever round they join.
    """
    roster_by_id = {r["player_id"]: r for r in roster_rows}
    players = []
    for pid in signed_up_ids:
        if pid not in roster_by_id:
            raise ValueError(
                f"signups.csv lists player_id={pid!r} for this round, but that "
                f"player_id isn't in roster.csv. Add them to the roster first."
            )
        row = roster_by_id[pid]
        if pid in state:
            players.append(state_to_player(pid, state[pid]))
        else:
            players.append(Player(
                name=row["name"],
                gender=row["gender"],
                rating=row["rating"],
                player_id=pid,
                sit_out_count=row.get("starting_sit_out_count", 0),
            ))
    return players
