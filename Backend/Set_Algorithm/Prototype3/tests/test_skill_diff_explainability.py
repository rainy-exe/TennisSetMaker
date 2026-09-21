"""Addresses Rai's PR-review comment: he saw a foursome split into
lopsided teams (e.g. two similarly-rated players kept together as
partners, producing a worse team-sum diff than a different split of the
SAME four players would have given) and suspected "the system is maybe
favoring matching same-skill partners over a more balanced outcome."

Confirmed (not a bug): `raw_skill_spread` is identical across all 3
possible splits of a fixed foursome (it only looks at the 4 ratings, not
how they're split), so it can never favour "same-skill partners" over a
balanced split on its own. Under mixed_mode=0 with no partner/opponent
history, the code is mathematically guaranteed to pick the minimum-diff
split (test 1). A worse-diff split only ever gets chosen when a STRICTLY
higher-priority criterion -- format compliance (mixed_mode=1) or social
variety (a repeat partner/opponent) -- rules out the better-diff split
(tests 2 and 3). This matches the Set-Matching Algorithm Priority Ranking
doc's explicit design intent: "Deliberately stops perfect skill-matching
if cost is social mixing." `explain_split_choice` (algorithm.py) makes
this visible so a lopsided-looking result isn't mistaken for a defect."""

from helpers import Player
from algorithm import explain_split_choice


def make_player(name, rating, gender="MALE"):
    return Player(name=name, gender=gender, rating=rating)


def test_best_diff_split_chosen_when_unconstrained():
    """{3,3,4,5}, mixed_mode=0, no history: the (3,4) vs (3,5) split
    (diff=1) must be chosen over (3,3) vs (4,5) (diff=3) -- nothing
    forces the worse split here."""
    four = (make_player("a", 3), make_player("b", 3), make_player("c", 4), make_player("d", 5))
    result = explain_split_choice(four, mixed_mode=0, variety_mode=2)
    assert result["chosen_diff"] == 1
    assert result["best_possible_diff"] == 1
    assert result["compromised"] is False
    assert result["reason"] is None


def test_mixed_mode_can_force_a_worse_diff_split():
    """{3,4,5,6} with genders M=3,6 / F=4,5: the diff=0 split (3+6 vs
    4+5) is gender-homogeneous (invalid under mixed_mode=1), so a worse
    but gender-valid split must be chosen instead -- explainable, not
    a bug."""
    four = (
        make_player("m3", 3, "MALE"), make_player("f4", 4, "FEMALE"),
        make_player("f5", 5, "FEMALE"), make_player("m6", 6, "MALE"),
    )
    result = explain_split_choice(four, mixed_mode=1, variety_mode=2)
    assert result["best_possible_diff"] == 0  # 3+6 vs 4+5 exists, but is gender-invalid
    assert result["compromised"] is True
    assert result["chosen_diff"] > 0
    assert result["reason"] == "format compliance (mixed_mode) ruled out the lower-diff split"


def test_partner_history_can_force_a_worse_diff_split():
    """{3,3,4,5}: if the (3,4)+(3,5) partnership pairing already happened
    (one of the 3s already partnered the 4, say), that split becomes a
    partner repeat and a worse-diff split must be chosen instead."""
    a, b, c, d = make_player("a", 3), make_player("b", 3), make_player("c", 4), make_player("d", 5)
    a.partners_played.add(c.player_id)  # a+c (the 3+4 pairing) already happened
    c.partners_played.add(a.player_id)

    result = explain_split_choice((a, b, c, d), mixed_mode=0, variety_mode=2)
    assert result["best_possible_diff"] == 1  # (a,c)+(b,d) or (b,c)+(a,d), both diff=1
    # the specific low-diff split using a+c is now a repeat; a different diff=1
    # split ((b,c)+(a,d), i.e. 3+4 vs 3+5 the other way) is still available and
    # doesn't repeat anything, so the algorithm should still find diff=1 --
    # this test instead forces ALL diff=1 splits to repeat, to guarantee a
    # compromise actually occurs.
    b.partners_played.add(c.player_id)
    c.partners_played.add(b.player_id)
    b.partners_played.add(d.player_id)
    d.partners_played.add(b.player_id)
    a.partners_played.add(d.player_id)
    d.partners_played.add(a.player_id)

    result = explain_split_choice((a, b, c, d), mixed_mode=0, variety_mode=2)
    assert result["compromised"] is True
    assert result["reason"] == "partner variety ruled out the lower-diff split (it would have repeated a partner)"
