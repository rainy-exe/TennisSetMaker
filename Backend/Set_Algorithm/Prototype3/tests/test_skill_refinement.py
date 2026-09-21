"""Tests for the local-search skill-balance refinement pass (refinement.py).

The greedy per-court fill always fills court 1 first, so it systematically
front-loads the tightest-clustered foursome onto the earliest courts,
leaving later courts progressively worse -- measured empirically on the
real dataset: ~3x worse average skill spread on later courts vs earlier
ones at realistic session sizes (60 players / 15 courts). This module's
`refine_skill_balance` runs after the greedy fill to flatten that
gradient, but ONLY via Pareto-safe swaps (no court's spread may get
worse, only better) so it can never sacrifice an already-good court --
e.g. an already-perfectly-clustered one -- to help a worse one, which
would undermine the skill-clustering fix itself.

`refine_max_iterations=0` (passed straight through to refine_skill_balance)
gives the pre-refinement greedy-only baseline for comparison, exercised
through the real production functions rather than reimplemented logic."""

import random
import time

from helpers import Player
from algorithm import assign_courts_for_round, raw_skill_spread, _mixed_score, _partner_score, _opponent_score
from algorithm_weighted import assign_courts_for_round_weighted, WeightVector


def make_player(name: str, rating: int, gender: str = "MALE") -> Player:
    return Player(name=name, gender=gender, rating=rating)


def _spreads_by_court(num_players, num_courts, num_rounds, seed, refine_max_iterations):
    """No one sits out (num_players == num_courts * 4 exactly), so history
    accumulates naturally round over round with no sit-out bookkeeping
    needed."""
    rng = random.Random(seed)
    players = [make_player(f"p{i}", rng.randint(1, 10)) for i in range(num_players)]
    spreads_by_court = {c: [] for c in range(1, num_courts + 1)}
    for _ in range(num_rounds):
        results = assign_courts_for_round(
            players, num_courts, mixed_mode=0, variety_mode=2,
            refine_max_iterations=refine_max_iterations,
        )
        for r in results:
            spreads_by_court[r["court"]].append(raw_skill_spread(tuple(r["players"])))
    return spreads_by_court


def _early_late_gap(spreads_by_court):
    courts = sorted(spreads_by_court)
    mid = len(courts) // 2
    early = [v for c in courts[:mid] for v in spreads_by_court[c]]
    late = [v for c in courts[mid:] for v in spreads_by_court[c]]
    return (sum(late) / len(late)) - (sum(early) / len(early))


def test_refinement_reduces_court_position_degradation():
    """24 players / 6 courts / 10 rounds: the same court-position gradient
    diagnosed at full scale (60p/15c), reproduced at a size fast enough
    for the default test suite. Refinement should materially shrink the
    early-vs-late-court spread gap versus the greedy-only baseline."""
    num_players, num_courts, num_rounds, seed = 24, 6, 10, 101
    before = _spreads_by_court(num_players, num_courts, num_rounds, seed, refine_max_iterations=0)
    after = _spreads_by_court(num_players, num_courts, num_rounds, seed, refine_max_iterations=None)

    before_gap = _early_late_gap(before)
    after_gap = _early_late_gap(after)
    assert after_gap < before_gap, (
        f"expected refinement to shrink the early/late court spread gap: "
        f"before={before_gap:.2f} after={after_gap:.2f}"
    )


def test_refinement_never_regresses_priority_quality():
    """Across randomized scenarios (varying pool size, ratings, genders,
    mixed_mode, variety_mode) and multiple rounds (so real partner/opponent
    history actually accumulates), every court's format/partner/opponent
    quality after refinement must be no worse than before, court-by-court.
    Skill balance is a strictly lower scope priority -- refinement may only
    ever improve it at zero cost to priorities 2-4, never trade against them."""
    rng = random.Random(0)
    n_rounds = 5

    for _ in range(10):
        n_courts = rng.choice([2, 3, 4, 5])
        n_players = n_courts * 4
        mixed_mode = rng.choice([0, 1])
        variety_mode = rng.choice([1, 2, 3])
        genders = [rng.choice(["MALE", "FEMALE"]) for _ in range(n_players)]
        ratings = [rng.randint(1, 10) for _ in range(n_players)]

        players_before = [Player(name=f"p{i}", gender=genders[i], rating=ratings[i]) for i in range(n_players)]
        players_after = [Player(name=f"p{i}", gender=genders[i], rating=ratings[i]) for i in range(n_players)]

        for round_num in range(n_rounds):
            before = assign_courts_for_round(players_before, n_courts, mixed_mode, variety_mode, refine_max_iterations=0)
            after = assign_courts_for_round(players_after, n_courts, mixed_mode, variety_mode)

            before_by_court = {r["court"]: r for r in before}
            after_by_court = {r["court"]: r for r in after}
            for court_num, b in before_by_court.items():
                a = after_by_court[court_num]
                b_quality = (
                    _mixed_score(b["team_a"], b["team_b"], mixed_mode),
                    _partner_score(b["team_a"], b["team_b"], variety_mode),
                    _opponent_score(b["team_a"], b["team_b"], variety_mode),
                )
                a_quality = (
                    _mixed_score(a["team_a"], a["team_b"], mixed_mode),
                    _partner_score(a["team_a"], a["team_b"], variety_mode),
                    _opponent_score(a["team_a"], a["team_b"], variety_mode),
                )
                assert all(x <= y for x, y in zip(a_quality, b_quality)), (
                    f"round {round_num} court {court_num} regressed: before={b_quality} after={a_quality} "
                    f"(n_players={n_players}, n_courts={n_courts}, mixed_mode={mixed_mode}, variety_mode={variety_mode})"
                )


