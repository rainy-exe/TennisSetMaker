"""
refinement.py

Bounded local-search post-processing for a single round's court
assignments. The greedy per-court fill in algorithm.py/algorithm_weighted.py
always fills court 1 first, so skill-spread quality systematically degrades
by court position (measured empirically: ~3x worse average skill spread on
later courts vs earlier ones at realistic session sizes, since court 1
always gets first pick of the tightest-clustered foursome). This module
tries swapping players between courts after the greedy fill to flatten
that gradient, without ever regressing format/partner/opponent quality
(priorities 2-4) on either court touched by a swap -- skill balance is a
strictly lower scope priority than those, so a swap may only trade among
equally-legal states, never buy skill improvement at variety's expense.

Engine-agnostic by design: callers (algorithm.py, algorithm_weighted.py)
supply their own scoring via callbacks rather than this module importing
either engine directly. This also avoids a circular import, since
algorithm.py imports refine_skill_balance from this module.
"""

import itertools
from typing import Callable, List, Optional, Tuple

from helpers import Player

DEFAULT_MAX_REFINE_ITERATIONS = 5

PriorityQualityFn = Callable[[Tuple[Player, Player], Tuple[Player, Player]], Tuple[int, ...]]
BestSplitFn = Callable[[Tuple[Player, ...]], Tuple[object, Tuple[Player, Player], Tuple[Player, Player]]]
SkillSpreadFn = Callable[[Tuple[Player, ...]], int]


def _candidate_index_swaps() -> List[Tuple[set, set]]:
    """All (remove_w_idx, remove_c_idx) index-set pairs worth trying between
    two 4-player courts: every 1-for-1 swap (4x4=16) and every 2-for-2 swap
    (C(4,2)xC(4,2)=36), 52 total.

    Both sizes matter, not just 1-for-1: a single 2-for-2 swap can reach a
    materially better split in one move than two 1-for-1 swaps would reach
    across two of our bounded iterations -- e.g. given courts {1,2,8,9}
    (spread 8) and {5,5,5,5} (spread 0), the best single 1-for-1 swap only
    reaches spread 7/4, while swapping {1,2} for {5,5} at once reaches 4/4
    in one move.
    """
    idx = (0, 1, 2, 3)
    swaps = []
    for w_idx in itertools.combinations(idx, 1):
        for c_idx in itertools.combinations(idx, 1):
            swaps.append((set(w_idx), set(c_idx)))
    for w_idx in itertools.combinations(idx, 2):
        for c_idx in itertools.combinations(idx, 2):
            swaps.append((set(w_idx), set(c_idx)))
    return swaps


_CANDIDATE_SWAPS = _candidate_index_swaps()


def _apply_swap(w_players, c_players, remove_w_idx, remove_c_idx):
    """Builds the two new 4-player rosters after swapping the players at
    `remove_w_idx` (indices into w_players) with the players at
    `remove_c_idx` (indices into c_players).

    Index-based, never comparing or hashing Player objects directly --
    Player is an unhashable dataclass (default eq=True, not frozen), so a
    set()/`in` check on Player instances would raise TypeError."""
    new_w = [w_players[i] for i in range(4) if i not in remove_w_idx] + [c_players[i] for i in remove_c_idx]
    new_c = [c_players[i] for i in range(4) if i not in remove_c_idx] + [w_players[i] for i in remove_w_idx]
    return new_w, new_c


def _court_fields(team_a, team_b, players) -> dict:
    return {
        "team_a": team_a,
        "team_b": team_b,
        "players": players,
        "team_a_sum": team_a[0].rating + team_a[1].rating,
        "team_b_sum": team_b[0].rating + team_b[1].rating,
    }


