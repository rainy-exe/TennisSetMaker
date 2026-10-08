"""Prototype4 sit-out rules and partner-gap limit.

Sit-outs (client rules):
  1. Nobody sits out a second time in a session until everyone there has sat once.
  2. No back-to-back sit-outs unless unavoidable.
  3. Ties go to whoever has the lowest sit-out ratio over their last 50
     rounds; missing rounds are filled at the room average.

Skill: partners at most 2 apart, team gap at most 2, team gap 1 ideal --
but never bought by putting partners more than 2 apart."""

import random

from helpers import Player
from algorithm import (
    RATIO_WINDOW_ROUNDS,
    _score_candidate,
    assign_courts_for_round,
    raw_partner_gap,
    raw_skill_diff,
    room_average_ratio,
    select_round_players,
    sit_out_ratio,
)


def P(name, rating=5, gender="MALE", **kw):
    return Player(name=name, gender=gender, rating=rating, **kw)


def play_session(players, num_to_play, rounds, rng):
    """One session using the same bookkeeping the backend/simulator does.
    Returns the list of sitting-out name sets, one per round."""
    for p in players:
        p.session_sit_outs = 0
        p.consecutive_sit_outs = 0
    log = []
    for _ in range(rounds):
        playing, sitting = select_round_players(players, num_to_play, rng)
        sitting_ids = {id(p) for p in sitting}
        for p in players:
            if id(p) in sitting_ids:
                p.sit_out_count += 1
                p.session_sit_outs += 1
                p.consecutive_sit_outs += 1
            else:
                p.sessions_played += 1
                p.consecutive_sit_outs = 0
        log.append({p.name for p in sitting})
    return log


# ---------------------------------------------------------------- sit-outs

def test_everyone_sits_once_before_anyone_sits_twice():
    """30 players, 24 spots, 6 rounds = 36 sit-out slots for 30 people, so
    6 people must sit twice -- but only after all 30 have sat once."""
    rng = random.Random(1)
    players = [P(f"p{i}", sit_out_count=rng.randint(0, 20), sessions_played=rng.randint(30, 200)) for i in range(30)]
    log = play_session(players, 24, 6, rng)
    seen = set()
    for sitting in log:
        repeats = sitting & seen
        if repeats:
            assert len(seen) == 30, f"{repeats} sat twice before everyone had sat once"
        seen |= sitting
    assert max(p.session_sit_outs for p in players) - min(p.session_sit_outs for p in players) <= 1


def test_no_back_to_back_sit_outs():
    rng = random.Random(2)
    players = [P(f"p{i}", sit_out_count=rng.randint(0, 20), sessions_played=rng.randint(30, 200)) for i in range(30)]
    log = play_session(players, 24, 6, rng)
    for previous, current in zip(log, log[1:]):
        assert not (previous & current)


def test_newcomer_is_not_made_to_catch_up():
    """Prototype3 sat a newcomer out 5-6 rounds a session when regulars
    were on 10-20 sit-outs. Now: at most once per session, like everyone."""
    rng = random.Random(3)
    newcomer = P("new")
    regulars = [P(f"r{i}", sit_out_count=20, sessions_played=220) for i in range(25)]
    for _ in range(4):
        play_session([newcomer] + regulars, 24, 6, rng)
        assert newcomer.session_sit_outs <= 1


def test_lucky_regular_is_not_made_to_pay_it_all_back_in_one_day():
    """A regular whose ratio is well below everyone else's sits out once
    today (first in line), not every round."""
    rng = random.Random(4)
    lucky = P("lucky", recent_sit_outs=1, recent_rounds=50)
    others = [P(f"o{i}", recent_sit_outs=4, recent_rounds=50) for i in range(25)]
    log = play_session([lucky] + others, 24, 6, rng)
    assert lucky.session_sit_outs == 1
    assert "lucky" in log[0]  # lowest ratio -> first in line


def test_ratio_window_fills_missing_rounds_at_room_average():
    room = [P(f"r{i}", recent_sit_outs=5, recent_rounds=50) for i in range(4)]
    avg = room_average_ratio(room)
    assert avg == 0.1
    brand_new = P("new", recent_sit_outs=0, recent_rounds=0)
    assert sit_out_ratio(brand_new, avg) == avg
    half = P("half", recent_sit_outs=0, recent_rounds=25)   # 25 real rounds, 25 filled at average
    assert abs(sit_out_ratio(half, avg) - 0.05) < 1e-9
    full = P("full", recent_sit_outs=10, recent_rounds=RATIO_WINDOW_ROUNDS)
    assert sit_out_ratio(full, avg) == 10 / RATIO_WINDOW_ROUNDS


def test_backend_fields_still_work_without_new_inputs():
    """The backend currently passes only sit_out_count, rounds_played (as
    sessions_played) and consecutive_sit_outs. That must still run, using
    the all-history ratio."""
    rng = random.Random(5)
    players = [P(f"p{i}", sit_out_count=i, sessions_played=100) for i in range(10)]
    playing, sitting = select_round_players(players, 8, rng)
    assert len(playing) == 8 and len(sitting) == 2
    assert {p.name for p in sitting} == {"p0", "p1"}  # lowest ratios sit


# ---------------------------------------------------------------- partners / team gap

def _ratings(team_a, team_b):
    return {tuple(sorted(p.rating for p in team_a)), tuple(sorted(p.rating for p in team_b))}


