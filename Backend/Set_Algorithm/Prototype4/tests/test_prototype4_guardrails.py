"""Prototype4: skill guardrails above variety, gender policy as the lock,
and native same-gender mode.

The headline regression case is the real one from the 50-day round-robin
simulation: Mark L (5) + Gerry K (7) vs Ceclie L (2) + Suzanne B (2), a
12-vs-4 match (diff 8) that Prototype3 chose because the balanced splits
would have repeated an opponent pairing."""

import copy
import random

from helpers import Player
from algorithm import (
    _mixed_score,
    _score_candidate,
    assign_courts_for_round,
    raw_same_gender_score,
    raw_skill_diff,
    raw_skill_spread,
    raw_team_diff_excess,
    raw_court_spread_excess,
    DEFAULT_MAX_TEAM_DIFF,
    DEFAULT_MAX_COURT_SPREAD,
    GENDER_MIXED,
    GENDER_SAME,
)


def P(name, rating, gender="MALE"):
    return Player(name=name, gender=gender, rating=rating)


def _team_ratings(team_a, team_b):
    return {frozenset(p.rating for p in team_a), frozenset(p.rating for p in team_b)}


def _real_blowout_foursome():
    """Opponent history that makes the lopsided split the ONLY split with
    zero opponent repeats (this is what happened in the real run)."""
    mark, gerry = P("Mark L", 5), P("Gerry K", 7)
    ceclie, suzanne = P("Ceclie L", 2, "FEMALE"), P("Suzanne B", 2, "FEMALE")
    mark.opponents_played.add(gerry.player_id)
    gerry.opponents_played.add(mark.player_id)
    ceclie.opponents_played.add(suzanne.player_id)
    suzanne.opponents_played.add(ceclie.player_id)
    return mark, gerry, ceclie, suzanne


def test_prototype3_behaviour_reproduced_when_guardrails_off():
    """With both guardrails switched off we get Prototype3's choice back:
    variety wins, and the match is 12 vs 4."""
    four = _real_blowout_foursome()
    _, team_a, team_b = _score_candidate(four, 0, 3, max_team_diff=None, max_court_spread=None)
    assert raw_skill_diff(team_a, team_b) == 8


def test_guardrail_prevents_the_real_diff_8_blowout():
    four = _real_blowout_foursome()
    _, team_a, team_b = _score_candidate(four, 0, 3)
    assert raw_skill_diff(team_a, team_b) <= DEFAULT_MAX_TEAM_DIFF
    # 5+2 vs 7+2 -- accepts a repeat opponent instead of a 12-vs-4 match
    assert _team_ratings(team_a, team_b) == {frozenset({5, 2}), frozenset({7, 2})}


def test_guardrails_are_silent_inside_the_limits():
    """When every option is within the limits, variety decides exactly as
    before: a fresh partnership is chosen over a 0-diff repeat."""
    a, b, c, d = P("a", 5), P("b", 5), P("c", 4), P("d", 6)
    # a+b have partnered before; the two no-repeat splits pair a with c or d.
    a.partners_played.add(b.player_id)
    b.partners_played.add(a.player_id)
    _, team_a, team_b = _score_candidate((a, b, c, d), 0, 2)
    for team in (team_a, team_b):
        ids = {p.player_id for p in team}
        assert ids != {"a", "b"}
    # identical choice with guardrails off
    off = _score_candidate((a, b, c, d), 0, 2, max_team_diff=None, max_court_spread=None)
    on = _score_candidate((a, b, c, d), 0, 2)
    assert {frozenset(p.player_id for p in t) for t in off[1:]} == {frozenset(p.player_id for p in t) for t in on[1:]}


def test_gender_lock_outranks_skill_guardrail():
    """Mixed mode: M5, M5, F2, F7. The only fully-mixed split is (M5,F2) vs
    (M5,F7), diff 5 -- over the limit -- while (M5,M5) vs (F2,F7) would be
    diff 1. The gender policy is the lock, so the mixed split must win."""
    m1, m2 = P("m1", 5, "MALE"), P("m2", 5, "MALE")
    f1, f2 = P("f1", 2, "FEMALE"), P("f2", 7, "FEMALE")
    score, team_a, team_b = _score_candidate((m1, m2, f1, f2), GENDER_MIXED, 2)
    assert score[0] == 0
    for team in (team_a, team_b):
        assert {p.gender for p in team} == {"MALE", "FEMALE"}


