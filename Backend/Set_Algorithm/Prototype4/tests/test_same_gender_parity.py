"""Same-gender sessions must never put a mixed team on a court.

The client's rule: "for same gender sessions it should only be male-male
and female-female and then any 'spares' should be put together in a
balanced set."

A mixed team is forced exactly when some gender has an ODD number of
players on court, because an odd count cannot be split into same-gender
pairs. Whether that happens is decided by who is picked to play, not by
how the courts are then built -- so these tests cover
select_round_players' parity repair and then the end-to-end result.
"""

import random

from helpers import Player
from algorithm import (
    GENDER_MIXED,
    GENDER_OPEN,
    GENDER_SAME,
    _gender_counts,
    _gender_parity_cost,
    assign_courts_for_round,
    court_gender_shape,
    select_round_players,
)


def P(name, rating=5, gender="MALE", **kw):
    return Player(name=name, gender=gender, rating=rating, **kw)


def pool(n_male, n_female, rng):
    return ([P(f"m{i}", rng.randint(2, 8), "MALE") for i in range(n_male)]
            + [P(f"f{i}", rng.randint(2, 8), "FEMALE") for i in range(n_female)])


def run_session(players, num_to_play, rounds, rng, mixed_mode):
    """One session with the bookkeeping the simulator and backend do.
    Yields (playing, sitting) per round."""
    for p in players:
        p.session_sit_outs = 0
        p.consecutive_sit_outs = 0
    for _ in range(rounds):
        playing, sitting = select_round_players(players, num_to_play, rng, mixed_mode=mixed_mode)
        sitting_ids = {id(p) for p in sitting}
        for p in players:
            if id(p) in sitting_ids:
                p.sit_out_count += 1
                p.session_sit_outs += 1
                p.consecutive_sit_outs += 1
            else:
                p.sessions_played += 1
                p.consecutive_sit_outs = 0
        yield playing, sitting


def parity_feasible(n_male, n_female, rounds):
    """Whether parity is reachable in every round without breaking the
    client's sit-out rules.

    To leave an even number of a gender on court, an odd-sized gender
    must put an odd number of its players in the sit-out group -- so at
    least one of them sits EVERY round. Rule 1 forbids anyone sitting
    twice until everyone has sat once, so that needs a fresh player each
    round: an odd-sized gender must have at least `rounds` players.
    When it doesn't, fairness wins and parity gives way (see
    test_parity_gives_way_to_fairness_when_a_gender_is_too_small).
    """
    return all(n >= rounds for n in (n_male, n_female) if n % 2)


# ---------------------------------------------------------------------------
# The parity condition itself
# ---------------------------------------------------------------------------

def test_parity_cost_reads_zero_only_for_single_gender_courts():
    assert _gender_parity_cost(pool(12, 12, random.Random(1))) == (0, 0)
    assert _gender_parity_cost(pool(16, 8, random.Random(1))) == (0, 0)
    # even counts, but not multiples of 4: one leftover men-v-ladies court
    assert _gender_parity_cost(pool(14, 10, random.Random(1))) == (0, 2)
    # odd counts: somebody must partner across genders
    assert _gender_parity_cost(pool(13, 11, random.Random(1))) == (2, 2)


def test_parity_repair_makes_every_gender_even():
    """26 attending on 6 courts: every attendance split that parity can
    reach must end up with an even number of each gender on court, for
    all 6 rounds. Covers the whole realistic range for the club's
    42M/33F roster."""
    checked = 0
    for n_male in range(2, 25):
        n_female = 26 - n_male
        if not parity_feasible(n_male, n_female, rounds=6):
            continue
        checked += 1
        rng = random.Random(2026)
        players = pool(n_male, n_female, rng)
        for playing, _sitting in run_session(players, 24, 6, rng, GENDER_SAME):
            counts = _gender_counts(playing)
            assert all(n % 2 == 0 for n in counts.values()), (
                f"{n_male}M/{n_female}F attending left {counts} on court"
            )
    assert checked == 19  # only 3M, 5M, 21M, 23M are out of reach


