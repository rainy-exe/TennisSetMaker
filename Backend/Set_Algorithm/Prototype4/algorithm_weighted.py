"""
algorithm_weighted.py

An ALTERNATIVE scoring engine to algorithm.py's lexicographic tuple chain.
Instead of a strict priority hierarchy (priority 2 always beats priority 3,
no matter how large the priority-3 difference), this module blends every
criterion into a single number using a normalized weighted sum:

    total_score = w1*mixed  + w2*partner  + w3*opponent  + w4*skill  + w5*court_pref

Each wN is a value from 0.0 to 1.0:
    0.0 = criterion switched off entirely (never influences the outcome)
    1.0 = maximum importance

This trades away the hard "priority 3 always beats priority 5" guarantee
that algorithm.py provides, in exchange for smooth, tunable trade-offs —
e.g. w4=0.3 lets a bit of skill imbalance be accepted in return for
noticeably better partner variety, rather than variety always winning
outright regardless of how small the difference is.

WHY NORMALIZATION MATTERS (read before touching MAX_* constants):
Raw component scores have very different natural ranges (mixed 0-2,
opponent repeats 0-4, skill spread 0-9, skill diff 0-18). Without dividing
each raw score by its own maximum first, a weight of 1.0 would NOT mean
the same amount of influence across criteria — skill_diff's much larger
raw range would silently dominate the sum even at "equal" weight.
Normalizing first is what makes the weight vector behave the way the
number actually suggests.

The single "skill" dial itself blends TWO signals internally (see
SKILL_SPREAD_SHARE/SKILL_DIFF_SHARE below): how similar the 4 players in
a foursome are to each other, and how balanced the two resulting team
sums are. This mirrors algorithm.py's lexicographic engine, which checks
spread before diff — see raw_skill_spread's docstring for why that
ordering is what makes players cluster by similar skill instead of only
caring that team sums balance.

Sit-out ratio (priority 1) is NOT part of this weighted sum — it's still
handled beforehand at the round level by select_round_players() in
algorithm.py, exactly as in the lexicographic version. It's a property of
who's playing this round, not a property of a specific court pairing, so
it doesn't fit into a per-court score either way.

This module knows nothing about CSV files, printing, or user input.
"""

import itertools
from typing import List, Optional, Tuple
from helpers import Player
from algorithm import (
    raw_mixed_score,
    raw_partner_repeats,
    raw_opponent_repeats,
    raw_skill_spread,
    raw_skill_diff,
    raw_court_pref_score,
)
from refinement import refine_skill_balance

# Maximum possible value of each raw component, used to normalize every
# score into the 0-1 range before weighting. If the underlying scoring
# functions in algorithm.py ever change range, these must be updated too.
MAX_MIXED = 2          # at most both teams are non-mixed
MAX_PARTNER = 2         # at most both teams are repeat partnerships
MAX_OPPONENT = 4        # at most all 4 cross-team pairs have played before
MAX_SKILL_SPREAD = 9    # ratings 1-10; max-min across any foursome = 9
MAX_SKILL_DIFF = 18     # ratings 1-10; team sum range 2-20; max diff = 18
MAX_COURT_PREF = 1      # not implemented; placeholder so a future weight has a scale to normalize against

# The single "skill" dial is internally split between two signals: how
# similar the 4 players in the foursome are to each other (spread), and
# how balanced the resulting two team sums are (diff). Spread is weighted
# higher, mirroring the lexicographic engine's tuple order (spread is
# checked before diff there). Adjustable — not derived from anything, just
# a starting point pending feedback from real test runs.
SKILL_SPREAD_SHARE = 0.65
SKILL_DIFF_SHARE = 0.35


class WeightVector:
    """The five dials, in the same order as the Scope of Work's default
    priority preset (positions 2-6; sit-out ratio is position 1, handled
    separately). All values should be 0.0-1.0."""

    __slots__ = ("mixed", "partner", "opponent", "skill", "court_pref")

    def __init__(self, mixed: float, partner: float, opponent: float,
                 skill: float, court_pref: float = 0.0):
        for name, value in (("mixed", mixed), ("partner", partner),
                             ("opponent", opponent), ("skill", skill),
                             ("court_pref", court_pref)):
            if not (0.0 <= value <= 1.0):
                raise ValueError(f"Weight '{name}' must be between 0.0 and 1.0, got {value}")
        self.mixed = mixed
        self.partner = partner
        self.opponent = opponent
        self.skill = skill
        self.court_pref = court_pref

    def as_tuple(self):
        return (self.mixed, self.partner, self.opponent, self.skill, self.court_pref)

    def __repr__(self):
        return (f"WeightVector(mixed={self.mixed}, partner={self.partner}, "
                f"opponent={self.opponent}, skill={self.skill}, court_pref={self.court_pref})")