def test_same_gender_score_ordering():
    mm = (P("m1", 5, "MALE"), P("m2", 5, "MALE"))
    ff = (P("f1", 5, "FEMALE"), P("f2", 5, "FEMALE"))
    mf1 = (P("m3", 5, "MALE"), P("f3", 5, "FEMALE"))
    mf2 = (P("m4", 5, "MALE"), P("f4", 5, "FEMALE"))
    m34 = (P("m5", 5, "MALE"), P("m6", 5, "MALE"))
    assert raw_same_gender_score(mm, m34) == 0                       # all-male court
    assert raw_same_gender_score(mm, mf1) == 4                       # 3+1
    assert raw_same_gender_score(mm, ff) < raw_same_gender_score(mf1, mf2)  # MMvFF beats MFvMF


def test_same_gender_mode_builds_single_gender_courts():
    rng = random.Random(7)
    players = [P(f"m{i}", rng.randint(3, 6), "MALE") for i in range(8)] + \
              [P(f"f{i}", rng.randint(3, 6), "FEMALE") for i in range(8)]
    for _ in range(4):
        results = assign_courts_for_round(players, 4, GENDER_SAME, 2)
        for r in results:
            assert len({p.gender for p in r["players"]}) == 1


def test_guardrails_hold_across_realistic_rounds():
    """24 players with a club-like rating spread, 12 rounds of accumulating
    history under round-robin bias. Every court must stay within both
    limits when the pool makes that possible (this pool does)."""
    ratings = [2, 3, 3, 3, 4, 4, 4, 4, 4, 5, 5, 5, 5, 5, 5, 5, 5, 6, 6, 6, 6, 7, 7, 7]
    rng = random.Random(3)
    players = [P(f"p{i}", r, rng.choice(["MALE", "FEMALE"])) for i, r in enumerate(ratings)]
    worst_diff = worst_spread = 0
    for _ in range(12):
        for r in assign_courts_for_round(players, 6, 0, 3):
            worst_diff = max(worst_diff, abs(r["team_a_sum"] - r["team_b_sum"]))
            worst_spread = max(worst_spread, raw_skill_spread(tuple(r["players"])))
    assert worst_diff <= DEFAULT_MAX_TEAM_DIFF
    assert worst_spread <= DEFAULT_MAX_COURT_SPREAD


def test_repair_and_refinement_never_regress_gender_or_total_guardrail_excess():
    """Steps 2-3 (repair + refinement) versus greedy-only, from identical
    history: the gender lock must never get worse on any court, and the
    round's total guardrail excess must never go up."""
    def total_excess(results):
        return (sum(raw_team_diff_excess(r["team_a"], r["team_b"], DEFAULT_MAX_TEAM_DIFF) for r in results),
                sum(raw_court_spread_excess(tuple(r["players"]), DEFAULT_MAX_COURT_SPREAD) for r in results))

    rng = random.Random(11)
    for _ in range(8):
        n_courts = rng.choice([2, 3, 4, 5])
        n = n_courts * 4
        ratings = [rng.randint(1, 10) for _ in range(n)]
        genders = [rng.choice(["MALE", "FEMALE"]) for _ in range(n)]
        mixed_mode, variety_mode = rng.choice([0, 1, 2]), rng.choice([1, 2, 3])
        after_players = [Player(name=f"p{i}", gender=genders[i], rating=ratings[i]) for i in range(n)]
        for _round in range(4):
            before_players = copy.deepcopy(after_players)  # identical starting history
            before = assign_courts_for_round(before_players, n_courts, mixed_mode, variety_mode, refine_max_iterations=0)
            after = assign_courts_for_round(after_players, n_courts, mixed_mode, variety_mode)
            assert total_excess(after) <= total_excess(before)
            # gender: compare the multiset of court scores (courts can trade players)
            b_g = sorted(_mixed_score(r["team_a"], r["team_b"], mixed_mode) for r in before)
            a_g = sorted(_mixed_score(r["team_a"], r["team_b"], mixed_mode) for r in after)
            assert sum(a_g) <= sum(b_g)


def test_guardrails_share_out_extremes():
    """Four 5s plus {1,2,8,9}. Prototype3 kept the perfect 5s court and put
    1, 2, 8 and 9 together (partners 1+9 and 2+8). Prototype4 shares the
    extremes out so no court has an 8-point range."""
    players = [P(f"five{i}", 5) for i in range(4)] + [P("one", 1), P("two", 2), P("eight", 8), P("nine", 9)]
    results = assign_courts_for_round(players, 2, 0, 2)
    assert max(raw_skill_spread(tuple(r["players"])) for r in results) <= 4
    assert max(abs(r["team_a_sum"] - r["team_b_sum"]) for r in results) <= DEFAULT_MAX_TEAM_DIFF


def test_backend_adapter_call_shape_still_works():
    """app/algorithm_adapter.py calls assign_courts_for_round(playing,
    n_courts, 0|1, 2) positionally -- must keep working unchanged."""
    players = [P(f"p{i}", 5) for i in range(8)]
    results = assign_courts_for_round(players, 2, 1, 2)
    assert len(results) == 2