def test_parity_gives_way_to_fairness_when_a_gender_is_too_small():
    """3M/23F, 6 rounds: parity would need a different male to sit every
    round, but there are only three. The client's sit-out rules win, so
    the early rounds are clean and the later ones fall back -- which is
    the documented behaviour, not a failure."""
    rng = random.Random(2026)
    players = pool(3, 23, rng)
    odd_rounds = []
    for rd, (playing, _sitting) in enumerate(run_session(players, 24, 6, rng, GENDER_SAME), 1):
        counts = _gender_counts(playing)
        if any(n % 2 for n in counts.values()):
            odd_rounds.append(rd)
    assert odd_rounds == [4, 5, 6]        # the first three rounds still get parity
    counts = [p.session_sit_outs for p in players]
    assert max(counts) - min(counts) <= 1  # rule 1 held throughout


def test_parity_repair_with_a_single_sitout_slot():
    """13 players on 3 courts: only one sit-out slot, which is still
    enough because an odd turnout always has exactly one odd gender."""
    for n_male in range(2, 12):
        n_female = 13 - n_male
        if not parity_feasible(n_male, n_female, rounds=6):
            continue
        rng = random.Random(7)
        players = pool(n_male, n_female, rng)
        for playing, _sitting in run_session(players, 12, 6, rng, GENDER_SAME):
            counts = _gender_counts(playing)
            assert all(n % 2 == 0 for n in counts.values()), (
                f"{n_male}M/{n_female}F attending left {counts} on court"
            )


# ---------------------------------------------------------------------------
# The client's sit-out rules must survive the repair
# ---------------------------------------------------------------------------

def test_parity_repair_keeps_sitout_rules_1_and_2():
    """Rule 1: nobody sits twice until everyone has sat once. Rule 2: no
    back-to-back sit-outs. The repair only trades players already level
    on both, so both must hold exactly as in open mode."""
    for n_male in (11, 13, 14, 17):
        rng = random.Random(99)
        players = pool(n_male, 26 - n_male, rng)
        previous = set()
        for _playing, sitting in run_session(players, 24, 6, rng, GENDER_SAME):
            names = {p.name for p in sitting}
            assert not (names & previous), f"back-to-back sit-out: {names & previous}"
            previous = names
            counts = [p.session_sit_outs for p in players]
            assert max(counts) - min(counts) <= 1, "someone sat twice before everyone sat once"


def test_other_gender_modes_select_exactly_as_before():
    """Only GENDER_SAME runs the repair, so open and mixed mode must give
    byte-identical selections to the default call."""
    for mode in (None, GENDER_OPEN, GENDER_MIXED):
        rng_a, rng_b = random.Random(5), random.Random(5)
        players_a = pool(13, 13, random.Random(3))
        players_b = pool(13, 13, random.Random(3))
        baseline, _ = select_round_players(players_a, 24, rng_a)
        if mode is None:
            other, _ = select_round_players(players_b, 24, rng_b)
        else:
            other, _ = select_round_players(players_b, 24, rng_b, mixed_mode=mode)
        assert [p.name for p in baseline] == [p.name for p in other]


def test_no_sitout_slots_is_a_no_op():
    """Everyone plays: there is no lever, and it must not raise."""
    rng = random.Random(4)
    players = pool(13, 11, rng)
    playing, sitting = select_round_players(players, 24, rng, mixed_mode=GENDER_SAME)
    assert len(playing) == 24 and sitting == []


# ---------------------------------------------------------------------------
# End to end
# ---------------------------------------------------------------------------

def test_no_mixed_team_end_to_end():
    """The whole pipeline: parity-aware selection then court building.
    No court may contain a team whose two players differ in gender."""
    for n_male in range(2, 25):
        if not parity_feasible(n_male, 26 - n_male, rounds=6):
            continue
        rng = random.Random(2026)
        players = pool(n_male, 26 - n_male, rng)
        for playing, _sitting in run_session(players, 24, 6, rng, GENDER_SAME):
            for r in assign_courts_for_round(playing, 6, GENDER_SAME, 1):
                _minority, mixed_teams = court_gender_shape(r["team_a"], r["team_b"])
                assert mixed_teams == 0, (
                    f"{n_male}M attending: mixed team on court {r['court']} -- "
                    + ", ".join(f"{p.name}/{p.gender}" for p in r["players"])
                )


def test_spares_land_on_one_men_v_ladies_court():
    """14M/10F on court: five single-gender courts plus one spares court
    of two men against two women -- the client's stated fallback."""
    rng = random.Random(11)
    players = pool(14, 10, rng)
    shapes = sorted(court_gender_shape(r["team_a"], r["team_b"])
                    for r in assign_courts_for_round(players, 6, GENDER_SAME, 1))
    assert shapes == [(0, 0)] * 5 + [(2, 0)]
