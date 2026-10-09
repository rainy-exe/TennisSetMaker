'''
algorithm.py  (Prototype4)

The set-matching algorithm. Two stages per round:

  A. WHO PLAYS  -- select_round_players (sit-out fairness, Scope of Work
     priority 1).
  B. WHO PLAYS WHOM -- assign_courts_for_round, scoring every candidate
     court as a tuple. Python compares tuples left-to-right, so the tuple
     order in _score_candidate IS the priority hierarchy.

A. Sit-outs (client rules)
--------------------------
  1. Nobody sits out a second time in a session until everyone there has
     sat out once (then nobody sits a third time until everyone has sat
     twice, and so on).                               -- hard rule
  2. Nobody sits out two rounds in a row, unless the numbers make it
     unavoidable.                                     -- hard rule
  3. Among players still level on 1 and 2, the one with the LOWEST sit-out
     ratio over their last RATIO_WINDOW_ROUNDS rounds sits first. A player
     with fewer rounds than that has the missing rounds filled in at the
     room's average ratio, so a newcomer starts exactly average and is
     judged fully on their own record once they have a full window.
  4. Remaining ties: random (seeded).

Prototype3 instead ranked players by their raw sit-out COUNT. That made a
newcomer (count 0, regulars on 10+) sit out round after round until they
"caught up", and made occasional players sit out a bigger share of their
rounds than regulars.

B. Courts -- the variety mode picks the trade-off
-------------------------------------------------
Gender (open / mixed / same-gender) is always first and acts as a lock.
After that, each variety mode balances rotation and skill differently
(MODE_PROFILES):

  1  Hard avoid repeats -- ROTATION FIRST. Hard limits: team gap <= 4,
     partners <= 4 apart. Inside those, avoiding repeat partners/opponents
     decides; skill targets (gaps within 2, team gap 1) only choose between
     options rotation leaves level.
  2  Soft -- SKILL FIRST. Hard limits: team gap <= 2, partners <= 2 apart,
     court range <= 3, and team gap 1 preferred -- all above rotation.
     Repeats are avoided only among options that keep those.
  3  Round-robin -- MIDDLE. Same hard limits as mode 2 (team gap <= 2,
     partners <= 2 apart), but rotation comes BEFORE the team-gap-1
     preference, and it counts anyone you've met today at all (partner or
     opponent), not just exact repeats. (A version with limits of 3 was
     tried: it rotated no better than mode 1 and matched worse.)

"Hard limit" means it is only broken when no combination avoids it; it is
graded (how far over), and when the team-gap and partner-gap limits can't
both be met, the option breaking either by the least wins, so the two
gaps stay similar (5+7 v 2+2 loses to 5+2 v 7+2). The full tuple layout is
in court_score().

Ranking these skill limits above partner/opponent variety deviates from
the Scope of Work's default preset order and needs client sign-off -- see
README.md "Prototype4 changes".

This module knows nothing about CSV files, printing, or user input.
'''

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

# Variety modes. Each one is a different trade-off between rotation and
# skill, not just a different repeat penalty:
VARIETY_HARD = 1         # rotation first: avoid repeats; gaps allowed up to 4
VARIETY_SOFT = 2         # skill first: gaps kept within 2; repeats avoided where that holds
VARIETY_ROUND_ROBIN = 3  # middle: gaps capped at 2, then avoid anyone met today at all

# Per-mode skill settings. Ratings are 1-10; team sums are 2-20.
#   max_team_diff     team totals at most this far apart            } hard limits: only broken
#   max_partner_gap   partners at most this far apart               } when no combination
#   max_court_spread  highest minus lowest rating on the court      } avoids it
#   ideal_team_diff   preferred team gap (skill-first mode: still ranked above rotation)
#   rotation_first    True: after the hard limits, rotation decides, and the skill
#                     TARGETS below only choose between options rotation leaves level
MODE_PROFILES = {
    VARIETY_HARD: dict(max_team_diff=4, max_partner_gap=4, max_court_spread=None,
                       ideal_team_diff=None, rotation_first=True),
    VARIETY_SOFT: dict(max_team_diff=2, max_partner_gap=2, max_court_spread=3,
                       ideal_team_diff=1, rotation_first=False),
    VARIETY_ROUND_ROBIN: dict(max_team_diff=2, max_partner_gap=2, max_court_spread=None,
                              ideal_team_diff=None, rotation_first=True),
}
# Skill targets used as tie-breakers below rotation in the rotation-first modes:
# "try" to keep team gap and partner gap within 2, team gap 1 preferred.
TARGET_TEAM_DIFF = 2
TARGET_PARTNER_GAP = 2
TARGET_IDEAL_TEAM_DIFF = 1

