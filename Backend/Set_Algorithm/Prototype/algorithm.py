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
    5. Skill balance             -> skill_diff
    6. Court preferences         -> not implemented (no data in prototype scope), always 0

Each candidate court (a foursome + a team split) is scored as a tuple.
Python compares tuples lexicographically, so the tuple order below IS
the priority hierarchy — changing the algorithm's priority order later
is a one-line change (reorder the tuple), not a rewrite.

This module knows nothing about CSV files, printing, or user input.
"""

import itertools
from typing import List, Tuple
from helpers import Player

BIG_PENALTY = 10_000  # used by variety_mode=1 (hard avoid) to dominate the score


# ---------------------------------------------------------------------------
# Priority 1: Sit-out ratio (round-level, not per-court)
# ---------------------------------------------------------------------------

def select_round_players(active_pool: List[Player], num_to_play: int) -> Tuple[List[Player], List[Player]]:
    """Chooses who plays this round vs who sits out, based on sit-out
    fairness: players who have sat out MORE in the past get priority to
    play now; players who have sat out LEAST are the fairest candidates
    to sit out this round (their count then rises to catch up).

    Ties broken by a stable sort on name to keep runs reproducible under
    a fixed seed.
    """
    ranked = sorted(active_pool, key=lambda p: (-p.sit_out_count, p.name))
    playing = ranked[:num_to_play]
    sitting_out = ranked[num_to_play:]
    return playing, sitting_out


# ---------------------------------------------------------------------------
# Priority 2: Set format compliance (mixed/non-mixed)
# ---------------------------------------------------------------------------

def _mixed_score(team_a: Tuple[Player, Player], team_b: Tuple[Player, Player], mixed_mode: int) -> int:
    if mixed_mode == 0:
        return 0  # open format, no constraint
    penalty = 0
    for team in (team_a, team_b):
        genders = {team[0].gender, team[1].gender}
        if len(genders) != 2:  # not one MALE + one FEMALE
            penalty += 1
    return penalty  # 0 = fully mixed, 1 = one team not mixed, 2 = neither team mixed


# ---------------------------------------------------------------------------
# Priorities 3 & 4: Social variety — unique partners / unique opponents
# ---------------------------------------------------------------------------

def _partner_score(team_a: Tuple[Player, Player], team_b: Tuple[Player, Player], variety_mode: int) -> int:
    repeats = 0
    for team in (team_a, team_b):
        if team[1].name in team[0].partners_played:
            repeats += 1
    if variety_mode == 1:  # hard avoid
        return BIG_PENALTY * repeats
    return repeats  # modes 2 and 3 both use a soft count here; mode 3's extra bias is in opponent_score


def _opponent_score(team_a: Tuple[Player, Player], team_b: Tuple[Player, Player], variety_mode: int) -> int:
    repeats = 0
    for pa in team_a:
        for pb in team_b:
            if pb.name in pa.opponents_played:
                repeats += 1
    if variety_mode == 1:  # hard avoid
        return BIG_PENALTY * repeats
    if variety_mode == 3:  # round-robin bias: also penalise ANY prior connection at all
        # widen the net -- count players in this foursome who've shared a court
        # (as partner OR opponent) with anyone else in the foursome, favouring
        # completely fresh groupings over sessions.
        four = team_a + team_b
        connection_penalty = 0
        for p1, p2 in itertools.combinations(four, 2):
            if p2.name in p1.partners_played or p2.name in p1.opponents_played:
                connection_penalty += 1
        return repeats + connection_penalty
    return repeats  # mode 2: plain soft count


# ---------------------------------------------------------------------------
# Priority 5: Skill balance
# ---------------------------------------------------------------------------

def _skill_diff(team_a: Tuple[Player, Player], team_b: Tuple[Player, Player]) -> int:
    a_sum = team_a[0].rating + team_a[1].rating
    b_sum = team_b[0].rating + team_b[1].rating
    return abs(a_sum - b_sum)


# ---------------------------------------------------------------------------
# Priority 6: Court preferences -- not implemented in prototype (no data)
# ---------------------------------------------------------------------------

def _court_pref_score() -> int:
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
    best = None
    for team_a, team_b in splits:
        score = (
            _mixed_score(team_a, team_b, mixed_mode),
            _partner_score(team_a, team_b, variety_mode),
            _opponent_score(team_a, team_b, variety_mode),
            _skill_diff(team_a, team_b),
            _court_pref_score(),
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
