"""Tests for the skill-pairing fix: players should be clustered by similar
skill onto the same court first (raw_skill_spread), with the existing
between-team balance (raw_skill_diff) only deciding the fallback split when
the pool genuinely forces a wide-spread foursome together."""

import random

import pytest

from helpers import Player
from algorithm import (
    raw_skill_spread,
    _score_candidate,
    assign_courts_for_round,
)
from algorithm_weighted import (
    WeightVector,
    assign_courts_for_round_weighted,
)


def make_player(name: str, rating: int, gender: str = "MALE") -> Player:
    return Player(name=name, gender=gender, rating=rating)


def ratings_of(team) -> set:
    return {p.rating for p in team}


def test_raw_skill_spread_basic_range():
    assert raw_skill_spread([make_player("a", 1), make_player("b", 10),
                             make_player("c", 5), make_player("d", 5)]) == 9
    assert raw_skill_spread([make_player("a", 5)] * 4) == 0
    for _ in range(20):
        ratings = [random.randint(1, 10) for _ in range(4)]
        four = [make_player(str(i), r) for i, r in enumerate(ratings)]
        spread = raw_skill_spread(four)
        assert 0 <= spread <= 9
        assert spread == max(ratings) - min(ratings)


def test_fallback_split_preserved_in_isolation():
    """Given exactly the foursome {1, 2, 8, 9}, the chosen split must still
    be (2,8) vs (1,9) — proving raw_skill_spread (constant across splits)
    can't override raw_skill_diff's choice of split."""
    p1, p2, p8, p9 = (make_player("p1", 1), make_player("p2", 2),
                      make_player("p8", 8), make_player("p9", 9))
    score, team_a, team_b = _score_candidate((p1, p2, p8, p9), mixed_mode=0, variety_mode=2)
    chosen = {frozenset(ratings_of(team_a)), frozenset(ratings_of(team_b))}
    assert chosen == {frozenset({2, 8}), frozenset({1, 9})}


@pytest.mark.parametrize("order_seed", [0, 1, 2, 3])
def test_skill_clustering_beats_sum_balanced_mismatch(order_seed):
    """Pool of four 5-rated players plus the extreme {1,2,8,9} group.
    The four 5s should be grouped onto one court; the extremes are forced
    onto the other, with the (2,8) vs (1,9) fallback split preserved.
    Result must not depend on the input list order.
    Prototype4: run with the skill guardrails OFF -- this checks the
    Prototype3 clustering mechanism, which is unchanged underneath. With
    the guardrails on, Prototype4 deliberately breaks up the perfect court
    to avoid leaving a court with a 9-point range (see
    test_prototype4_guardrails.py::test_guardrails_share_out_extremes)."""
    players = [
        make_player("five_a", 5), make_player("five_b", 5),
        make_player("five_c", 5), make_player("five_d", 5),
        make_player("one", 1), make_player("two", 2),
        make_player("eight", 8), make_player("nine", 9),
    ]
    random.Random(order_seed).shuffle(players)

    results = assign_courts_for_round(players, num_courts=2, mixed_mode=0, variety_mode=2,
                                      max_team_diff=None, max_court_spread=None)
    assert len(results) == 2

    fives_court = next((r for r in results if all(p.rating == 5 for p in r["players"])), None)
    extremes_court = next((r for r in results if r is not fives_court), None)
    assert fives_court is not None, f"Expected a court of all-5s, got: {[ratings_of(r['players']) for r in results]}"
    assert ratings_of(extremes_court["players"]) == {1, 2, 8, 9}

    chosen = {frozenset(ratings_of(extremes_court["team_a"])),
              frozenset(ratings_of(extremes_court["team_b"]))}
    assert chosen == {frozenset({2, 8}), frozenset({1, 9})}


def test_skewed_small_pool_minimizes_spread_without_crashing():
    """Four rating-2 players plus two rating-1s and one 9, one 10 (8 total).
    The four 2s should cluster onto court 1 (spread 0); the leftover
    {1,1,9,10} is forced onto court 2, where the between-team balance
    should still pick the fairer split ((1,9) vs (1,10), diff=1) over the
    much worse (1,1) vs (9,10) (diff=17).
    Prototype4: run with the skill guardrails OFF -- this checks the
    Prototype3 clustering mechanism, which is unchanged underneath. With
    the guardrails on, Prototype4 deliberately breaks up the perfect court
    to avoid leaving a court with a 9-point range (see
    test_prototype4_guardrails.py::test_guardrails_share_out_extremes)."""
    players = [
        make_player("two_a", 2), make_player("two_b", 2),
        make_player("two_c", 2), make_player("two_d", 2),
        make_player("one_a", 1), make_player("one_b", 1),
        make_player("nine", 9), make_player("ten", 10),
    ]
    results = assign_courts_for_round(players, num_courts=2, mixed_mode=0, variety_mode=2,
                                      max_team_diff=None, max_court_spread=None)
    assert len(results) == 2

    twos_court = next((r for r in results if all(p.rating == 2 for p in r["players"])), None)
    assert twos_court is not None
    leftover_court = next(r for r in results if r is not twos_court)
    assert ratings_of(leftover_court["players"]) == {1, 1, 9, 10}
    assert abs(leftover_court["team_a_sum"] - leftover_court["team_b_sum"]) == 1


def test_weighted_engine_prefers_clustering_when_skill_weighted():
    players = [
        make_player("five_a", 5), make_player("five_b", 5),
        make_player("five_c", 5), make_player("five_d", 5),
        make_player("one", 1), make_player("two", 2),
        make_player("eight", 8), make_player("nine", 9),
    ]
    weights = WeightVector(mixed=0.0, partner=0.0, opponent=0.0, skill=1.0, court_pref=0.0)
    results = assign_courts_for_round_weighted(players, num_courts=2, weights=weights)
    assert len(results) == 2
    fives_court = next((r for r in results if all(p.rating == 5 for p in r["players"])), None)
    assert fives_court is not None


def test_weighted_engine_skill_disabled_does_not_crash():
    players = [
        make_player("five_a", 5), make_player("five_b", 5),
        make_player("five_c", 5), make_player("five_d", 5),
        make_player("one", 1), make_player("two", 2),
        make_player("eight", 8), make_player("nine", 9),
    ]
    weights = WeightVector(mixed=1.0, partner=0.0, opponent=0.0, skill=0.0, court_pref=0.0)
    results = assign_courts_for_round_weighted(players, num_courts=2, weights=weights)
    assert len(results) == 2
    assert all(len(r["players"]) == 4 for r in results)