# Skill-first (mode 2) values, kept under their old names for existing imports.
DEFAULT_MAX_TEAM_DIFF = MODE_PROFILES[VARIETY_SOFT]["max_team_diff"]
DEFAULT_MAX_PARTNER_GAP = MODE_PROFILES[VARIETY_SOFT]["max_partner_gap"]
DEFAULT_MAX_COURT_SPREAD = MODE_PROFILES[VARIETY_SOFT]["max_court_spread"]
DEFAULT_IDEAL_TEAM_DIFF = MODE_PROFILES[VARIETY_SOFT]["ideal_team_diff"]

_MODE_DEFAULT = object()  # "use the variety mode's own setting"


def skill_profile(variety_mode: int) -> dict:
    """The skill settings a variety mode uses (unknown modes -> skill first)."""
    return dict(MODE_PROFILES.get(variety_mode, MODE_PROFILES[VARIETY_SOFT]))


# Everything off = Prototype3's court scoring (used by tests and for comparison).
NO_SKILL_LIMITS = dict(max_team_diff=None, max_court_spread=None, max_partner_gap=None,
                       ideal_team_diff=None, skill_targets=False)

# Sit-out ratio window: each player's last N rounds attended.
RATIO_WINDOW_ROUNDS = 50


# ---------------------------------------------------------------------------
# A. Who plays: sit-out fairness
# ---------------------------------------------------------------------------

def _window_counts(p: Player) -> Tuple[int, int]:
    """(sit-outs, rounds attended) inside the player's ratio window. Uses
    recent_sit_outs/recent_rounds when the caller supplies them; otherwise
    falls back to the all-history totals (sit_out_count, and sit-outs plus
    rounds played) -- which is what the backend passes today."""
    if p.recent_rounds is not None and p.recent_sit_outs is not None:
        return p.recent_sit_outs, p.recent_rounds
    return p.sit_out_count, p.sit_out_count + p.sessions_played


def room_average_ratio(active_pool: List[Player]) -> float:
    """Pooled sit-out ratio of everyone present who has any history."""
    sits = rounds = 0
    for p in active_pool:
        s, r = _window_counts(p)
        sits += s
        rounds += r
    return sits / rounds if rounds else 0.0


def sit_out_ratio(p: Player, room_average: float, window: int = RATIO_WINDOW_ROUNDS) -> float:
    """Sit-out ratio over the player's last `window` rounds. Missing rounds
    (a player with fewer than `window` rounds of history) count at the
    room's average, so a brand-new player is exactly average and nobody is
    judged on a handful of rounds."""
    sits, rounds = _window_counts(p)
    if rounds >= window:
        return sits / rounds
    return (sits + (window - rounds) * room_average) / window


# Same-gender parity: how many sit-out trades the policy may ask for.
# One trade flips a gender's parity, so two is enough for any pool; the
# cap is only here to bound the loop.
MAX_PARITY_SWAPS = 4


def _gender_counts(players: List[Player]) -> dict:
    """How many players of each gender are in this group."""
    counts = {}
    for p in players:
        counts[p.gender] = counts.get(p.gender, 0) + 1
    return counts


def _gender_parity_cost(playing: List[Player]) -> Tuple[int, int]:
    """How far a set of players is from filling single-gender courts.

      [0] genders with an ODD headcount. An odd count cannot be split
          into same-gender pairs, so somebody MUST partner across
          genders -- the arrangement the client rejected. Driven to zero.
      [1] genders whose headcount is not a multiple of 4: what stops
          every court being all one gender. Only looked at once [0] is
          level, because a leftover men-v-ladies court is an acceptable
          result ("spares put together in a balanced set").

    [1] >= [0] always, since an odd count is never a multiple of 4.
    """
    counts = _gender_counts(playing)
    return (
        sum(1 for n in counts.values() if n % 2),
        sum(1 for n in counts.values() if n % 4),
    )


def _fairness_tier(p: Player) -> Tuple[int, int]:
    """The part of play_priority that carries the client's two sit-out
    rules (sat out already this session; sat out last round). Parity
    repair only ever trades players within one tier, so rules 1 and 2
    cannot be weakened by it."""
    return (p.session_sit_outs, 1 if p.consecutive_sit_outs > 0 else 0)


