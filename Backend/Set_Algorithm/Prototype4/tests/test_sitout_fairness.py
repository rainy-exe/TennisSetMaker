"""Tests for the two sit-out fairness bugs fixed in Prototype3:

1. Tie-break bias: ties used to break alphabetically by name every round,
   systematically favouring/disfavouring players purely by name. Fixed by
   shuffling with the run's seeded RNG before sorting.
2. Nudge doesn't scale: the old flat +1 "sat out last round" nudge wasn't
   enough to stop the same player(s) sitting out several rounds in a row
   when their raw sit_out_count started well below everyone else's. Fixed
   by an escalating consecutive_sit_outs-based nudge.

Both fixes are exercised through the real production functions
(select_round_players + main._record_sit_outs) rather than reimplemented
logic, so these tests fail if the actual behavior regresses."""

import random

from helpers import Player
from algorithm import select_round_players
from main import _record_sit_outs


def make_player(name: str, sit_out_count: int = 0) -> Player:
    return Player(name=name, gender="MALE", rating=5, sit_out_count=sit_out_count)


def simulate(players, num_to_play, num_rounds, seed):
    rng = random.Random(seed)
    history = []  # per round: {player_name: cumulative sit_out_count}
    consecutive_log = []  # per round: {player_name: consecutive_sit_outs}
    for round_num in range(1, num_rounds + 1):
        playing, sitting_out = select_round_players(players, num_to_play, rng)
        _record_sit_outs(players, sitting_out, round_num)
        history.append({p.name: p.sit_out_count for p in players})
        consecutive_log.append({p.name: p.consecutive_sit_outs for p in players})
    return history, consecutive_log


def test_tie_break_is_balanced_over_many_rounds():
    """6 players, all starting equal, 2 sit out per round for 30 rounds.
    A fair rotation should keep everyone's cumulative sit-out count nearly
    identical (previously, alphabetically-late names were systematically
    favoured for sit-out over the whole run)."""
    players = [make_player(n) for n in ["Alice", "Ben", "Carla", "Dana", "Evan", "Zoe"]]
    history, _ = simulate(players, num_to_play=4, num_rounds=30, seed=42)

    final_counts = history[-1]
    spread = max(final_counts.values()) - min(final_counts.values())
    assert spread <= 1, f"Sit-out counts not balanced: {final_counts}"


def test_tie_break_outcome_is_invariant_under_renaming():
    """The sharpest regression test: rerun the exact same scenario (same
    seed, same list order, same starting stats) but with players renamed.
    Under the old alphabetical tie-break, renaming would change who loses
    ties and shift the whole distribution. Under the fix (RNG-based
    shuffle, independent of name), the resulting *distribution* of
    cumulative sit-out counts must be identical either way."""
    names_a = ["Alice", "Ben", "Carla", "Dana", "Evan", "Zoe"]
    names_b = ["Zoe", "Yannick", "Xander", "Wendy", "Vic", "Uma"]

    players_a = [make_player(n) for n in names_a]
    players_b = [make_player(n) for n in names_b]

    history_a, _ = simulate(players_a, num_to_play=4, num_rounds=30, seed=99)
    history_b, _ = simulate(players_b, num_to_play=4, num_rounds=30, seed=99)

    distribution_a = sorted(history_a[-1].values())
    distribution_b = sorted(history_b[-1].values())
    assert distribution_a == distribution_b


def test_consecutive_sitouts_escalate_and_recover_quickly():
    """2 players starting far behind everyone else (gap of 5) used to sit
    out repeatedly for ~5 rounds under the old flat nudge. With the
    escalating fix, the nudge is sit_out_count + consecutive_sit_outs, so
    a player who has sat out k rounds in a row reaches effective count 2k
    — they should catch up and start playing again once 2k exceeds the
    gap, well before 5 consecutive sit-outs."""
    amy = make_player("Amy", sit_out_count=0)
    bob = make_player("Bob", sit_out_count=0)
    regulars = [make_player(n, sit_out_count=5) for n in ["W", "X", "Y", "Z"]]
    players = [amy, bob] + regulars

    _, consecutive_log = simulate(players, num_to_play=4, num_rounds=12, seed=7)

    max_consecutive_amy = max(round_log["Amy"] for round_log in consecutive_log)
    max_consecutive_bob = max(round_log["Bob"] for round_log in consecutive_log)

    # Old flat-nudge behavior let this run for ~5 consecutive rounds before
    # recovering; the escalating fix should recover well before that.
    assert max_consecutive_amy < 5, f"Amy sat out {max_consecutive_amy} rounds in a row"
    assert max_consecutive_bob < 5, f"Bob sat out {max_consecutive_bob} rounds in a row"


def test_consecutive_sitouts_bounded_for_realistic_seeded_gap():
    """The prototype's own seed_sit_out_history seeds starting counts in
    0-4. Worst case within that range (gap of 4) should recover within 2
    consecutive sit-outs under the escalating nudge (2k >= 4 => k >= 2)."""
    amy = make_player("Amy", sit_out_count=0)
    regulars = [make_player(n, sit_out_count=4) for n in ["W", "X", "Y"]]
    players = [amy] + regulars  # 4 players, 1 court -> num_to_play=4 needs a 5th; use 6 total
    players += [make_player("Bob", sit_out_count=0), make_player("V", sit_out_count=4)]

    _, consecutive_log = simulate(players, num_to_play=4, num_rounds=10, seed=3)

    max_consecutive_amy = max(round_log["Amy"] for round_log in consecutive_log)
    assert max_consecutive_amy <= 2, f"Amy sat out {max_consecutive_amy} rounds in a row"


def test_large_gap_converges_without_crashing():
    """Extreme, unrealistic gap (20) — not expected in real seeded data,
    but the escalating nudge must still converge (not stay stuck forever)
    and must not crash or raise."""
    amy = make_player("Amy", sit_out_count=0)
    bob = make_player("Bob", sit_out_count=0)
    regulars = [make_player(n, sit_out_count=20) for n in ["W", "X", "Y", "Z"]]
    players = [amy, bob] + regulars

    _, consecutive_log = simulate(players, num_to_play=4, num_rounds=15, seed=1)

    # Amy/Bob must eventually get selected to play at least once within
    # the simulated window (consecutive streak drops back to 0 at some
    # point) — proves the escalation isn't stuck/broken, just slower for
    # an unrealistically large gap.
    assert any(round_log["Amy"] == 0 for round_log in consecutive_log)
    assert any(round_log["Bob"] == 0 for round_log in consecutive_log)
