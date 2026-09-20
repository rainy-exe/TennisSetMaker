"""
algorithm.py

The actual set-matching algorithm: priority-weight blocks applied as a
lexicographic scoring chain, exactly mirroring the Scope of Work's
default preset order:

    1. Sit-out ratio            -> handled by round-level player selection,
                                    not per-court scoring (see select_round_players)
    2. Set format compliance    -> mixed_score
    3. Social variety: partners -> partner_score
    4. Social variety: opponents-> opponent_score
    5. Skill balance             -> skill_spread (which 4 players get grouped
                                    together) then skill_diff (how they're
                                    split into teams) — see raw_skill_spread
    6. Court preferences         -> not implemented (no data in prototype scope), always 0

Each candidate court (a foursome + a team split) is scored as a tuple.
Python compares tuples lexicographically, so the tuple order below IS
the priority hierarchy — changing the algorithm's priority order later
is a one-line change (reorder the tuple), not a rewrite.

This module knows nothing about CSV files, printing, or user input.
"""

import itertools
import random
from typing import List, Tuple
from helpers import Player

BIG_PENALTY = 10_000  # used by variety_mode=1 (hard avoid) to dominate the score


# ---------------------------------------------------------------------------
# Priority 1: Sit-out ratio (round-level, not per-court)
# ---------------------------------------------------------------------------

def select_round_players(active_pool: List[Player], num_to_play: int, rng: random.Random) -> Tuple[List[Player], List[Player]]:
    """Chooses who plays this round vs who sits out, based on sit-out
    fairness: players who have sat out MORE in the past get priority to
    play now; players who have sat out LEAST are the fairest candidates
    to sit out this round (their count then rises to catch up).

    A player's `consecutive_sit_outs` streak (rounds sat out in a row,
    reset to 0 the instant they play — see main.py's bookkeeping) adds a
    soft, ESCALATING penalty to their effective count: +1 after 1
    consecutive sit-out, +2 after 2, etc. This prevents a player from
    being stuck sitting out round after round just because their raw
    count started lower than everyone else's — the penalty grows the
    longer the streak runs, rather than a single flat nudge that gets
    swamped whenever the starting counts are unevenly spread. It's still
    soft, not a hard block, so it yields when truly unavoidable (e.g. too
    few players relative to needed sit-out slots).

    Ties in effective count are broken by shuffling the pool with the
    run's seeded `rng` before a stable sort, so the outcome is
    reproducible under a fixed seed but never structurally biased toward
    or against any particular player (a fixed tie-break key like name
    would otherwise let the same player lose every tie, every round).
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
# Priority 2: Set format compliance (mixed/non-mixed)
# ---------------------------------------------------------------------------

def raw_mixed_score(team_a: Tuple[Player, Player], team_b: Tuple[Player, Player]) -> int:
    """0 = both teams are one MALE + one FEMALE, 1 = one team isn't,
    2 = neither team is. Mode-independent — callers decide how much this
    number should matter (a hard toggle, or a weight)."""
    penalty = 0
    for team in (team_a, team_b):
        genders = {team[0].gender, team[1].gender}
        if len(genders) != 2:
            penalty += 1
    return penalty


def _mixed_score(team_a: Tuple[Player, Player], team_b: Tuple[Player, Player], mixed_mode: int) -> int:
    """Lexicographic-mode wrapper: mixed_mode=0 disables the constraint outright."""
    if mixed_mode == 0:
        return 0
    return raw_mixed_score(team_a, team_b)


# ---------------------------------------------------------------------------
# Priorities 3 & 4: Social variety — unique partners / unique opponents
# ---------------------------------------------------------------------------

def raw_partner_repeats(team_a: Tuple[Player, Player], team_b: Tuple[Player, Player]) -> int:
    """Count of teams (0, 1, or 2) whose two players have partnered before."""
    repeats = 0
    for team in (team_a, team_b):
        if team[1].name in team[0].partners_played:
            repeats += 1
    return repeats


def raw_opponent_repeats(team_a: Tuple[Player, Player], team_b: Tuple[Player, Player]) -> int:
    """Count of cross-team pairs (0-4) who've faced each other before."""
    repeats = 0
    for pa in team_a:
        for pb in team_b:
            if pb.name in pa.opponents_played:
                repeats += 1
    return repeats


def raw_connection_penalty(team_a: Tuple[Player, Player], team_b: Tuple[Player, Player]) -> int:
    """Count of all 6 pairs within the foursome (partner AND opponent pairs
    combined) who share ANY prior history together. Wider net than the two
    functions above — used by round-robin bias to favour completely fresh
    groupings, not just avoid direct repeats."""
    four = team_a + team_b
    penalty = 0
    for p1, p2 in itertools.combinations(four, 2):
        if p2.name in p1.partners_played or p2.name in p1.opponents_played:
            penalty += 1
    return penalty


