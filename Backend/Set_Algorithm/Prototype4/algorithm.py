"""
algorithm.py  (Prototype4)

The set-matching algorithm: priority blocks applied as a lexicographic
scoring chain. Python compares tuples left-to-right, so the tuple order in
_score_candidate IS the priority hierarchy.

What changed from Prototype3, and why
-------------------------------------
Prototype3 ranked skill balance BELOW partner/opponent variety, exactly as
the Scope of Work's default preset lists them. In long simulations that
produced courts like (5+7) vs (2+2) -- a 12-vs-4 match, rating diff 8 --
whenever avoiding a repeat opponent was "worth more" than a fair match.
Measured on a 25-player / 6-court / 6-round / 50-day run, ~19% of courts
were not on their best available split and most of those were on their
WORST split.

The client's priorities for a social club are:
  1. Gender policy (open / mixed / same-gender) behaves as a lock.
  2. Matches are never lopsided, and the four players on a court are
     roughly the same standard (partners close in skill AND the two teams
     close in skill -- not "two 2s vs a 5 and a 7").
  3. Within those limits, partners and opponents rotate as much as possible.

So Prototype4 adds two skill GUARDRAILS that sit above variety, but only
bite when a limit is exceeded:

    (gender_score,            1. lock: open / mixed / same-gender
     team_diff_excess,        2. guardrail: how far the team-sum gap is OVER max_team_diff
     court_spread_excess,     3. guardrail: how far the court's rating range is OVER max_court_spread
     partner_score,           4. rotate partners
     opponent_score,          5. rotate opponents
     skill_spread,            6. then: tightest-possible court
     skill_diff,              7. then: most even split
     court_pref)              8. not implemented (always 0)

Inside the limits both guardrails score 0, so variety decides exactly as it
did in Prototype3 -- rotation is still the main driver of who plays whom.
A guardrail only overrides variety when the alternative would be a match
outside the limit, and it is graded (excess, not pass/fail), so when no
option fits the limit the least-bad one is still chosen rather than
failing.

This is a deliberate deviation from the Scope of Work's documented default
order (variety 3/4 above skill 5) and needs client sign-off -- see
README.md "Prototype4 changes".

Sit-out fairness (priority 1 in the Scope of Work) is unchanged: it is
decided before court scoring, in select_round_players.

This module knows nothing about CSV files, printing, or user input.
"""

import itertools
import random
from typing import List, Optional, Tuple
from helpers import Player
from refinement import refine_skill_balance, repair_guardrails

BIG_PENALTY = 10_000  # used by variety_mode=1 (hard avoid) to dominate the score

# Gender policy values (the `mixed_mode` argument, name kept for backend compatibility)
GENDER_OPEN = 0
GENDER_MIXED = 1
GENDER_SAME = 2

# Skill guardrails. Ratings are 1-10; team sums are 2-20.
#   max_team_diff=2     -> e.g. 9 vs 11 is fine, 8 vs 11 is over the limit
#   max_court_spread=3  -> e.g. a court of 3,4,5,6 is fine, 2,3,5,6 is over
# Pass None to switch a guardrail off (Prototype3 behaviour for that term).
DEFAULT_MAX_TEAM_DIFF = 2
DEFAULT_MAX_COURT_SPREAD = 3


# ---------------------------------------------------------------------------
# Sit-out ratio (round-level, not per-court) -- unchanged from Prototype3
# ---------------------------------------------------------------------------

def select_round_players(active_pool: List[Player], num_to_play: int, rng: random.Random) -> Tuple[List[Player], List[Player]]:
    """Chooses who plays this round vs who sits out, based on sit-out
    fairness: players who have sat out MORE in the past get priority to
    play now; players who have sat out LEAST are the fairest candidates
    to sit out this round (their count then rises to catch up).

    A player's `consecutive_sit_outs` streak adds a soft, escalating
    penalty to their effective count, so nobody gets stuck sitting out
    round after round. Ties are broken by shuffling with the run's seeded
    `rng` before a stable sort (reproducible, never biased toward a name).
    """
    def effective_count(p: Player) -> int:
        return p.sit_out_count + p.consecutive_sit_outs

    shuffled = list(active_pool)
    rng.shuffle(shuffled)
    ranked = sorted(shuffled, key=lambda p: -effective_count(p))
    playing = ranked[:num_to_play]
    sitting_out = ranked[num_to_play:]
    return playing, sitting_out


# ---------------------------------------------------------------------------
# Gender policy (the lock)
# ---------------------------------------------------------------------------

def raw_mixed_score(team_a: Tuple[Player, Player], team_b: Tuple[Player, Player]) -> int:
    """0 = both teams are one MALE + one FEMALE, 1 = one team isn't,
    2 = neither team is."""
    penalty = 0
    for team in (team_a, team_b):
        genders = {team[0].gender, team[1].gender}
        if len(genders) != 2:
            penalty += 1
    return penalty


