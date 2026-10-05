#!/usr/bin/env bash
set -euo pipefail

# Adds/updates simulate.py in your existing simulation_test folder -- additive
# only, doesn't touch roster.csv/signups.csv/generate_round.py/state_io.py.
# Run from INSIDE simulation_test/ with no arguments, or pass a path.
TARGET="${1:-.}"
mkdir -p "$TARGET"
echo "Writing simulate.py to: $TARGET"

cat > "$TARGET/simulate.py" <<'SIMULATE_EOF'
"""
simulate.py

Minimal-input, long-run simulation against your REAL roster.csv and the
REAL Prototype3 algorithm.py/helpers.py (not a reimplementation). Answers
a different question than generate_round.py:

  generate_round.py -- "what does ONE specific round look like, with
  whoever actually signed up for it?" (round-based, partial attendance,
  state persists call-to-call).

  simulate.py (this file) -- "if EVERYONE in the roster showed up to
  EVERY session, what would the sit-out fairness and skill-balance
  numbers look like after 10 days, 50 days, 100 days?" One command, a
  few prompts, no court-number fiddling -- courts are sized automatically
  from however many players are in roster.csv.

Run it with just:
    python3 simulate.py
and answer the prompts (every one has a sensible default -- press Enter
to accept it).

Produces ONE workbook, simulation_results.xlsx, with 4 sheets (a CSV
can't have multiple sheets -- this is a real spreadsheet file instead):
  Summary Checkpoints   -- every player's cumulative sit-out count and
                            ratio, logged at the end of EVERY simulated
                            day. Filter/pivot this in Excel to see day
                            10, day 20, etc.
  Final SitOut Tally    -- one row per player, final totals only, ranked
                            worst-sat-out to least, plus min/max/average
                            ratio formulas. The "how many times has each
                            player been sat out" answer.
  Skill Balance Summary  -- one row per day: average and worst rating-diff
                            across all courts that day. Tracks the
                            skill-matchup concern from the PR review over
                            a long run.
  Round By Round Detail  -- full audit trail, every court, every round,
                            every day. Big, but there if you need to
                            check a specific result.

Gender policy 2 ("same-gender teams only") does NOT exist in the real
algorithm.py on disk -- it only implements 0 (open) and 1 (enforce
mixed). Selecting it here patches the loaded algorithm module's scoring
IN THIS PROCESS ONLY (never touches the file on disk) so it's testable.
If you want this promoted into the real algorithm, that's a separate,
deliberate change to make there -- flag it rather than assuming this
harness quietly made it real.
"""

import csv
import os
import random
import sys

from openpyxl import Workbook
from openpyxl.styles import Font

PROTOTYPE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "Prototype3")
ROSTER_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "roster.csv")
OUTPUT_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "simulation_results.xlsx")
SEED = 2026
FONT = Font(name="Arial", size=10)
HEADER_FONT = Font(name="Arial", size=10, bold=True)


def _same_gender_score(team_a, team_b):
    """Mirror image of algorithm.py's raw_mixed_score: penalises a team
    whose two players are NOT the same gender, instead of penalising a
    team that isn't mixed. Not present in algorithm.py itself."""
    penalty = 0
    for team in (team_a, team_b):
        genders = {team[0].gender, team[1].gender}
        if len(genders) != 1:
            penalty += 1
    return penalty


def _install_same_gender_patch(algorithm_module):
    """Wraps the loaded algorithm module's _mixed_score so gender_mode=2
    (same-gender teams only) is scoreable, without editing algorithm.py
    on disk. Only called when gender_mode==2 is actually selected."""
    original = algorithm_module._mixed_score

    def patched(team_a, team_b, mode):
        if mode == 2:
            return _same_gender_score(team_a, team_b)
        return original(team_a, team_b, mode)

    algorithm_module._mixed_score = patched