def _partner_score(team_a: Tuple[Player, Player], team_b: Tuple[Player, Player], variety_mode: int) -> int:
    """Lexicographic-mode wrapper around raw_partner_repeats."""
    repeats = raw_partner_repeats(team_a, team_b)
    if variety_mode == 1:  # hard avoid
        return BIG_PENALTY * repeats
    return repeats  # modes 2 and 3 both use a soft count here; mode 3's extra bias is in opponent_score


def _opponent_score(team_a: Tuple[Player, Player], team_b: Tuple[Player, Player], variety_mode: int) -> int:
    """Lexicographic-mode wrapper around raw_opponent_repeats / raw_connection_penalty."""
    repeats = raw_opponent_repeats(team_a, team_b)
    if variety_mode == 1:  # hard avoid
        return BIG_PENALTY * repeats
    if variety_mode == 3:  # round-robin bias: also penalise ANY prior connection at all
        return repeats + raw_connection_penalty(team_a, team_b)
    return repeats  # mode 2: plain soft count


# ---------------------------------------------------------------------------
# Priority 5: Skill balance
# ---------------------------------------------------------------------------

def raw_skill_spread(four: Tuple[Player, Player, Player, Player]) -> int:
    """max-min rating across the candidate FOURSOME, irrespective of team
    split. This is what makes the algorithm prefer grouping similarly-rated
    players onto the same court (e.g. 5s with other 4/5/6s) instead of only
    caring that team sums balance — a foursome of {5,5,5,5} now clearly
    beats one of {2,8,1,9} even though both can produce sum-balanced teams.

    Deliberately constant across all 3 splits of a fixed foursome (it
    doesn't look at team_a/team_b at all), so it never overrides which
    split raw_skill_diff picks once 4 players are already chosen — it only
    shapes WHICH 4 players get proposed as a court in the first place.
    That's why a foursome that's genuinely forced to span a wide skill
    range (too few players at each level) still falls back to whatever
    raw_skill_diff finds best within it, matching the team's stated
    fallback example (team A: 2 & 8 vs team B: 1 & 9) exactly, with no
    special-case branching needed.

    Range: 0-9 for ratings 1-10.
    """
    ratings = [p.rating for p in four]
    return max(ratings) - min(ratings)


def raw_skill_diff(team_a: Tuple[Player, Player], team_b: Tuple[Player, Player]) -> int:
    """Absolute difference between the two teams' summed ratings. This is
    the 'competitiveness of the match' measure, applied only once a
    foursome is already chosen (see raw_skill_spread for how the foursome
    itself gets chosen) — no separate stacked/seeded-pairs objective, per
    team decision."""
    a_sum = team_a[0].rating + team_a[1].rating
    b_sum = team_b[0].rating + team_b[1].rating
    return abs(a_sum - b_sum)


# ---------------------------------------------------------------------------
# Priority 6: Court preferences -- not implemented in prototype (no data)
# ---------------------------------------------------------------------------

def raw_court_pref_score() -> int:
    return 0


# ---------------------------------------------------------------------------
# Candidate generation & scoring
# ---------------------------------------------------------------------------

def _score_candidate(four: Tuple[Player, Player, Player, Player], mixed_mode: int, variety_mode: int):
    """Returns (score_tuple, team_a, team_b) for the BEST team split of
    this foursome. Trying all 3 possible splits of 4 players into two
    teams of 2 and keeping the best-scoring one."""
    p1, p2, p3, p4 = four
    splits = [
        ((p1, p2), (p3, p4)),
        ((p1, p3), (p2, p4)),
        ((p1, p4), (p2, p3)),
    ]
    skill_spread = raw_skill_spread(four)  # constant across splits, computed once
    best = None
    for team_a, team_b in splits:
        score = (
            _mixed_score(team_a, team_b, mixed_mode),
            _partner_score(team_a, team_b, variety_mode),
            _opponent_score(team_a, team_b, variety_mode),
            skill_spread,
            raw_skill_diff(team_a, team_b),
            raw_court_pref_score(),
        )
        if best is None or score < best[0]:
            best = (score, team_a, team_b)
    return best


def assign_courts_for_round(playing: List[Player], num_courts: int, mixed_mode: int, variety_mode: int):
    """Greedily fills courts one at a time: generate all candidate
    foursomes from the remaining pool, score each (priorities 2-6),
    assign the best-scoring court, remove those players, repeat.

    Returns a list of dicts: {court, team_a, team_b, players, team_a_sum, team_b_sum}
    """
    remaining = list(playing)
    results = []

    for court_num in range(1, num_courts + 1):
        if len(remaining) < 4:
            break

        best_overall = None
        for four in itertools.combinations(remaining, 4):
            score, team_a, team_b = _score_candidate(four, mixed_mode, variety_mode)
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

        # update history so subsequent courts THIS round see it too
        team_a[0].partners_played.add(team_a[1].name)
        team_a[1].partners_played.add(team_a[0].name)
        team_b[0].partners_played.add(team_b[1].name)
        team_b[1].partners_played.add(team_b[0].name)
        for pa in team_a:
            for pb in team_b:
                pa.opponents_played.add(pb.name)
                pb.opponents_played.add(pa.name)
        for p in four:
            p.sessions_played += 1
            remaining.remove(p)

    return results