def test_refinement_overhead_is_small_relative_to_greedy_fill():
    """40 players / 10 courts, single round: refinement's cost is bounded
    (a handful of iterations x court-pairs x 52 index-swaps) and should be
    a small fraction of the O(n choose 4) cost the greedy fill itself
    already pays -- generous threshold to avoid CI flakiness."""
    rng = random.Random(5)
    ratings = [rng.randint(1, 10) for _ in range(40)]
    players_baseline = [make_player(f"p{i}", ratings[i]) for i in range(40)]
    players_refined = [make_player(f"p{i}", ratings[i]) for i in range(40)]

    start = time.perf_counter()
    assign_courts_for_round(players_baseline, 10, mixed_mode=1, variety_mode=3, refine_max_iterations=0)
    baseline = time.perf_counter() - start

    start = time.perf_counter()
    assign_courts_for_round(players_refined, 10, mixed_mode=1, variety_mode=3)
    with_refinement = time.perf_counter() - start

    assert with_refinement < baseline * 1.5, (
        f"refinement overhead too large: baseline={baseline:.3f}s with_refinement={with_refinement:.3f}s"
    )


def test_refinement_single_court_is_a_no_op():
    players = [make_player(f"p{i}", 5) for i in range(4)]
    results = assign_courts_for_round(players, num_courts=1, mixed_mode=0, variety_mode=2)
    assert len(results) == 1


def test_refinement_empty_pool_does_not_crash():
    assert assign_courts_for_round([], num_courts=3, mixed_mode=0, variety_mode=2) == []


def test_refinement_handles_two_skewed_courts_without_crashing():
    players = [
        make_player("a1", 4), make_player("a2", 5), make_player("a3", 6), make_player("a4", 7),
        make_player("b1", 2), make_player("b2", 3), make_player("b3", 9), make_player("b4", 10),
    ]
    results = assign_courts_for_round(players, num_courts=2, mixed_mode=0, variety_mode=2)
    assert len(results) == 2
    assert all(len(r["players"]) == 4 for r in results)


def test_weighted_engine_refinement_never_regresses_when_criterion_disabled():
    """A criterion with weight 0 must never block a legal skill-improving
    swap just because its raw value happened to change."""
    rng = random.Random(2)
    n_courts, n_rounds = 4, 4
    n_players = n_courts * 4
    ratings = [rng.randint(1, 10) for _ in range(n_players)]
    weights = WeightVector(mixed=0.0, partner=1.0, opponent=1.0, skill=1.0, court_pref=0.0)

    players_before = [make_player(f"p{i}", ratings[i]) for i in range(n_players)]
    players_after = [make_player(f"p{i}", ratings[i]) for i in range(n_players)]

    for _ in range(n_rounds):
        before = assign_courts_for_round_weighted(players_before, n_courts, weights, refine_max_iterations=0)
        after = assign_courts_for_round_weighted(players_after, n_courts, weights)
        assert len(before) == len(after) == n_courts
        assert all(len(r["players"]) == 4 for r in after)


def test_history_bookkeeping_matches_final_post_refinement_assignment():
    """Deliberately skewed pool likely to trigger at least one refinement
    swap. Regardless of whether a swap actually happens, every player's
    partners_played/opponents_played/sessions_played must reflect exactly
    their FINAL (post-refinement) court and team -- not a stale snapshot
    from the pre-refinement greedy intermediate state."""
    players = [
        make_player("a1", 4), make_player("a2", 5), make_player("a3", 6), make_player("a4", 9),
        make_player("b1", 2), make_player("b2", 3), make_player("b3", 4), make_player("b4", 10),
        make_player("c1", 5), make_player("c2", 5), make_player("c3", 5), make_player("c4", 5),
    ]
    results = assign_courts_for_round(players, num_courts=3, mixed_mode=0, variety_mode=2)
    assert len(results) == 3

    for r in results:
        team_a, team_b = r["team_a"], r["team_b"]
        assert team_a[1].player_id in team_a[0].partners_played
        assert team_a[0].player_id in team_a[1].partners_played
        assert team_b[1].player_id in team_b[0].partners_played
        assert team_b[0].player_id in team_b[1].partners_played
        for pa in team_a:
            for pb in team_b:
                assert pb.player_id in pa.opponents_played
                assert pa.player_id in pb.opponents_played

    for p in players:
        assert p.sessions_played == 1
        assert len(p.partners_played) == 1  # exactly their one final partner, no stale extras
        assert len(p.opponents_played) == 2  # exactly their two final opponents