def raw_same_gender_score(team_a: Tuple[Player, Player], team_b: Tuple[Player, Player]) -> int:
    """Same-gender policy (men's / ladies' doubles). 0 = all four players
    on the court share a gender. Otherwise, primarily penalises how many
    players are in the court's minority gender, then breaks ties by how
    many TEAMS are mixed -- so when the pool forces a 2M+2F court, MM vs FF
    (6) is preferred over MF vs MF (8), and a 3+1 court scores 4."""
    four = team_a + team_b
    counts = {}
    for p in four:
        counts[p.gender] = counts.get(p.gender, 0) + 1
    minority = 4 - max(counts.values())
    mixed_teams = sum(1 for team in (team_a, team_b) if team[0].gender != team[1].gender)
    return 3 * minority + mixed_teams


def _mixed_score(team_a: Tuple[Player, Player], team_b: Tuple[Player, Player], mixed_mode: int) -> int:
    """Gender-policy score. mixed_mode: 0 = open (always 0), 1 = enforce
    mixed pairs, 2 = same-gender courts. Name kept from Prototype3 so
    existing imports keep working."""
    if mixed_mode == GENDER_MIXED:
        return raw_mixed_score(team_a, team_b)
    if mixed_mode == GENDER_SAME:
        return raw_same_gender_score(team_a, team_b)
    return 0


_gender_score = _mixed_score  # clearer alias for new code


# ---------------------------------------------------------------------------
# Social variety -- unique partners / unique opponents (unchanged)
# ---------------------------------------------------------------------------

def raw_partner_repeats(team_a: Tuple[Player, Player], team_b: Tuple[Player, Player]) -> int:
    """Count of teams (0, 1, or 2) whose two players have partnered before."""
    repeats = 0
    for team in (team_a, team_b):
        if team[1].player_id in team[0].partners_played:
            repeats += 1
    return repeats


def raw_opponent_repeats(team_a: Tuple[Player, Player], team_b: Tuple[Player, Player]) -> int:
    """Count of cross-team pairs (0-4) who've faced each other before."""
    repeats = 0
    for pa in team_a:
        for pb in team_b:
            if pb.player_id in pa.opponents_played:
                repeats += 1
    return repeats


def raw_connection_penalty(team_a: Tuple[Player, Player], team_b: Tuple[Player, Player]) -> int:
    """Count of all 6 pairs within the foursome who share ANY prior history
    (partner or opponent). Used by round-robin bias (variety_mode=3)."""
    four = team_a + team_b
    penalty = 0
    for p1, p2 in itertools.combinations(four, 2):
        if p2.player_id in p1.partners_played or p2.player_id in p1.opponents_played:
            penalty += 1
    return penalty


def _partner_score(team_a: Tuple[Player, Player], team_b: Tuple[Player, Player], variety_mode: int) -> int:
    repeats = raw_partner_repeats(team_a, team_b)
    if variety_mode == 1:  # hard avoid
        return BIG_PENALTY * repeats
    return repeats


def _opponent_score(team_a: Tuple[Player, Player], team_b: Tuple[Player, Player], variety_mode: int) -> int:
    repeats = raw_opponent_repeats(team_a, team_b)
    if variety_mode == 1:  # hard avoid
        return BIG_PENALTY * repeats
    if variety_mode == 3:  # round-robin bias
        return repeats + raw_connection_penalty(team_a, team_b)
    return repeats


# ---------------------------------------------------------------------------
# Skill balance
# ---------------------------------------------------------------------------

def raw_skill_spread(four: Tuple[Player, Player, Player, Player]) -> int:
    """max-min rating across the FOURSOME (constant across its 3 splits).
    Shapes which 4 players get grouped onto a court."""
    ratings = [p.rating for p in four]
    return max(ratings) - min(ratings)


def raw_skill_diff(team_a: Tuple[Player, Player], team_b: Tuple[Player, Player]) -> int:
    """Absolute difference between the two teams' summed ratings."""
    a_sum = team_a[0].rating + team_a[1].rating
    b_sum = team_b[0].rating + team_b[1].rating
    return abs(a_sum - b_sum)


def raw_team_diff_excess(team_a, team_b, max_team_diff: Optional[int]) -> int:
    """Guardrail: how many rating points the team-sum gap is OVER the
    limit. 0 whenever the match is within the limit (so it never competes
    with variety for in-limit options), graded above it."""
    if max_team_diff is None:
        return 0
    return max(0, raw_skill_diff(team_a, team_b) - max_team_diff)


def raw_court_spread_excess(four, max_court_spread: Optional[int]) -> int:
    """Guardrail: how many rating points the court's range (highest minus
    lowest of the four) is OVER the limit. Keeps partners AND opponents all
    near the same standard."""
    if max_court_spread is None:
        return 0
    return max(0, raw_skill_spread(four) - max_court_spread)


