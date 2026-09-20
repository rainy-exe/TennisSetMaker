"""
main.py

Entry point for the TennisSetMaker prototype. Wires together:
  - config.py             (run inputs)
  - helpers.py            (data loading, random pull, output writers)
  - algorithm.py           (lexicographic priority-hierarchy engine — default)
  - algorithm_weighted.py  (normalized weighted-sum engine — opt-in via --weights)

Run with the strict priority hierarchy (default, matches the Scope of
Work's "priority 2 always beats priority 3" guarantee exactly):
    python3 main.py

Run with a dynamic 0.0-1.0 weight-vector "template" instead (smooth
trade-offs between criteria rather than a hard hierarchy):
    python3 main.py --weights 0.5 1.0 0.6 1.0 0.0
    (order: mixed, partner, opponent, skill, court_pref -- 0 = switched off)

Cleanup generated CSV/JSON output with: python3 main.py --cleanup
  (add --yes to skip the confirmation prompt, e.g. for scripting)
"""

import argparse
import random
import time

from config import gather_config_interactive, gather_basic_run_inputs_interactive
from helpers import (
    load_players_from_csv,
    pull_random_players,
    seed_sit_out_history,
    write_seed_log,
    print_round_summary,
    write_results_csv,
    write_sit_out_csv,
    cleanup_generated_files,
)
from algorithm import select_round_players, assign_courts_for_round
from algorithm_weighted import assign_courts_for_round_weighted, WeightVector


