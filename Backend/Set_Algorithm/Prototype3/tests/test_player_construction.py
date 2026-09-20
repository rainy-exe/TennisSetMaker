"""Tests for two Player-construction-time fixes:

1. Identity: partner/opponent history used to be keyed by `.name` (a
   string), so two distinct real people sharing a name could get falsely
   flagged as repeat partners/opponents. Fixed with a dedicated
   `player_id` field (defaulting to `name` when a caller doesn't supply
   one, for backward compatibility) that `raw_partner_repeats`,
   `raw_opponent_repeats`, and `raw_connection_penalty` key on instead.
2. Validation: `raw_skill_diff`/`raw_skill_spread` would previously raise
   an obscure TypeError deep inside a scoring loop if any player's rating
   was None or otherwise non-numeric. Fixed by validating at construction
   time instead, in `Player.__post_init__`, with a clear error message."""

import pytest

from helpers import Player
from algorithm import raw_partner_repeats, raw_opponent_repeats, assign_courts_for_round


def test_player_id_defaults_to_name():
    p = Player(name="Sam", gender="FEMALE", rating=5)
    assert p.player_id == "Sam"


def test_player_id_explicit_override_is_respected():
    p = Player(name="Sam", gender="FEMALE", rating=5, player_id="row_42")
    assert p.player_id == "row_42"
    assert p.name == "Sam"  # display name is untouched by a distinct id


def test_distinct_players_sharing_a_name_are_not_conflated():
    """Sam already partnered with one 'Alex' (player_id 'alex_1'). A
    DIFFERENT physical person who also happens to be named 'Alex'
    (player_id 'alex_2') must not be flagged as a repeat -- this is the
    exact collision that existed before player_id was introduced, when
    both would have keyed on the string 'Alex'."""
    sam = Player(name="Sam", gender="FEMALE", rating=5)
    alex_1 = Player(name="Alex", gender="MALE", rating=5, player_id="alex_1")
    alex_2 = Player(name="Alex", gender="MALE", rating=9, player_id="alex_2")
    filler_a = Player(name="X", gender="MALE", rating=5)
    filler_b = Player(name="Y", gender="FEMALE", rating=5)

    sam.partners_played.add(alex_1.player_id)
    alex_1.partners_played.add(sam.player_id)

    # True repeat is still correctly detected...
    assert raw_partner_repeats((sam, alex_1), (filler_a, filler_b)) == 1
    # ...but the same-named, different person is not falsely flagged.
    assert raw_partner_repeats((sam, alex_2), (filler_a, filler_b)) == 0


def test_distinct_players_sharing_a_name_are_not_conflated_for_opponents():
    sam = Player(name="Sam", gender="FEMALE", rating=5)
    alex_1 = Player(name="Alex", gender="MALE", rating=5, player_id="alex_1")
    alex_2 = Player(name="Alex", gender="MALE", rating=9, player_id="alex_2")
    filler = Player(name="X", gender="MALE", rating=5)

    sam.opponents_played.add(alex_1.player_id)
    alex_1.opponents_played.add(sam.player_id)

    assert raw_opponent_repeats((sam, filler), (alex_1, filler)) == 1
    assert raw_opponent_repeats((sam, filler), (alex_2, filler)) == 0


def test_full_round_does_not_conflate_same_named_players():
    """Integration-level sanity check: two distinct Players sharing a name
    run through the real assign_courts_for_round pipeline across two
    rounds without their independent histories bleeding into each other."""
    alex_1 = Player(name="Alex", gender="MALE", rating=5, player_id="alex_1")
    alex_2 = Player(name="Alex", gender="MALE", rating=6, player_id="alex_2")
    others = [Player(name=f"p{i}", gender="FEMALE", rating=5) for i in range(6)]
    players = [alex_1, alex_2] + others

    for _ in range(2):
        assign_courts_for_round(players, num_courts=2, mixed_mode=0, variety_mode=2)

    # alex_2's own history must only ever contain ids of players alex_2
    # actually played with/against -- never alex_1's history bleeding in
    # via the shared display name "Alex".
    assert "alex_1" not in alex_2.partners_played
    assert "alex_1" not in alex_2.opponents_played
    assert "alex_2" not in alex_1.partners_played
    assert "alex_2" not in alex_1.opponents_played


def test_none_rating_raises_value_error():
    with pytest.raises(ValueError, match="Bad"):
        Player(name="Bad", gender="MALE", rating=None)


def test_non_numeric_rating_raises_value_error():
    with pytest.raises(ValueError):
        Player(name="Bad", gender="MALE", rating="five")


def test_bool_rating_raises_value_error():
    """bool is a subclass of int in Python -- without an explicit check,
    rating=True would silently behave like rating=1."""
    with pytest.raises(ValueError):
        Player(name="Bad", gender="MALE", rating=True)


def test_valid_int_and_float_ratings_construct_fine():
    assert Player(name="Ok", gender="MALE", rating=5).rating == 5
    assert Player(name="Ok", gender="MALE", rating=5.5).rating == 5.5