# ---------------------------------------------------------------------------
# Court preferences -- not implemented in prototype (no data)
# ---------------------------------------------------------------------------

def raw_court_pref_score() -> int:
    return 0


# ---------------------------------------------------------------------------
# Candidate generation & scoring
# ---------------------------------------------------------------------------

def _score_candidate(
    four: Tuple[Player, Player, Player, Player], mixed_mode: int, variety_mode: int,
    max_team_diff: Optional[int] = DEFAULT_MAX_TEAM_DIFF,
    max_court_spread: Optional[int] = DEFAULT_MAX_COURT_SPREAD,
):
    """Returns (score_tuple, team_a, team_b) for the BEST of this foursome's
    3 possible team splits. The tuple order is the priority hierarchy."""
    p1, p2, p3, p4 = four
    splits = [
        ((p1, p2), (p3, p4)),
        ((p1, p3), (p2, p4)),
        ((p1, p4), (p2, p3)),
    ]
    skill_spread = raw_skill_spread(four)  # constant across splits
    spread_excess = raw_court_spread_excess(four, max_court_spread)
    best = None
    for team_a, team_b in splits:
        score = (
            _mixed_score(team_a, team_b, mixed_mode),
            raw_team_diff_excess(team_a, team_b, max_team_diff),
            spread_excess,
            _partner_score(team_a, team_b, variety_mode),
            _opponent_score(team_a, team_b, variety_mode),
            skill_spread,
            raw_skill_diff(team_a, team_b),
            raw_court_pref_score(),
        )
        if best is None or score < best[0]:
            best = (score, team_a, team_b)
    return best


def assign_courts_for_round(
    playing: List[Player], num_courts: int, mixed_mode: int, variety_mode: int,
    refine_max_iterations: Optional[int] = None,
    max_team_diff: Optional[int] = DEFAULT_MAX_TEAM_DIFF,
    max_court_spread: Optional[int] = DEFAULT_MAX_COURT_SPREAD,
):
    """Three steps:
      1. Greedily fill courts one at a time (best-scoring foursome from
         whoever is left).
      2. repair_guardrails: swap players between courts to fix any court
         the greedy fill left over a skill limit (refinement.py).
      3. refine_skill_balance: Prototype3's swap pass that evens out skill
         spread across courts without making the gender lock, guardrails or
         variety worse on any court it touches.
    `refine_max_iterations=0` switches steps 2 and 3 off (greedy only).

    Same call signature as Prototype3 for the first 5 arguments, so the
    backend adapter works unchanged; the guardrails are optional keyword
    arguments with club-friendly defaults.

    Returns a list of dicts: {court, team_a, team_b, players, team_a_sum, team_b_sum}
    """
    def _best_split(four):
        return _score_candidate(four, mixed_mode, variety_mode, max_team_diff, max_court_spread)

    remaining = list(playing)
    results = []

    for court_num in range(1, num_courts + 1):
        if len(remaining) < 4:
            break

        best_overall = None
        for four in itertools.combinations(remaining, 4):
            score, team_a, team_b = _best_split(four)
            if best_overall is None or score < best_overall[0]:
                best_overall = (score, four, team_a, team_b)

        _, four, team_a, team_b = best_overall
        results.append({
            "court": court_num,
            "team_a": team_a,
            "team_b": team_b,
            "players": list(four),
            "team_a_sum": team_a[0].rating + team_a[1].rating,
            "team_b_sum": team_b[0].rating + team_b[1].rating,
        })

        for p in four:
            remaining.remove(p)

    def _priority_quality(team_a, team_b):
        return (
            _mixed_score(team_a, team_b, mixed_mode),
            raw_team_diff_excess(team_a, team_b, max_team_diff),
            raw_court_spread_excess(team_a + team_b, max_court_spread),
            _partner_score(team_a, team_b, variety_mode),
            _opponent_score(team_a, team_b, variety_mode),
        )

    # Step 2: fix any court the greedy fill left over a skill limit (may
    # cost a repeat partner/opponent -- guardrails outrank variety).
    results = repair_guardrails(
        results,
        gender_fn=lambda a, b: _mixed_score(a, b, mixed_mode),
        guardrail_fn=lambda a, b: (
            raw_team_diff_excess(a, b, max_team_diff),
            raw_court_spread_excess(a + b, max_court_spread),
        ),
        variety_fn=lambda a, b: _partner_score(a, b, variety_mode) + _opponent_score(a, b, variety_mode),
        best_split_fn=_best_split,
        max_iterations=refine_max_iterations,
    )

    # Step 3: Prototype3's skill-spread refinement, which never makes the
    # gender lock, guardrails or variety worse on any court it touches.
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