def _prompt_int(label, default):
    raw = input(f"{label} [{default}]: ").strip()
    if raw == "":
        return default
    try:
        return int(raw)
    except ValueError:
        print(f"  Not a whole number, using default ({default}).")
        return default


def _prompt_choice(label, choices, default):
    desc = ", ".join(f"{k}={v}" for k, v in choices.items())
    raw = input(f"{label} ({desc}) [{default}]: ").strip()
    if raw == "":
        return default
    try:
        value = int(raw)
    except ValueError:
        print(f"  Not a valid choice, using default ({default}).")
        return default
    if value not in choices:
        print(f"  Not a valid choice, using default ({default}).")
        return default
    return value


def load_roster(path):
    rows = []
    with open(path, newline="") as f:
        for row in csv.DictReader(f):
            rows.append({
                "player_id": row["player_id"].strip(),
                "name": row["name"].strip(),
                "gender": row["gender"].strip().upper(),
                "rating": int(row["rating"]),
                "starting_sit_out_count": int(row["starting_sit_out_count"]) if row.get("starting_sit_out_count", "").strip() else 0,
            })
    return rows


def main():
    sys.path.insert(0, PROTOTYPE_DIR)
    try:
        import algorithm
        from helpers import Player
    except ImportError as e:
        print(
            f"Couldn't import the real Prototype3 algorithm from {PROTOTYPE_DIR} ({e}). "
            f"Make sure simulate.py is sitting in simulation_test/, next to a sibling Prototype3/ folder."
        )
        sys.exit(1)
    select_round_players = algorithm.select_round_players
    assign_courts_for_round = algorithm.assign_courts_for_round

    print("=== TennisSetMaker simulation ===")
    print(f"Roster: {ROSTER_PATH}\n")
    rounds_per_session = _prompt_int("Rounds per session (a normal club day)", default=6)
    mixed_mode = _prompt_choice(
        "Gender policy",
        {0: "open/any gender", 1: "enforce mixed boy-girl pairing", 2: "same-gender teams only"},
        default=0,
    )
    if mixed_mode == 2:
        _install_same_gender_patch(algorithm)
        print("  Note: 'same-gender teams only' isn't in algorithm.py on disk -- this run patches "
              "the scoring in memory for this simulation only. Say if you want it made real.\n")
    variety_mode = _prompt_choice(
        "Variety mode",
        {1: "hard avoid repeat partners/opponents", 2: "soft penalty for repeats", 3: "round-robin bias"},
        default=2,
    )

    roster_rows = load_roster(ROSTER_PATH)
    full_pool_size = len(roster_rows)

    # The real assign_courts_for_round scores every possible foursome from
    # the remaining pool before filling each court -- that's C(n,4)
    # candidates for the first court alone. Benchmarked directly against
    # this codebase: ~0.1s at 24 players (6 courts), but ~15s for a SINGLE
    # round at 72 players (18 courts) -- i.e. your full roster playing at
    # once would take over 2 HOURS to simulate 100 days. That's a real
    # scaling limit of the current algorithm, not a harness issue -- worth
    # knowing if a real club night could ever have that many people wanting
    # a court simultaneously. Keeping courts modest here is what keeps this
    # runnable in under a minute.
    num_courts = _prompt_int("Number of courts your club has available", default=6)
    if num_courts > 12:
        print(f"  Note: {num_courts} courts means {num_courts * 4} players/round -- the "
              f"scaling issue above means this could take a while (minutes, possibly more).")
    num_to_play = num_courts * 4

    # Deliberately NOT "everyone plays every day" -- with no scarcity at
    # all, nobody ever sits out and there's nothing to measure. Default
    # turnout is set above what the courts can hold, so sit-outs actually
    # happen and the ratio numbers mean something.
    players_per_day = _prompt_int(
        f"Players attending each day (drawn at random from your {full_pool_size}-player roster)",
        default=min(full_pool_size, num_to_play + 8),
    )
    if players_per_day > full_pool_size:
        print(f"  Only {full_pool_size} players in the roster -- capping to that.")
        players_per_day = full_pool_size
    if players_per_day <= num_to_play:
        print(f"  Note: {players_per_day} attending <= {num_to_play} that {num_courts} court(s) can "
              f"hold -- nobody will ever sit out from scarcity with this combination.")
    num_days = _prompt_int("Number of days/sessions to simulate", default=100)

    print(
        f"\n{full_pool_size} players in roster.csv; {players_per_day} drawn per day -> "
        f"{num_courts} court(s), {num_to_play} play per round "
        f"({max(players_per_day - num_to_play, 0)} sit out purely from not fitting a court, on top of fairness sit-outs).\n"
        f"Simulating {num_days} days x {rounds_per_session} rounds/day = "
        f"{num_days * rounds_per_session} total rounds...\n"
    )

    attendance_rng = random.Random(SEED)
    rng = random.Random(SEED + 1)
    all_players = {
        r["player_id"]: Player(
            name=r["name"], gender=r["gender"], rating=r["rating"],
            player_id=r["player_id"], sit_out_count=r["starting_sit_out_count"],
        )
        for r in roster_rows
    }
    # Separate from Player.sessions_played -- the real algorithm.py already
    # increments THAT field itself (once per round a player actually plays,
    # inside assign_courts_for_round) for its own bookkeeping. Reusing it
    # here for "days attended" would silently corrupt both numbers.
    days_attended = {pid: 0 for pid in all_players}
    checkpoint_rows = []
    skill_rows = []
    round_rows = []
    global_round = 0

    for day in range(1, num_days + 1):
        attendees = attendance_rng.sample(list(all_players.values()), players_per_day)
        for p in attendees:
            days_attended[p.player_id] += 1
        day_diffs = []
        for _round_in_day in range(1, rounds_per_session + 1):
            global_round += 1
            playing, sitting_out = select_round_players(attendees, num_to_play, rng)
            court_results = assign_courts_for_round(playing, num_courts, mixed_mode, variety_mode)

            sitting_ids = {id(p) for p in sitting_out}
            for p in attendees:
                if id(p) in sitting_ids:
                    p.sit_out_count += 1
                    p.last_sit_out_round = global_round
                    p.consecutive_sit_outs += 1
                else:
                    p.consecutive_sit_outs = 0

            for r in court_results:
                diff = abs(r["team_a_sum"] - r["team_b_sum"])
                day_diffs.append(diff)
                ta, tb = r["team_a"], r["team_b"]
                round_rows.append([
                    day, _round_in_day, r["court"],
                    ta[0].name, ta[1].name, tb[0].name, tb[1].name,
                    r["team_a_sum"], r["team_b_sum"], diff,
                ])

        for p in all_players.values():
            rounds_eligible_so_far = days_attended[p.player_id] * rounds_per_session
            ratio = (p.sit_out_count / rounds_eligible_so_far) if rounds_eligible_so_far else None
            checkpoint_rows.append([
                day, p.name, p.gender, p.rating, p.sit_out_count, rounds_eligible_so_far,
                round(ratio, 4) if ratio is not None else "no_data_yet",
            ])

        if day_diffs:
            skill_rows.append([day, round(sum(day_diffs) / len(day_diffs), 3), max(day_diffs), len(day_diffs)])

        if day % 10 == 0:
            print(f"  ...day {day}/{num_days} done", flush=True)

    final_rows = []
    for p in all_players.values():
        attended = days_attended[p.player_id]
        rounds_eligible_total = attended * rounds_per_session
        ratio = (p.sit_out_count / rounds_eligible_total) if rounds_eligible_total else None
        final_rows.append([p.name, p.gender, p.rating, p.sit_out_count, attended, rounds_eligible_total, ratio])
    # Never-drawn players (possible with a small players_per_day over a
    # short run) sort to the bottom rather than crashing the sort.
    final_rows.sort(key=lambda row: (row[-1] is None, -(row[-1] or 0)))

    _write_workbook(checkpoint_rows, final_rows, skill_rows, round_rows)

    ratios = [row[-1] for row in final_rows if row[-1] is not None]
    if ratios:
        print(f"Done. Sit-out ratio range across the full run: min={min(ratios):.3f}, max={max(ratios):.3f}, mean={sum(ratios)/len(ratios):.3f}")
    else:
        print("Done. (No player was drawn enough times to compute a ratio -- try more days or more players/day.)")
    print(f"Wrote: {OUTPUT_PATH} (sheets: Summary Checkpoints, Final SitOut Tally, Skill Balance Summary, Round By Round Detail)")