def _setup_run(basics, extra_config_for_log: dict, engine_tag: str):
    """Shared setup for both modes: seed handling, data load, random pull,
    sit-out history seeding, and the seed/state log file. Returns
    (seed, timestamp, players, num_to_play, rng).

    The returned `rng` is the SAME seeded instance used for the player
    pull/sit-out seeding above — reusing it (rather than creating a new
    one) for round-by-round tie-breaking in select_round_players keeps the
    whole run reproducible from a single seed while still avoiding a
    fixed, structurally-biased tie-break key like player name.

    engine_tag is included in every output filename so a lexicographic run
    and a weighted run with the same seed/timestamp never collide."""
    seed = basics.seed if basics.seed is not None else random.randint(0, 999_999)
    rng = random.Random(seed)
    print(f"Using seed: {seed}\n")

    all_rows = load_players_from_csv(basics.csv_path)
    players = pull_random_players(all_rows, basics.num_players, rng)
    seed_sit_out_history(players, rng)

    timestamp = time.strftime("%Y%m%d_%H%M%S")
    seed_log_path = f"seed_{engine_tag}_{seed}_{timestamp}.json"
    write_seed_log(
        seed_log_path,
        seed,
        {
            "num_players": basics.num_players,
            "num_courts": basics.num_courts,
            "num_rounds": basics.num_rounds,
            **extra_config_for_log,
        },
        players,
    )
    print(f"Seed/state log written to: {seed_log_path}\n")

    # Round down to the nearest multiple of 4: a court needs exactly 4
    # players, so any remainder must sit out rather than silently vanish.
    # e.g. 10 players / 3 courts requested -> capped at 10, but 10 isn't
    # divisible by 4, so only 8 actually play (2 full courts) and the
    # other 2 are routed to sitting_out by select_round_players.
    capped = min(len(players), basics.num_courts * 4)
    num_to_play = (capped // 4) * 4

    max_fillable_courts = num_to_play // 4
    if max_fillable_courts < basics.num_courts:
        print(
            f"Note: {basics.num_courts} courts requested, but with "
            f"{len(players)} players only {max_fillable_courts} full "
            f"court(s) can be filled each round (a court needs exactly 4 "
            f"players). The remaining players will rotate through as "
            f"sit-outs.\n"
        )

    return seed, timestamp, players, num_to_play, rng


def _record_sit_outs(players, sitting_out, round_num):
    """Shared per-round bookkeeping for both run paths: bump sit_out_count
    and last_sit_out_round for whoever sat out this round, and update
    everyone's consecutive_sit_outs streak (incremented for sitters, reset
    to 0 for anyone who played). Every player must be visited, not just
    sitters, or streaks would never reset for players who start playing
    again.

    Matched by identity (id(p)), not name, so this stays correct even if
    two players in the pool ever happen to share a name."""
    sitting_ids = {id(p) for p in sitting_out}
    for p in players:
        if id(p) in sitting_ids:
            p.sit_out_count += 1
            p.last_sit_out_round = round_num
            p.consecutive_sit_outs += 1
        else:
            p.consecutive_sit_outs = 0


def _write_outputs(seed, timestamp, all_round_results, sit_out_log, engine_tag):
    results_path = f"results_{engine_tag}_{seed}_{timestamp}.csv"
    sitouts_path = f"sitouts_{engine_tag}_{seed}_{timestamp}.csv"
    write_results_csv(results_path, all_round_results)
    write_sit_out_csv(sitouts_path, sit_out_log)
    print(f"\nResults written to: {results_path}")
    print(f"Sit-out log written to: {sitouts_path}")


def run_lexicographic():
    """Default mode: strict priority-hierarchy scoring (algorithm.py)."""
    cfg = gather_config_interactive()
    seed, timestamp, players, num_to_play, rng = _setup_run(
        cfg, {"mixed_mode": cfg.mixed_mode, "variety_mode": cfg.variety_mode, "engine": "lexicographic"},
        engine_tag="lex",
    )

    all_round_results = []
    sit_out_log = []

    for round_num in range(1, cfg.num_rounds + 1):
        playing, sitting_out = select_round_players(players, num_to_play, rng)
        court_results = assign_courts_for_round(playing, cfg.num_courts, cfg.mixed_mode, cfg.variety_mode)

        _record_sit_outs(players, sitting_out, round_num)

        print_round_summary(round_num, court_results, sitting_out)

        for r in court_results:
            all_round_results.append({
                "round": round_num, "court": r["court"],
                "team_a": r["team_a"], "team_b": r["team_b"],
                "team_a_sum": r["team_a_sum"], "team_b_sum": r["team_b_sum"],
            })
        for p in players:
            sit_out_log.append({
                "round": round_num, "name": p.name,
                "cumulative_sit_out_count": p.sit_out_count,
            })

    _write_outputs(seed, timestamp, all_round_results, sit_out_log, engine_tag="lex")


def run_weighted(weight_values):
    """Weighted-sum mode (algorithm_weighted.py): weight_values is a list
    of 5 floats [mixed, partner, opponent, skill, court_pref], each 0.0-1.0."""
    weights = WeightVector(*weight_values)
    print(f"Using weights: {weights}\n")

    basics = gather_basic_run_inputs_interactive()
    seed, timestamp, players, num_to_play, rng = _setup_run(
        basics, {"weights": weights.as_tuple(), "engine": "weighted"},
        engine_tag="wtd",
    )

    all_round_results = []
    sit_out_log = []

    for round_num in range(1, basics.num_rounds + 1):
        playing, sitting_out = select_round_players(players, num_to_play, rng)
        court_results = assign_courts_for_round_weighted(playing, basics.num_courts, weights)

        _record_sit_outs(players, sitting_out, round_num)

        print_round_summary(round_num, court_results, sitting_out)

        for r in court_results:
            all_round_results.append({
                "round": round_num, "court": r["court"],
                "team_a": r["team_a"], "team_b": r["team_b"],
                "team_a_sum": r["team_a_sum"], "team_b_sum": r["team_b_sum"],
            })
        for p in players:
            sit_out_log.append({
                "round": round_num, "name": p.name,
                "cumulative_sit_out_count": p.sit_out_count,
            })

    _write_outputs(seed, timestamp, all_round_results, sit_out_log, engine_tag="wtd")


def main():
    parser = argparse.ArgumentParser(description="TennisSetMaker prototype")
    parser.add_argument(
        "--weights", nargs=5, type=float, metavar=("MIXED", "PARTNER", "OPPONENT", "SKILL", "COURT_PREF"),
        help="Use the weighted-sum engine instead of the default lexicographic hierarchy. "
             "Five values, each 0.0-1.0, in priority order (0 = switched off). "
             "Example: --weights 0.5 1.0 0.6 1.0 0.0",
    )
    parser.add_argument(
        "--cleanup", action="store_true",
        help="Delete previously generated results/sitouts/seed files and exit "
             "(does not run the algorithm).",
    )
    parser.add_argument(
        "--yes", action="store_true",
        help="Skip the confirmation prompt when used with --cleanup.",
    )
    args = parser.parse_args()

    if args.cleanup:
        cleanup_generated_files(confirm=not args.yes)
        return

    if args.weights is not None:
        try:
            run_weighted(args.weights)
        except ValueError as e:
            parser.error(str(e))
    else:
        run_lexicographic()


if __name__ == "__main__":
    main()