def _repair_gender_parity(
    playing: List[Player],
    sitting: List[Player],
    average: float,
    max_swaps: int = MAX_PARITY_SWAPS,
) -> Tuple[List[Player], List[Player]]:
    """Trades players between `playing` and `sitting` until every gender
    has an even number on court -- exactly the condition for never
    needing a mixed team (see _gender_parity_cost).

    Both players in a trade must share a _fairness_tier, so the client's
    sit-out rules 1 and 2 hold precisely as they did before; among the
    trades that help, the one disturbing the rule-3 sit-out ratio least
    wins, with the ranked positions as a final seed-stable tie-break.

    If no legal trade helps, the fairness-optimal selection is returned
    untouched and court scoring falls back to the least-bad arrangement.
    Returns new lists; the inputs are not mutated.
    """
    playing, sitting = list(playing), list(sitting)
    if not playing or not sitting:
        return playing, sitting

    cost = _gender_parity_cost(playing)
    for _ in range(max_swaps):
        if cost == (0, 0):
            break
        best = None  # (sort key, playing index, sitting index, new cost)
        for i, p_in in enumerate(playing):
            for j, p_out in enumerate(sitting):
                if p_in.gender == p_out.gender:
                    continue  # like for like cannot change any headcount
                if _fairness_tier(p_in) != _fairness_tier(p_out):
                    continue  # would weaken sit-out rule 1 or 2
                trial = playing[:i] + playing[i + 1:] + [p_out]
                new_cost = _gender_parity_cost(trial)
                if new_cost >= cost:
                    continue  # legal, but no improvement
                # Rule 3: a higher sit-out ratio earns a court spot, so
                # prefer giving up the player who has earned it least.
                key = (
                    new_cost,
                    sit_out_ratio(p_in, average) - sit_out_ratio(p_out, average),
                    i,
                    j,
                )
                if best is None or key < best[0]:
                    best = (key, i, j, new_cost)
        if best is None:
            break
        _, i, j, cost = best
        playing[i], sitting[j] = sitting[j], playing[i]
    return playing, sitting


def select_round_players(active_pool: List[Player], num_to_play: int, rng: random.Random,
                         mixed_mode: int = GENDER_OPEN) -> Tuple[List[Player], List[Player]]:
    """Chooses who plays this round and who sits out (rules A1-A4 above).

    Players are ranked for a court spot by, in order:
      - most sit-outs already today        (A1: everyone sits once before anyone sits twice)
      - sat out last round                 (A2: no back-to-back)
      - highest sit-out ratio              (A3: whoever has sat out least of their share sits)
      - seeded shuffle                     (A4)
    The lowest-ranked players sit out. Because each rule only orders
    players the rules above it leave level, rules 1 and 2 only give way
    when the numbers make them impossible to keep.

    In same-gender mode (mixed_mode = GENDER_SAME) a repair pass then
    trades players between the two groups so every gender has an even
    number on court -- the condition for never having to put a mixed
    team on a court. It only trades players the rules above already left
    level, so rules 1 and 2 are unaffected (_repair_gender_parity).
    """
    average = room_average_ratio(active_pool)

    def play_priority(p: Player):
        return (
            p.session_sit_outs,
            1 if p.consecutive_sit_outs > 0 else 0,
            sit_out_ratio(p, average),
        )

    shuffled = list(active_pool)
    rng.shuffle(shuffled)
    ranked = sorted(shuffled, key=play_priority, reverse=True)
    playing = ranked[:num_to_play]
    sitting_out = ranked[num_to_play:]
    if mixed_mode == GENDER_SAME:
        playing, sitting_out = _repair_gender_parity(playing, sitting_out, average)
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


def court_gender_shape(team_a: Tuple[Player, Player], team_b: Tuple[Player, Player]) -> Tuple[int, int]:
    """The two raw gender facts about one court:

      minority     how many of the four players are NOT in the court's
                   majority gender (0 = all four share a gender)
      mixed_teams  how many of the two TEAMS have partners of different
                   genders (0, 1 or 2)

    Reporting uses this as well, so a gender metric in the simulation
    output can never drift from raw_same_gender_score() below.
    """
    four = team_a + team_b
    counts = {}
    for p in four:
        counts[p.gender] = counts.get(p.gender, 0) + 1
    minority = 4 - max(counts.values())
    mixed_teams = sum(1 for team in (team_a, team_b) if team[0].gender != team[1].gender)
    return minority, mixed_teams