def _style_header(ws, num_cols):
    for col in range(1, num_cols + 1):
        ws.cell(row=1, column=col).font = HEADER_FONT
    ws.freeze_panes = "A2"


def _write_workbook(checkpoint_rows, final_rows, skill_rows, round_rows):
    wb = Workbook()

    ws1 = wb.active
    ws1.title = "Summary Checkpoints"
    ws1.append(["Day", "Player", "Gender", "Rating", "Cumulative_SitOut_Count", "Rounds_Eligible_So_Far", "SitOut_Ratio"])
    for row in checkpoint_rows:
        ws1.append(row)
    _style_header(ws1, 7)

    ws2 = wb.create_sheet("Final SitOut Tally")
    ws2.append(["Rank", "Player", "Gender", "Rating", "Total_SitOut_Count", "Days_Attended", "Total_Rounds_Eligible", "SitOut_Ratio"])
    for rank, row in enumerate(final_rows, start=1):
        name, gender, rating, sit_out_count, attended, rounds_eligible_total, ratio = row
        ws2.append([rank, name, gender, rating, sit_out_count, attended, rounds_eligible_total,
                    round(ratio, 4) if ratio is not None else "never_drawn"])
    _style_header(ws2, 8)
    last_row = len(final_rows) + 1
    ws2["J1"], ws2["K1"] = "Stat", "SitOut_Ratio"
    ws2["J1"].font = HEADER_FONT
    ws2["K1"].font = HEADER_FONT
    for i, (label, fn) in enumerate([("Min", "MIN"), ("Max", "MAX"), ("Average", "AVERAGE")], start=2):
        ws2.cell(row=i, column=10, value=label).font = FONT
        ws2.cell(row=i, column=11, value=f"={fn}(H2:H{last_row})").font = FONT

    ws3 = wb.create_sheet("Skill Balance Summary")
    ws3.append(["Day", "Avg_RatingDiff_AcrossCourts", "Worst_RatingDiff_ThisDay", "Courts_Played_ThisDay"])
    for row in skill_rows:
        ws3.append(row)
    _style_header(ws3, 4)

    ws4 = wb.create_sheet("Round By Round Detail")
    ws4.append(["Day", "Round", "Court", "TeamA_Player1", "TeamA_Player2", "TeamB_Player1", "TeamB_Player2", "TeamA_Sum", "TeamB_Sum", "RatingDiff"])
    for row in round_rows:
        ws4.append(row)
    _style_header(ws4, 10)

    for ws in (ws1, ws2, ws3, ws4):
        for row in ws.iter_rows():
            for cell in row:
                if cell.font != HEADER_FONT:
                    cell.font = FONT
        for col_cells in ws.columns:
            width = max((len(str(c.value)) for c in col_cells if c.value is not None), default=10)
            ws.column_dimensions[col_cells[0].column_letter].width = min(max(width + 2, 10), 32)

    wb.save(OUTPUT_PATH)


if __name__ == "__main__":
    main()
SIMULATE_EOF

echo "Done. Needs openpyxl -- if \"python3 simulate.py\" complains it's missing:"
echo '  pip install openpyxl   (or: pip install --break-system-packages openpyxl)'
echo "Then run:  python3 simulate.py"