def _weighted_score(team_a: Tuple[Player, Player], team_b: Tuple[Player, Player],
                     weights: WeightVector) -> float:
    """Normalizes each raw component to 0-1, applies its weight, sums.
    Lower is still better, consistent with algorithm.py's convention."""
    norm_mixed = raw_mixed_score(team_a, team_b) / MAX_MIXED
    norm_partner = raw_partner_repeats(team_a, team_b) / MAX_PARTNER
    norm_opponent = raw_opponent_repeats(team_a, team_b) / MAX_OPPONENT
    norm_skill_spread = raw_skill_spread(team_a + team_b) / MAX_SKILL_SPREAD
    norm_skill_diff = raw_skill_diff(team_a, team_b) / MAX_SKILL_DIFF
    norm_skill = SKILL_SPREAD_SHARE * norm_skill_spread + SKILL_DIFF_SHARE * norm_skill_diff
    norm_court_pref = raw_court_pref_score() / MAX_COURT_PREF if MAX_COURT_PREF else 0.0

    return (
        weights.mixed * norm_mixed
        + weights.partner * norm_partner
        + weights.opponent * norm_opponent
        + weights.skill * norm_skill
        + weights.court_pref * norm_court_pref
    )


def _score_candidate_weighted(four: Tuple[Player, Player, Player, Player], weights: WeightVector):
    """Same idea as algorithm.py's _score_candidate: tries all 3 possible
    team splits of the foursome and keeps whichever minimizes the weighted
    total. Returns (score, team_a, team_b)."""
    p1, p2, p3, p4 = four
    splits = [
        ((p1, p2), (p3, p4)),
        ((p1, p3), (p2, p4)),
        ((p1, p4), (p2, p3)),
    ]
    best = None
    for team_a, team_b in splits:
        score = _weighted_score(team_a, team_b, weights)
        if best is None or score < best[0]:
            best = (score, team_a, team_b)
    return best


def assign_courts_for_round_weighted(
    playing: List[Player], num_courts: int, weights: WeightVector,
    refine_max_iterations: Optional[int] = None,
):
    """Weighted-sum equivalent of algorithm.py's assign_courts_for_round.
    Same greedy court-by-court strategy: generate every candidate foursome
    from the remaining pool, score each, assign the best, remove those
    players, repeat. Only the scoring function differs from the
    lexicographic version — the assignment strategy is identical, so
    results from both modules are directly comparable for the same input.

    Also runs the same bounded local-search refinement pass as the
    lexicographic engine (see refinement.py / algorithm.py's
    assign_courts_for_round for the full rationale) to flatten the
    greedy fill's court-position skill-spread gradient, gated so a swap
    never regresses mixed/partner/opponent quality for any criterion
    whose weight is actually switched on. `refine_max_iterations` is
    exposed mainly for testing.

    Returns the same result shape as algorithm.py's version:
    [{court, team_a, team_b, players, team_a_sum, team_b_sum}, ...]
    """
    remaining = list(playing)
    results = []

    for court_num in range(1, num_courts + 1):
        if len(remaining) < 4:
            break

        best_overall = None
        for four in itertools.combinations(remaining, 4):
            score, team_a, team_b = _score_candidate_weighted(four, weights)
            if best_overall is None or score < best_overall[0]:
                best_overall = (score, four, team_a, team_b)

        _, four, team_a, team_b = best_overall
        a_sum = team_a[0].rating + team_a[1].rating
        b_sum = team_b[0].rating + team_b[1].rating

        results.append({
            "court": court_num,
            "team_a": team_a,
            "team_b": team_b,
            "players": list(four),
            "team_a_sum": a_sum,
            "team_b_sum": b_sum,
        })

        for p in four:
            remaining.remove(p)

    def _priority_quality(team_a, team_b):
        # A criterion switched fully off (weight 0) must never block an
        # otherwise-legal skill-improving swap just because that
        # criterion's raw value happened to change.
        return (
            raw_mixed_score(team_a, team_b) if weights.mixed > 0 else 0,
            raw_partner_repeats(team_a, team_b) if weights.partner > 0 else 0,
            raw_opponent_repeats(team_a, team_b) if weights.opponent > 0 else 0,
        )

    def _best_split(four):
        return _score_candidate_weighted(four, weights)

    results = refine_skill_balance(
        results, priority_quality_fn=_priority_quality, best_split_fn=_best_split,
        skill_spread_fn=raw_skill_spread, max_iterations=refine_max_iterations,
    )

    for r in results:
        team_a, team_b = r["team_a"], r["team_b"]
        team_a[0].partners_played.add(team_a[1].player_id)
        team_a[1].partners_played.add(team_a[0].player_id)
        team_b[0].partners_played.add(team_b[1].player_id)
        team_b[1].partners_played.add(team_b[0].player_id)
        for pa in team_a:
            for pb in team_b:
                pa.opponents_played.add(pb.player_id)
                pb.opponents_played.add(pa.player_id)
        for p in r["players"]:
            p.sessions_played += 1

    return results