def raw_same_gender_score(team_a: Tuple[Player, Player], team_b: Tuple[Player, Player]) -> int:
    """Same-gender policy (men's / ladies' doubles), 0 = best.

    The client's rule is "only male-male and female-female, and then any
    spares should be put together in a balanced set". So a MIXED TEAM --
    somebody forced to partner across genders -- is the thing to avoid
    above all else; mixed teams carry the 3x weight and the minority
    headcount only breaks ties below it:

        0  all four share a gender             (MM v MM)
        2  men v ladies, the spares court      (MM v FF)
        4  a lone minority player              (MM v MF)
        8  two mixed pairs                     (MF v MF)

    Whether a mixed team is forced at all is decided a step earlier, by
    who is picked to play -- see _gender_parity_cost().
    """
    minority, mixed_teams = court_gender_shape(team_a, team_b)
    return 3 * mixed_teams + minority


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


def raw_partner_gap(team_a, team_b) -> int:
    """Largest rating gap between two partners on this court."""
    return max(abs(team_a[0].rating - team_a[1].rating), abs(team_b[0].rating - team_b[1].rating))


def skill_limit_excess(team_a, team_b, max_team_diff: Optional[int], max_partner_gap: Optional[int]) -> Tuple[int, int]:
    """(worst, total) points over the two match limits -- team gap and
    partner gap. Comparing `worst` first means that when both limits can't
    be met, the option that breaks EITHER one by the least wins: the two
    gaps are kept similar instead of one being fixed at the other's expense."""
    team_over = 0 if max_team_diff is None else max(0, raw_skill_diff(team_a, team_b) - max_team_diff)
    partner_over = 0 if max_partner_gap is None else max(0, raw_partner_gap(team_a, team_b) - max_partner_gap)
    return max(team_over, partner_over), team_over + partner_over


def raw_team_gap_over_ideal(team_a, team_b, ideal_team_diff: Optional[int]) -> int:
    if ideal_team_diff is None:
        return 0
    return max(0, raw_skill_diff(team_a, team_b) - ideal_team_diff)


# ---------------------------------------------------------------------------
# Court preferences -- not implemented in prototype (no data)
# ---------------------------------------------------------------------------

def raw_court_pref_score() -> int:
    return 0


# ---------------------------------------------------------------------------
# Candidate generation & scoring
# ---------------------------------------------------------------------------


def _resolve(variety_mode, overrides):
    """Mode profile with any explicit keyword overrides applied (None = off)."""
    prof = skill_profile(variety_mode)
    for k, v in overrides.items():
        if v is not _MODE_DEFAULT:
            prof[k] = v
    return prof


def court_score(team_a, team_b, mixed_mode: int, variety_mode: int,
                max_team_diff=_MODE_DEFAULT, max_court_spread=_MODE_DEFAULT,
                max_partner_gap=_MODE_DEFAULT, ideal_team_diff=_MODE_DEFAULT,
                skill_targets: bool = True, _profile: Optional[dict] = None):
    """The full priority tuple for one team split. Fixed layout for all modes:

      0  gender lock
      1  hard limits: worst points over (team gap / partner gap limit)
      2  hard limits: total points over both
      3  hard limits: court range over its limit
      4  skill-first only: team gap over the ideal
      5  partner repeats            } rotation
      6  opponent score             }
      7  rotation-first only: worst points over the skill targets (2 / 2)
      8  rotation-first only: total points over the targets
      9  rotation-first only: team gap over 1
      10 court range
      11 team gap + partner gap
      12 court preference (not implemented, always 0)
    """
    prof = _profile or _resolve(variety_mode, dict(
        max_team_diff=max_team_diff, max_court_spread=max_court_spread,
        max_partner_gap=max_partner_gap, ideal_team_diff=ideal_team_diff))
    four = team_a + team_b
    hard_worst, hard_total = skill_limit_excess(team_a, team_b, prof["max_team_diff"], prof["max_partner_gap"])
    pre_ideal = post_worst = post_total = post_ideal = 0
    if prof["rotation_first"]:
        if skill_targets:
            post_worst, post_total = skill_limit_excess(team_a, team_b, TARGET_TEAM_DIFF, TARGET_PARTNER_GAP)
            post_ideal = raw_team_gap_over_ideal(team_a, team_b, TARGET_IDEAL_TEAM_DIFF)
    else:
        pre_ideal = raw_team_gap_over_ideal(team_a, team_b, prof["ideal_team_diff"])
    return (
        _mixed_score(team_a, team_b, mixed_mode),
        hard_worst,
        hard_total,
        raw_court_spread_excess(four, prof["max_court_spread"]),
        pre_ideal,
        _partner_score(team_a, team_b, variety_mode),
        _opponent_score(team_a, team_b, variety_mode),
        post_worst,
        post_total,
        post_ideal,
        raw_skill_spread(four),
        raw_skill_diff(team_a, team_b) + raw_partner_gap(team_a, team_b),
        raw_court_pref_score(),
    )


