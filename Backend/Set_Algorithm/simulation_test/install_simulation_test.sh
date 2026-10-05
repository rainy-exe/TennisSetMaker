#!/usr/bin/env bash
set -euo pipefail

# Writes the simulation_test folder (generate_round.py, state_io.py,
# roster.csv, signups.csv, README.md) straight into your repo checkout.
# Run this with Git Bash or WSL from anywhere -- it doesn't matter what
# your current directory is, only the TARGET path below matters.
#
# Default path assumes Git Bash's drive convention (G:\... -> /g/...).
# If you're running this in WSL instead, comment the first TARGET line
# and uncomment the /mnt/g/... one below, or just pass your own path as
# the first argument: ./install_simulation_test.sh /some/other/path

TARGET="${1:-/g/UNI/2026_Semester_2/CITS3200_Professional_Computing/Repo/Algorithm/TennisSetMaker/Backend/Set_Algorithm/simulation_test}"
# TARGET="${1:-/mnt/g/UNI/2026_Semester_2/CITS3200_Professional_Computing/Repo/Algorithm/TennisSetMaker/Backend/Set_Algorithm/simulation_test}"

mkdir -p "$TARGET"
echo "Writing simulation_test files to: $TARGET"

cat > "$TARGET/generate_round.py" <<'GENROUND_EOF'
"""
generate_round.py

Mock test harness for round-by-round set generation, using the REAL,
unmodified Prototype3 algorithm (algorithm.py / helpers.py) -- not a
reimplementation -- so what you see here is exactly what the current
codebase would produce, not an approximation of it.

Why this exists (see the README in this folder for the full walkthrough):
  - Your club signs players up PER ROUND, not per session (someone might
    play rounds 3,4,5,6 of a 6-round session while someone else plays
    1,2,4,5). This script's "active pool" for a round is whoever signed up
    for THAT round, read from signups.csv -- not the whole roster.
  - Each call to this script is one "front-end asks back-end to generate a
    new set" event. State (sit-out counts, partner/opponent history) lives
    in a JSON file on disk and is loaded/saved on every call, so calling
    this once per round in sequence is exactly equivalent to the real
    system generating round after round over time.
  - A new player joining mid-season needs no code change: add their row to
    roster.csv and list their player_id in signups.csv from whichever round
    they start. The first time generate_round.py sees an unfamiliar
    player_id, it initialises them fresh (see state_io.build_players_for_round)
    -- this is a deliberate design choice (today's raw sit_out_count=0 cold
    start), not a fix -- see the README for why that matters.
  - Input is a CSV you control, so you can add two mock columns
    (player_id, starting_sit_out_count) onto a copy of the real client
    roster locally, run everything here, and never upload the real sheet.

Usage (run from inside your Prototype3 folder, or point --prototype-dir at
it):
    python3 generate_round.py --round 1 --roster roster.csv --signups signups.csv \
        --state state.json --output results_for_client.csv --num-courts 2 \
        --mixed-mode 0 --variety-mode 2 --seed 2026

Run again with --round 2, then --round 3, etc. -- one call per round, in
order. Each call appends to results_for_client.csv and updates state.json.
"""

import argparse
import csv
import os
import sys


def _load_roster(path):
    rows = []
    with open(path, newline="") as f:
        for row in csv.DictReader(f):
            rows.append({
                "player_id": row["player_id"].strip(),
                "name": row["name"].strip(),
                "gender": row["gender"].strip().upper(),
                "rating": int(row["rating"]),
                "starting_sit_out_count": int(row["starting_sit_out_count"]) if row.get("starting_sit_out_count", "").strip() else 0,
                "court_pref": row.get("court_pref", "").strip() or None,
            })
    return rows


def _load_signups_for_round(path, round_num):
    ids = []
    with open(path, newline="") as f:
        for row in csv.DictReader(f):
            if int(row["round_number"]) == round_num:
                ids.append(row["player_id"].strip())
    return ids


def _record_sit_outs(players, sitting_out, round_num):
    """Copied verbatim from main.py's private helper (not exported there) --
    same bookkeeping: bump sit_out_count/last_sit_out_round for sitters,
    advance/reset everyone's consecutive_sit_outs streak."""
    sitting_ids = {id(p) for p in sitting_out}
    for p in players:
        if id(p) in sitting_ids:
            p.sit_out_count += 1
            p.last_sit_out_round = round_num
            p.consecutive_sit_outs += 1
        else:
            p.consecutive_sit_outs = 0


