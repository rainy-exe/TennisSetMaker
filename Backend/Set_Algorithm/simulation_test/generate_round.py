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
    parser.add_argument("--mixed-mode", type=int, default=0, choices=[0, 1, 2], help="0 = open, 1 = mixed pairs, 2 = same-gender courts")
    parser.add_argument("--variety-mode", type=int, default=2, choices=[1, 2, 3])
    parser.add_argument("--seed", type=int, default=2026, help="Base seed -- combined with --round so tie-breaking is reproducible per round but not identical across rounds.")
    parser.add_argument("--prototype-dir", default=os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "Prototype4"), help="Algorithm folder to use (default: the sibling Prototype4 folder).")
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