# Tuple positions used by the repair and refinement passes.
_LOCK_AND_LIMITS = slice(0, 5)   # gender + everything ranked above rotation
_VARIETY = slice(5, 7)
_ALL_SKILL_AND_VARIETY = slice(0, 10)


def _score_candidate(
    four: Tuple[Player, Player, Player, Player], mixed_mode: int, variety_mode: int,
    max_team_diff=_MODE_DEFAULT, max_court_spread=_MODE_DEFAULT,
    max_partner_gap=_MODE_DEFAULT, ideal_team_diff=_MODE_DEFAULT,
    skill_targets: bool = True,
):
    """Returns (score_tuple, team_a, team_b) for the BEST of this foursome's
    3 possible team splits."""
    prof = _resolve(variety_mode, dict(
        max_team_diff=max_team_diff, max_court_spread=max_court_spread,
        max_partner_gap=max_partner_gap, ideal_team_diff=ideal_team_diff))
    p1, p2, p3, p4 = four
    splits = [
        ((p1, p2), (p3, p4)),
        ((p1, p3), (p2, p4)),
        ((p1, p4), (p2, p3)),
    ]
    best = None
    for team_a, team_b in splits:
        score = court_score(team_a, team_b, mixed_mode, variety_mode,
                            skill_targets=skill_targets, _profile=prof)
        if best is None or score < best[0]:
            best = (score, team_a, team_b)
    return best


def assign_courts_for_round(
    playing: List[Player], num_courts: int, mixed_mode: int, variety_mode: int,
    refine_max_iterations: Optional[int] = None,
    max_team_diff=_MODE_DEFAULT, max_court_spread=_MODE_DEFAULT,
    max_partner_gap=_MODE_DEFAULT, ideal_team_diff=_MODE_DEFAULT,
    skill_targets: bool = True,
):
    """Three steps:
      1. Greedily fill courts one at a time (best-scoring foursome from
         whoever is left).
      2. repair_guardrails: swap players between courts to fix any court
         the greedy fill left outside the skill limits -- may cost a repeat
         partner/opponent, never the gender lock (refinement.py).
      3. refine_skill_balance: Prototype3's swap pass that evens out skill
         spread across courts without making the gender lock, skill limits
         or variety worse on any court it touches.
    `refine_max_iterations=0` switches steps 2 and 3 off (greedy only).

    Same call signature as Prototype3 for the first 5 arguments, so the
    backend adapter works unchanged. The skill settings come from the
    variety mode (MODE_PROFILES); any keyword given overrides that one
    setting (None switches it off).

    Returns a list of dicts: {court, team_a, team_b, players, team_a_sum, team_b_sum}
    """
    prof = _resolve(variety_mode, dict(
        max_team_diff=max_team_diff, max_court_spread=max_court_spread,
        max_partner_gap=max_partner_gap, ideal_team_diff=ideal_team_diff))
    limits = dict(max_team_diff=prof["max_team_diff"], max_court_spread=prof["max_court_spread"],
                  max_partner_gap=prof["max_partner_gap"], ideal_team_diff=prof["ideal_team_diff"])

    def _best_split(four):
        return _score_candidate(four, mixed_mode, variety_mode, skill_targets=skill_targets, **limits)

    def _score(team_a, team_b):
        return court_score(team_a, team_b, mixed_mode, variety_mode, skill_targets=skill_targets, _profile=prof)

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

    # Step 2: fix courts left outside the skill limits.
    results = repair_guardrails(
        results,
        gender_fn=lambda a, b: _mixed_score(a, b, mixed_mode),
        guardrail_fn=lambda a, b: _score(a, b)[1:_LOCK_AND_LIMITS.stop],
        variety_fn=lambda a, b: sum(_score(a, b)[_VARIETY]),
        best_split_fn=_best_split,
        max_iterations=refine_max_iterations,
    )

    # Step 3: even out skill spread across courts, never making the gender
    # lock, skill limits or variety worse on any court touched.
    results = refine_skill_balance(
        results,
        priority_quality_fn=lambda a, b: _score(a, b)[_ALL_SKILL_AND_VARIETY],
        best_split_fn=_best_split,
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