def test_partners_kept_close_over_perfect_team_balance():
    """3,4,5,6: 3+6 v 4+5 is a perfect 9 v 9 but the 6 carries the 3.
    3+5 v 4+6 (8 v 10) keeps partners 2 apart -- preferred."""
    four = (P("a", 3), P("b", 4), P("c", 5), P("d", 6))
    _, team_a, team_b = _score_candidate(four, 0, 2)
    assert _ratings(team_a, team_b) == {(3, 5), (4, 6)}


def test_ideal_team_gap_beats_repeat_avoidance_when_partners_stay_close():
    """The real day-18 case: 3+3 v 2+2 (gap 2) was chosen to avoid repeat
    opponents. In skill-first mode, with gap 1 as the ideal, 3+2 v 3+2 wins."""
    adam, david = P("Adam S", 3), P("David M", 3)
    ann, gordon = P("Ann S", 2, "FEMALE"), P("Gordon D", 2)
    adam.partners_played.add(ann.player_id); ann.partners_played.add(adam.player_id)
    for x, y in ((adam, david), (ann, gordon)):
        x.opponents_played.add(y.player_id); y.opponents_played.add(x.player_id)
    _, team_a, team_b = _score_candidate((adam, david, ann, gordon), 0, 2)  # skill first
    assert raw_skill_diff(team_a, team_b) == 0
    assert _ratings(team_a, team_b) == {(2, 3)}


def test_blowout_still_prevented():
    four = (P("Mark L", 5), P("Gerry K", 7), P("Ceclie L", 2, "FEMALE"), P("Suzanne B", 2, "FEMALE"))
    _, team_a, team_b = _score_candidate(four, 0, 3)
    assert raw_skill_diff(team_a, team_b) <= 2


def test_partner_gap_and_team_gap_hold_across_realistic_rounds():
    ratings = [2, 3, 3, 3, 4, 4, 4, 4, 4, 5, 5, 5, 5, 5, 5, 5, 5, 6, 6, 6, 6, 7, 7, 7]
    rng = random.Random(3)
    players = [P(f"p{i}", r, rng.choice(["MALE", "FEMALE"])) for i, r in enumerate(ratings)]
    for _ in range(12):
        for r in assign_courts_for_round(players, 6, 0, 2):  # skill first
            assert raw_partner_gap(r["team_a"], r["team_b"]) <= 2
            assert raw_skill_diff(r["team_a"], r["team_b"]) <= 2


# ---------------------------------------------------------------- the three variety modes

def _day18_four():
    adam, david = P("Adam S", 3), P("David M", 3)
    ann, gordon = P("Ann S", 2, "FEMALE"), P("Gordon D", 2)
    adam.partners_played.add(ann.player_id); ann.partners_played.add(adam.player_id)
    for x, y in ((adam, david), (ann, gordon)):
        x.opponents_played.add(y.player_id); y.opponents_played.add(x.player_id)
    return adam, david, ann, gordon


def test_rotation_first_modes_avoid_the_repeat_inside_their_limits():
    """Same day-18 foursome: modes 1 and 3 put rotation first, so they keep
    3+3 v 2+2 (gap 2 is within their limits) to avoid repeat opponents."""
    for mode in (1, 3):
        _, team_a, team_b = _score_candidate(_day18_four(), 0, mode)
        assert _ratings(team_a, team_b) == {(3, 3), (2, 2)}, mode


def test_hard_mode_accepts_gaps_up_to_4_to_avoid_a_repeat():
    """4,4,6,6 where the balanced splits all repeat a partnership: mode 1
    takes 4+4 v 6+6 (team gap 4, partners 0 apart) to avoid the repeat;
    mode 2 keeps the balanced match and accepts the repeat."""
    def four():
        a, b, c, d = P("a", 4), P("b", 4), P("c", 6), P("d", 6)
        for x, y in ((a, c), (b, d), (a, d), (b, c)):   # every 4+6 pair has partnered
            x.partners_played.add(y.player_id); y.partners_played.add(x.player_id)
        return a, b, c, d
    _, ta, tb = _score_candidate(four(), 0, 1)
    assert raw_skill_diff(ta, tb) == 4
    _, ta, tb = _score_candidate(four(), 0, 2)
    assert raw_skill_diff(ta, tb) == 0


def test_hard_mode_never_goes_past_4_when_avoidable():
    """2,2,7,7 with every split except the 2+2 v 7+7 one repeating:
    2+2 v 7+7 is a 10-point gap, past mode 1's limit of 4, so mode 1
    takes a repeat instead (2+7 v 2+7)."""
    a, b, c, d = P("a", 2), P("b", 2), P("c", 7), P("d", 7)
    for x, y in ((a, c), (b, d), (a, d), (b, c)):
        x.partners_played.add(y.player_id); y.partners_played.add(x.player_id)
    _, ta, tb = _score_candidate((a, b, c, d), 0, 1)
    assert raw_skill_diff(ta, tb) == 0


def test_hard_mode_stays_within_4_across_realistic_rounds():
    ratings = [2, 3, 3, 3, 4, 4, 4, 4, 4, 5, 5, 5, 5, 5, 5, 5, 5, 6, 6, 6, 6, 7, 7, 7]
    rng = random.Random(8)
    players = [P(f"p{i}", r, rng.choice(["MALE", "FEMALE"])) for i, r in enumerate(ratings)]
    for _ in range(12):
        for r in assign_courts_for_round(players, 6, 0, 1):
            assert raw_partner_gap(r["team_a"], r["team_b"]) <= 4
            assert raw_skill_diff(r["team_a"], r["team_b"]) <= 4