def _best_swap_between(
    court_w: dict, court_c: dict,
    priority_quality_fn: PriorityQualityFn, best_split_fn: BestSplitFn, skill_spread_fn: SkillSpreadFn,
) -> Optional[Tuple[dict, dict]]:
    """Searches every legal swap between two courts' rosters and returns
    the best one found (as a pair of new court-dict field updates), or
    None if no swap both (a) respects priorities 2-4 on both sides and
    (b) actually improves on the pair's current (max spread, total spread).
    """
    w_players = court_w["players"]
    c_players = court_c["players"]

    old_w_quality = priority_quality_fn(court_w["team_a"], court_w["team_b"])
    old_c_quality = priority_quality_fn(court_c["team_a"], court_c["team_b"])
    old_w_spread = skill_spread_fn(tuple(w_players))
    old_c_spread = skill_spread_fn(tuple(c_players))

    # A court that is ALREADY perfectly clustered (spread 0) must never be
    # disturbed, full stop -- breaking apart a genuinely optimal cluster to
    # relieve a worse court elsewhere would undo the skill-clustering fix
    # itself. (`court_w` -- the current worst court, per the caller's outer
    # loop -- can never be spread 0 here: the caller already stops before
    # calling this function once the worst court's own spread hits 0.)
    if old_c_spread == 0:
        return None

    old_pair_metric = (max(old_w_spread, old_c_spread), old_w_spread + old_c_spread)

    best = None  # (pair_metric, new_w_fields, new_c_fields)
    for remove_w_idx, remove_c_idx in _CANDIDATE_SWAPS:
        new_w_players, new_c_players = _apply_swap(w_players, c_players, remove_w_idx, remove_c_idx)

        _, new_w_team_a, new_w_team_b = best_split_fn(tuple(new_w_players))
        _, new_c_team_a, new_c_team_b = best_split_fn(tuple(new_c_players))

        new_w_quality = priority_quality_fn(new_w_team_a, new_w_team_b)
        new_c_quality = priority_quality_fn(new_c_team_a, new_c_team_b)

        # Never regress format/partner/opponent quality on EITHER court,
        # checked criterion-by-criterion (not just the combined tuple) --
        # skill balance is a strictly lower scope priority, so this pass
        # must never trade away variety/format quality to get it.
        if any(new > old for new, old in zip(new_w_quality, old_w_quality)):
            continue
        if any(new > old for new, old in zip(new_c_quality, old_c_quality)):
            continue

        new_w_spread = skill_spread_fn(tuple(new_w_players))
        new_c_spread = skill_spread_fn(tuple(new_c_players))

        # Between two courts that are BOTH already imperfect, genuine
        # give-and-take is fine -- one side may end up slightly worse as
        # long as the pair's (worst-of-the-two, combined-total) genuinely
        # improves. Only a literally-perfect court (handled above) is ever
        # off-limits; two merely-mediocre courts trading players is exactly
        # how the court-position gradient gets flattened in a large pool
        # where almost no court starts out perfectly clustered by chance.
        pair_metric = (max(new_w_spread, new_c_spread), new_w_spread + new_c_spread)
        if pair_metric >= old_pair_metric:
            continue  # legal, but not actually an improvement for this pair

        if best is None or pair_metric < best[0]:
            best = (
                pair_metric,
                _court_fields(new_w_team_a, new_w_team_b, new_w_players),
                _court_fields(new_c_team_a, new_c_team_b, new_c_players),
            )

    return None if best is None else (best[1], best[2])


def refine_skill_balance(
    results: List[dict],
    priority_quality_fn: PriorityQualityFn,
    best_split_fn: BestSplitFn,
    skill_spread_fn: SkillSpreadFn,
    max_iterations: Optional[int] = None,
) -> List[dict]:
    """Bounded local-search pass: repeatedly finds the court with the worst
    skill spread and tries swapping players with every other court, taking
    the best legal improving swap found each iteration, until no improving
    legal swap exists or `max_iterations` is reached.

    A swap is legal only if it doesn't regress `priority_quality_fn` (the
    caller's format/partner/opponent scoring, lower-is-better) on either
    court touched -- see `_best_swap_between`.

    `results` is mutated in place (by court-slot index) and returned; court
    numbers themselves never change, only which players/teams occupy each
    slot. Cost is bounded regardless of pool size: at most `max_iterations`
    outer loops, each checking (num_courts - 1) other courts x 52 index-swaps
    x 2 split evaluations -- negligible next to the O(n choose 4) cost the
    greedy fill itself already pays to build `results` in the first place.
    """
    if max_iterations is None:
        max_iterations = DEFAULT_MAX_REFINE_ITERATIONS
    if len(results) < 2 or max_iterations <= 0:
        return results

    for _ in range(max_iterations):
        spreads = [skill_spread_fn(tuple(r["players"])) for r in results]
        worst_idx = max(range(len(spreads)), key=lambda i: spreads[i])
        if spreads[worst_idx] == 0:
            break  # perfectly clustered already, nothing to gain

        current_metric = (max(spreads), sum(spreads))

        best_move = None  # (trial_metric, other_idx, new_w_fields, new_c_fields)
        for other_idx, other_result in enumerate(results):
            if other_idx == worst_idx:
                continue
            swap = _best_swap_between(
                results[worst_idx], other_result, priority_quality_fn, best_split_fn, skill_spread_fn,
            )
            if swap is None:
                continue
            new_w_fields, new_c_fields = swap
            trial_spreads = list(spreads)
            trial_spreads[worst_idx] = skill_spread_fn(tuple(new_w_fields["players"]))
            trial_spreads[other_idx] = skill_spread_fn(tuple(new_c_fields["players"]))
            trial_metric = (max(trial_spreads), sum(trial_spreads))
            if best_move is None or trial_metric < best_move[0]:
                best_move = (trial_metric, other_idx, new_w_fields, new_c_fields)

        if best_move is None or best_move[0] >= current_metric:
            break  # no improving legal move anywhere this iteration

        _, other_idx, new_w_fields, new_c_fields = best_move
        results[worst_idx].update(new_w_fields)
        results[other_idx].update(new_c_fields)

    return results