def _append_client_csv(path, round_num, court_results, sitting_out, court_pref_by_id):
    """Human-readable, client-shareable CSV: one row per player per round.
    Played players show their court/team/partner-vs-opponent context;
    sit-out players show up in their own block with their updated
    cumulative sit-out count sitting right next to their rating, which is
    the specific thing you asked for ("sit out attribute slapped on next
    to the player skills"). Appends -- writes the header only once."""
    is_new_file = not os.path.exists(path)
    with open(path, "a", newline="") as f:
        writer = csv.writer(f)
        if is_new_file:
            writer.writerow([
                "Round", "Court", "Team", "Player", "Gender", "Rating",
                "Court_Pref", "TeamRatingSum", "RatingDiffThisCourt",
                "Playing_or_SatOut", "Cumulative_SitOut_Count",
            ])
        for r in court_results:
            diff = abs(r["team_a_sum"] - r["team_b_sum"])
            for team_label, team in (("A", r["team_a"]), ("B", r["team_b"])):
                team_sum = r["team_a_sum"] if team_label == "A" else r["team_b_sum"]
                for p in team:
                    writer.writerow([
                        round_num, r["court"], team_label, p.name, p.gender, p.rating,
                        court_pref_by_id.get(p.player_id, ""), team_sum, diff,
                        "PLAYING", p.sit_out_count,
                    ])
        for p in sitting_out:
            writer.writerow([
                round_num, "-", "-", p.name, p.gender, p.rating,
                court_pref_by_id.get(p.player_id, ""), "-", "-",
                "SAT_OUT", p.sit_out_count,
            ])


