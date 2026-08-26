"""
main.py

Entry point for the TennisSetMaker prototype. Wires together:
  - config.py    (run inputs)
  - helpers.py   (data loading, random pull, output writers)
  - algorithm.py (the actual priority-weight block chain)

Run with: python3 main.py
Cleanup generated CSV/JSON output with: python3 main.py --cleanup
  (add --yes to skip the confirmation prompt, e.g. for scripting)
"""

import argparse
import random
import time

from config import gather_config_interactive
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


def run():
    cfg = gather_config_interactive()

    # --- seed handling ---
    seed = cfg.seed if cfg.seed is not None else random.randint(0, 999_999)
    rng = random.Random(seed)
    print(f"Using seed: {seed}\n")

    # --- data load + random pull ---
    all_rows = load_players_from_csv(cfg.csv_path)
    players = pull_random_players(all_rows, cfg.num_players, rng)
    seed_sit_out_history(players, rng)

    timestamp = time.strftime("%Y%m%d_%H%M%S")
    seed_log_path = f"seed_{seed}_{timestamp}.json"
    write_seed_log(
        seed_log_path,
        seed,
        {
            "num_players": cfg.num_players,
            "num_courts": cfg.num_courts,
            "num_rounds": cfg.num_rounds,
            "mixed_mode": cfg.mixed_mode,
            "variety_mode": cfg.variety_mode,
        },
        players,
    )
    print(f"Seed/state log written to: {seed_log_path}\n")

    num_to_play = min(len(players), cfg.num_courts * 4)

    all_round_results = []   # for results CSV
    sit_out_log = []         # for sit-out CSV

    for round_num in range(1, cfg.num_rounds + 1):
        playing, sitting_out = select_round_players(players, num_to_play)

        court_results = assign_courts_for_round(
            playing, cfg.num_courts, cfg.mixed_mode, cfg.variety_mode
        )

        # bump sit-out counts for whoever sat out this round
        for p in sitting_out:
            p.sit_out_count += 1

        # console log (uses the algorithm's actual team_a/team_b split)
        print_round_summary(round_num, court_results, sitting_out)

        # collect for CSV
        for r in court_results:
            all_round_results.append({
                "round": round_num,
                "court": r["court"],
                "team_a": r["team_a"],
                "team_b": r["team_b"],
                "team_a_sum": r["team_a_sum"],
                "team_b_sum": r["team_b_sum"],
            })
        for p in players:
            sit_out_log.append({
                "round": round_num,
                "name": p.name,
                "cumulative_sit_out_count": p.sit_out_count,
            })

    results_path = f"results_{seed}_{timestamp}.csv"
    sitouts_path = f"sitouts_{seed}_{timestamp}.csv"
    write_results_csv(results_path, all_round_results)
    write_sit_out_csv(sitouts_path, sit_out_log)

    print(f"\nResults written to: {results_path}")
    print(f"Sit-out log written to: {sitouts_path}")


def main():
    parser = argparse.ArgumentParser(description="TennisSetMaker prototype")
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

    run()


if __name__ == "__main__":
    main()