def main():
    parser = argparse.ArgumentParser(description="Generate one round of TennisSetMaker sets, round-based signups, with persisted state.")
    parser.add_argument("--round", type=int, required=True, help="Round number to generate (call once per round, in order).")
    parser.add_argument("--roster", default="roster.csv", help="Static player list: player_id,name,gender,rating,starting_sit_out_count,court_pref")
    parser.add_argument("--signups", default="signups.csv", help="Per-round signups, long format: round_number,player_id")
    parser.add_argument("--state", default="state.json", help="Where sit-out/partner/opponent history persists between round calls.")
    parser.add_argument("--output", default="results_for_client.csv", help="Client-shareable CSV this round's results get appended to.")
    parser.add_argument("--num-courts", type=int, default=2)
    parser.add_argument("--mixed-mode", type=int, default=0, choices=[0, 1])
    parser.add_argument("--variety-mode", type=int, default=2, choices=[1, 2, 3])
    parser.add_argument("--seed", type=int, default=2026, help="Base seed -- combined with --round so tie-breaking is reproducible per round but not identical across rounds.")
    parser.add_argument("--prototype-dir", default=None, help="Path to your Prototype3 folder, if this script isn't sitting inside it already.")
    args = parser.parse_args()

    if args.prototype_dir:
        sys.path.insert(0, args.prototype_dir)
    # Make sure this folder (for state_io.py) is importable too.
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

    try:
        from algorithm import select_round_players, assign_courts_for_round
        from helpers import print_round_summary
    except ImportError as e:
        parser.error(
            f"Couldn't import the real Prototype3 algorithm ({e}). Either run this "
            f"script from inside your Prototype3 folder, or pass --prototype-dir "
            f"pointing at it."
        )

    import state_io  # local helper, not part of Prototype3

    roster_rows = _load_roster(args.roster)
    court_pref_by_id = {r["player_id"]: r["court_pref"] for r in roster_rows if r["court_pref"]}
    signed_up_ids = _load_signups_for_round(args.signups, args.round)
    if not signed_up_ids:
        parser.error(f"No signups found for round {args.round} in {args.signups}.")

    state = state_io.load_state(args.state)
    players = state_io.build_players_for_round(roster_rows, signed_up_ids, state)

    import random
    rng = random.Random(f"{args.seed}-round{args.round}")

    num_to_play = (min(len(players), args.num_courts * 4) // 4) * 4
    if num_to_play < len(players):
        print(
            f"Note: {len(players)} signed up for round {args.round}, but "
            f"{args.num_courts} court(s) can only fit {num_to_play}. "
            f"{len(players) - num_to_play} will sit out this round.\n"
        )

    playing, sitting_out = select_round_players(players, num_to_play, rng)
    court_results = assign_courts_for_round(playing, args.num_courts, args.mixed_mode, args.variety_mode)
    _record_sit_outs(players, sitting_out, args.round)

    # Court preference isn't wired into the real algorithm yet (algorithm.py's
    # raw_court_pref_score() is a stub that always returns 0 -- see its
    # docstring). We carry court_pref through for display in the client CSV
    # only, so Warren can see it, but it does NOT currently influence which
    # court/team anyone lands on. Flagging this explicitly so this test
    # isn't read as "court preference is already handled."
    print_round_summary(args.round, court_results, sitting_out)

    # Persist every signed-up player's updated state back to disk, whether
    # they played or sat out. Anyone NOT signed up for this round is left
    # untouched -- not signing up isn't treated as a sit-out.
    for p in players:
        state[p.player_id] = state_io.player_to_state(p)
    state_io.save_state(args.state, state)

    _append_client_csv(args.output, args.round, court_results, sitting_out, court_pref_by_id)
    print(f"\nAppended round {args.round} to: {args.output}")
    print(f"State saved to: {args.state}")


if __name__ == "__main__":
    main()
GENROUND_EOF

cat > "$TARGET/state_io.py" <<'STATEIO_EOF'
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
STATEIO_EOF

cat > "$TARGET/roster.csv" <<'ROSTER_EOF'
player_id,name,gender,rating,starting_sit_out_count,court_pref
f38b2ffc-80a4-4f5a-91c9-bc701e7ea419,Andrew S,MALE,6,0,
f3f49249-dc28-4f90-a5ae-c7978306d03b,Anton B,MALE,5,0,
e5121482-3929-4d22-a255-accb1a466884,Darius O,MALE,5,0,
6bad6be2-8e7a-46e9-9f19-950499dd251d,Basil R,MALE,3,0,
d7a7a3cc-8c3d-4f16-9293-de8fc88b2875,Chui T,MALE,3,0,
7dabe929-c4a3-44bf-86cd-75e9bb049a79,Chloe T,FEMALE,6,0,
70eb9a0a-9626-4ae6-85e8-18fac0433cbd,Blair A,MALE,4,0,
14aa4e71-9d3c-4dec-80a6-1f933d6c51e3,Brid D,FEMALE,5,0,
1919e93a-d117-45ad-8988-93101c593af5,Gary H,MALE,5,0,
e4163207-d094-4996-82f0-ee99731c9452,Frank S,MALE,5,0,
5071950e-adec-4f11-bd83-6e77af67d461,Franck T,MALE,4,0,
59001ac9-4063-49bc-a5b0-0a2d35d14880,David M,MALE,3,0,
5b4c8012-ede7-4d0c-bfb8-8309fadb8908,David R,MALE,3,0,
bef7e268-ffe9-46ab-a058-1ccace1d62e0,Emma H,FEMALE,5,0,
b91dddd9-1389-4372-a341-738c837a7935,Direshni B,FEMALE,5,0,
89be9c1c-8eb5-440f-96f4-488157241955,Di B,FEMALE,3,0,
24114258-751b-4c83-89a0-47dc4ac87fc0,Jack W,MALE,7,0,
b766ff10-b437-4db5-a511-49bbe060a724,Isaac A,MALE,7,0,
d9090b89-0655-4096-8f1a-8a1d93d20470,Gerry K,MALE,7,0,
7608d942-5d11-4a9d-9e6c-9992b5fb12e0,Indu J,FEMALE,5,0,
e41686cd-6616-4227-9737-14726c167229,Ian L,MALE,5,0,
de0279e1-7f9a-4098-8df0-5f2595f19a51,Helen M,FEMALE,5,0,
cd73de00-81a0-4a05-ace9-da661dcf884c,Harshil P,MALE,7,0,
e01c683e-99a4-4df0-9de3-a361c0099eba,Grey J,MALE,5,0,
864fa3f3-eab0-4e9b-a5ed-0de47db4304d,Jacqui M,FEMALE,5,0,
90624fe3-6b82-46c9-982f-b0f1423674a6,Judy K,FEMALE,7,0,
8350a73f-7aac-419f-bb75-9e0fed1d9d16,Janet F,FEMALE,5,0,
93550840-067f-4cfc-8e1f-d3d9849acfb5,Jenny C,FEMALE,6,0,
d1711cbd-2106-419e-840d-31b5397a7623,Jon D,MALE,7,0,
85f184e0-b464-4554-b675-299b0c83e786,Jim E,MALE,5,0,
b7a1774f-1a42-421e-aba4-c70ee306f0c4,Jodi P,FEMALE,4,0,
2475263c-7854-4014-aded-c86a9f4fb02b,Mahi M,MALE,5,0,
d9814559-3a9a-4a39-a261-e34a7b6bc3c6,Kate B,FEMALE,4,0,
b3618e1c-a06d-4a69-9f3c-42b2e2cbbb93,Elizabeth S,FEMALE,5,0,
b6eafff5-782a-4e6b-ac9f-21df74f09af5,Lisa M,FEMALE,3,0,
fa97002c-fcba-4167-b5a9-ca5fedf165da,Kon K,MALE,4,0,
66eebc57-8f4e-4b4f-8041-f5ee8bae8e66,Linda A,FEMALE,3,0,
8c53765f-4ec0-4954-bf8b-2a6aab74fe57,Liesl N,FEMALE,6,0,
b2c55523-807c-4e30-a598-d0dbccbfd2ec,Mark D,MALE,5,0,
66e61127-e26b-424a-8e0d-8d877a98b9ac,Neil C,MALE,5,0,
d4f398ee-4b5c-4a95-b3d9-18086edd77d8,Mark L,MALE,5,0,
265c28ea-0879-4955-825f-f87c44df8a13,Nathalie S,FEMALE,5,0,
f5ee8c72-98e6-45b7-a1cd-7be8a7d83351,Mehmet C,MALE,4,0,
ed4d19b8-85dc-4a68-8db5-4088754e64f4,Melanie E,FEMALE,4,0,
33288e16-ebe8-4fa9-9c24-c1ae7d510557,Michelle S,FEMALE,4,0,
7776c655-7c4e-4248-b151-5fff42969a50,Rachel P,FEMALE,4,0,
7f45b096-d380-4b73-8446-ab8c82e26123,Pip M,FEMALE,4,0,
a9f5307c-8ece-48b0-af29-a9f1ced66b44,Philip W,MALE,4,0,
4a41f8e4-d4d6-4278-889b-5123e842997d,Nigel B,MALE,4,0,
cfd8c364-eab4-4bd6-b528-ea79511070a7,Richard C,MALE,5,0,
b76dc0c3-b078-42db-b03e-53cf192f09a9,Rob L,MALE,5,0,
4242c225-83e2-465a-b654-0600c8fb4456,Robert M,MALE,4,0,
2edc49c1-f71b-4135-ac06-43b173f29c6a,Senq L,MALE,5,0,
ba5c875c-d7aa-4208-acff-b0f0f79edc82,Sonya K,FEMALE,3,0,
a60741f3-1075-45b0-b0ac-debe7aa95416,Tricia H,FEMALE,4,0,
5ec0c260-7f98-40a6-a73c-49ea44371ff3,Stjepan M,MALE,5,0,
db4c9492-bf5f-45e2-b1d0-6d9c18bee074,Sung K,MALE,5,0,
ea9e7ab5-730b-49dc-a577-c324694baad6,Suresh S,MALE,6,0,
7383d496-000e-45d0-9798-5d060397a31d,Susan T,FEMALE,5,0,
d61b5e08-eea3-4d4e-8bed-5b6594a4d29c,Yvette F,FEMALE,4,0,
32e4034c-cb34-4cea-9c31-b9301543c8ef,Vivek R,MALE,6,0,
0b6ec6f3-9efe-4fda-9eed-fcfc3f724926,Warren V,MALE,7,0,
44becdce-f4d4-45ec-8be4-0e7fc07991d4,Khilan S,MALE,3,0,
5b74c718-a2aa-416a-81f6-192bab215122,Amelie B,FEMALE,5,0,
aa220078-503d-486b-8150-4585279e6933,Adam S,MALE,3,0,
ed1ba5c8-c4ff-464e-9304-3fca9543c6c3,Sam L,MALE,6,0,
b093e3b4-7df2-4c58-9c56-dc87f32efa03,Ash L,MALE,6,0,
8bb9c912-2286-4c53-b000-e394b870e4e1,Oki B,MALE,3,0,
219763c5-9682-4074-b915-72d53911c9b0,Louise D,FEMALE,3,0,
b14ab8e9-e041-4118-991f-77c9f551abfd,Ceclie L,FEMALE,2,0,
6af38f6c-159d-4a38-8a6a-03b381356bc5,Suzanne B,FEMALE,2,0,
22a60b44-ef5f-431c-a8fd-b92dab266981,Gordon D,MALE,2,0,
96060250-ea3e-4f73-884c-1884c88f63d6,Lyn P,FEMALE,3,0,
63f1bbbc-697b-45cb-81f8-c04a4e564cbf,Ann S,FEMALE,2,0,
ce918208-8520-4f0c-a606-799cced8c284,Soni B,FEMALE,2,0,
ROSTER_EOF

cat > "$TARGET/signups.csv" <<'SIGNUPS_EOF'
round_number,player_id
1,f38b2ffc-80a4-4f5a-91c9-bc701e7ea419
1,f3f49249-dc28-4f90-a5ae-c7978306d03b
1,e5121482-3929-4d22-a255-accb1a466884
1,6bad6be2-8e7a-46e9-9f19-950499dd251d
1,d7a7a3cc-8c3d-4f16-9293-de8fc88b2875
1,7dabe929-c4a3-44bf-86cd-75e9bb049a79
1,70eb9a0a-9626-4ae6-85e8-18fac0433cbd
1,14aa4e71-9d3c-4dec-80a6-1f933d6c51e3
1,1919e93a-d117-45ad-8988-93101c593af5
1,e4163207-d094-4996-82f0-ee99731c9452
1,5071950e-adec-4f11-bd83-6e77af67d461
1,59001ac9-4063-49bc-a5b0-0a2d35d14880
1,5b4c8012-ede7-4d0c-bfb8-8309fadb8908
1,bef7e268-ffe9-46ab-a058-1ccace1d62e0
1,b91dddd9-1389-4372-a341-738c837a7935
1,89be9c1c-8eb5-440f-96f4-488157241955
1,24114258-751b-4c83-89a0-47dc4ac87fc0
1,b766ff10-b437-4db5-a511-49bbe060a724
1,d9090b89-0655-4096-8f1a-8a1d93d20470
1,7608d942-5d11-4a9d-9e6c-9992b5fb12e0
1,e41686cd-6616-4227-9737-14726c167229
1,de0279e1-7f9a-4098-8df0-5f2595f19a51
1,cd73de00-81a0-4a05-ace9-da661dcf884c
1,e01c683e-99a4-4df0-9de3-a361c0099eba
1,864fa3f3-eab0-4e9b-a5ed-0de47db4304d
1,90624fe3-6b82-46c9-982f-b0f1423674a6
1,8350a73f-7aac-419f-bb75-9e0fed1d9d16
1,93550840-067f-4cfc-8e1f-d3d9849acfb5
1,d1711cbd-2106-419e-840d-31b5397a7623
1,85f184e0-b464-4554-b675-299b0c83e786
1,b7a1774f-1a42-421e-aba4-c70ee306f0c4
1,2475263c-7854-4014-aded-c86a9f4fb02b
1,d9814559-3a9a-4a39-a261-e34a7b6bc3c6
1,b3618e1c-a06d-4a69-9f3c-42b2e2cbbb93
1,b6eafff5-782a-4e6b-ac9f-21df74f09af5
1,fa97002c-fcba-4167-b5a9-ca5fedf165da
1,66eebc57-8f4e-4b4f-8041-f5ee8bae8e66
1,8c53765f-4ec0-4954-bf8b-2a6aab74fe57
1,b2c55523-807c-4e30-a598-d0dbccbfd2ec
1,66e61127-e26b-424a-8e0d-8d877a98b9ac
1,d4f398ee-4b5c-4a95-b3d9-18086edd77d8
1,265c28ea-0879-4955-825f-f87c44df8a13
1,f5ee8c72-98e6-45b7-a1cd-7be8a7d83351
1,ed4d19b8-85dc-4a68-8db5-4088754e64f4
1,33288e16-ebe8-4fa9-9c24-c1ae7d510557
1,7776c655-7c4e-4248-b151-5fff42969a50
1,7f45b096-d380-4b73-8446-ab8c82e26123
1,a9f5307c-8ece-48b0-af29-a9f1ced66b44
1,4a41f8e4-d4d6-4278-889b-5123e842997d
1,cfd8c364-eab4-4bd6-b528-ea79511070a7
1,b76dc0c3-b078-42db-b03e-53cf192f09a9
1,4242c225-83e2-465a-b654-0600c8fb4456
1,2edc49c1-f71b-4135-ac06-43b173f29c6a
1,ba5c875c-d7aa-4208-acff-b0f0f79edc82
1,a60741f3-1075-45b0-b0ac-debe7aa95416
1,5ec0c260-7f98-40a6-a73c-49ea44371ff3
1,db4c9492-bf5f-45e2-b1d0-6d9c18bee074
1,ea9e7ab5-730b-49dc-a577-c324694baad6
1,7383d496-000e-45d0-9798-5d060397a31d
1,d61b5e08-eea3-4d4e-8bed-5b6594a4d29c
1,32e4034c-cb34-4cea-9c31-b9301543c8ef
1,0b6ec6f3-9efe-4fda-9eed-fcfc3f724926
1,44becdce-f4d4-45ec-8be4-0e7fc07991d4
1,5b74c718-a2aa-416a-81f6-192bab215122
1,aa220078-503d-486b-8150-4585279e6933
1,ed1ba5c8-c4ff-464e-9304-3fca9543c6c3
1,b093e3b4-7df2-4c58-9c56-dc87f32efa03
1,8bb9c912-2286-4c53-b000-e394b870e4e1
1,219763c5-9682-4074-b915-72d53911c9b0
1,b14ab8e9-e041-4118-991f-77c9f551abfd
1,6af38f6c-159d-4a38-8a6a-03b381356bc5
1,22a60b44-ef5f-431c-a8fd-b92dab266981
1,96060250-ea3e-4f73-884c-1884c88f63d6
1,63f1bbbc-697b-45cb-81f8-c04a4e564cbf
1,ce918208-8520-4f0c-a606-799cced8c284
SIGNUPS_EOF

cat > "$TARGET/README.md" <<'README_EOF'
# simulation_test

Round-based mock test harness. Calls the REAL, unmodified `algorithm.py` /
`helpers.py` in the sibling `Prototype3` folder -- not a reimplementation.

## player_id format

`roster.csv`'s `player_id` column is a UUID4 string, matching
`app/models.py`'s `Player.id` in the backend branch exactly
(`db.Column(db.String(36), primary_key=True, default=new_id)`, where
`new_id()` is `str(uuid.uuid4())`). Chosen deliberately so results shown to
the team look like real backend player IDs, not an invented test-only
scheme -- no format translation needed if this gets compared against
backend output later.

## What's already filled in

`roster.csv` is already built from the real client roster (`NTC_ratings_data.csv`,
names already truncated to first name + last initial by the client) with a
UUID4 `player_id` generated per player and `starting_sit_out_count=0` for
everyone (clean-slate test). `signups.csv` lists every player signed up
for round 1, so round 1 runs immediately with no editing.

You still decide:
- `--num-courts`, matching however many courts you're testing with.
- `signups.csv` rows for round 2 onward -- copy the pattern
  (`round_number,player_id`), list whoever's playing that round. Not
  everyone has to appear in every round.
- Adding a new player mid-season: add a row to `roster.csv` (generate a
  UUID for them -- `python3 -c "import uuid; print(uuid.uuid4())"` ) and
  list them in `signups.csv` from whichever round they join.

## Running it

From inside this folder:

```
python3 generate_round.py --round 1 --num-courts 4 --prototype-dir ../Prototype3
python3 generate_round.py --round 2 --num-courts 4 --prototype-dir ../Prototype3
```

One call per round, in order. Each call reads/updates `state.json`
(created automatically on round 1) and appends to
`results_for_client.csv` -- that's the file to share with the team/Warren,
sortable by `Round` then `Court` in Excel.

## Court preference

`court_pref` is carried through into `results_for_client.csv` for
visibility only. `algorithm.py`'s `raw_court_pref_score()` always returns
0 right now -- it doesn't affect court/team assignment yet. Say if you
want that wired in before the next test round.
README_EOF

chmod +x "$TARGET/generate_round.py"

echo "Done. ${TARGET} now has: generate_round.py, state_io.py, roster.csv, signups.csv, README.md"
echo "Next: cd into it and run:"
echo "  python3 generate_round.py --round 1 --num-courts 4 --prototype-dir ../Prototype3"
