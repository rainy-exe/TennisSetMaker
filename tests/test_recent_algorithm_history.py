from datetime import timedelta

from app.extensions import db
from app.models import (
    Player,
    Round,
    RoundEligibility,
    RoundPlayerOutcome,
    TennisSession,
    utc_now,
)
from app.round_service import _history_for, _recent_history_for


def saved_round(
    session,
    player_id,
    court_id,
    number,
    completed_at,
    outcome="playing",
    status="completed",
    generated="sit_out",
):
    """Seed one player's stored facts; API assignment validity is tested separately."""
    round_ = Round(
        session_id=session.id,
        number=number,
        status=status,
        completed_at=completed_at if status == "completed" else None,
    )
    db.session.add(round_)
    db.session.flush()
    for version, result in (("generated", generated), ("final", outcome)):
        db.session.add(
            RoundPlayerOutcome(
                round_id=round_.id,
                player_id=player_id,
                version=version,
                outcome=result,
                court_id=court_id if result == "playing" else None,
                team=1 if result == "playing" else None,
                slot=1 if result == "playing" else None,
            )
        )
    return round_


def test_recent_window_uses_last_50_attended_final_rounds_not_120_days(
    app,
    session_setup,
    client,
):
    prior, players, courts = session_setup(player_count=5, court_count=1, rounds=30)
    response = client.post(
        "/api/sessions",
        json={
            "name": "Another past social",
            "starts_at": "2026-03-02T13:00:00",
            "number_of_rounds": 30,
            "round_duration_minutes": 30,
            "court_ids": [courts[0]["id"]],
            "player_ids": [player["id"] for player in players],
        },
    )
    assert response.status_code == 201
    second_past_id = response.get_json()["session"]["id"]
    # Reuse the same stable players across a new session.
    response = client.post(
        "/api/sessions",
        json={
            "name": "Next social",
            "starts_at": "2026-10-05T13:00:00",
            "number_of_rounds": 3,
            "round_duration_minutes": 30,
            "round_format": "open",
            "court_ids": [courts[0]["id"]],
            "player_ids": [player["id"] for player in players],
        },
    )
    assert response.status_code == 201
    current_id = response.get_json()["session"]["id"]
    with app.app_context():
        past = db.session.get(TennisSession, prior["id"])
        second_past = db.session.get(TennisSession, second_past_id)
        old_time = utc_now() - timedelta(days=200)
        for number in range(1, 56):
            outcome = "sit_out" if number <= 5 or number in (15, 25) else "playing"
            saved_round(
                past if number <= 30 else second_past,
                players[0]["id"],
                courts[0]["id"],
                number if number <= 30 else number - 30,
                old_time + timedelta(minutes=number),
                outcome,
            )
        # Missing rounds have eligibility facts but no playing/sit-out outcome.
        for number, status in enumerate(
            ("not_arrived", "unavailable", "missing_skill"), 26
        ):
            round_ = Round(
                session_id=second_past.id,
                number=number,
                status="completed",
                completed_at=utc_now() - timedelta(minutes=number),
            )
            db.session.add(round_)
            db.session.flush()
            db.session.add(
                RoundEligibility(
                    round_id=round_.id, player_id=players[0]["id"], status=status
                )
            )
        saved_round(
            second_past,
            players[0]["id"],
            courts[0]["id"],
            29,
            utc_now(),
            outcome="sit_out",
            status="generated",
        )
        db.session.commit()
        current = db.session.get(TennisSession, current_id)
        player = db.session.get(Player, players[0]["id"])
        history = _history_for(current, [player], 1)[0]
        assert history.recent_rounds == 50
        assert (
            history.recent_sit_outs == 2
        )  # First five sit-outs fell out of the window.
        assert history.session_sit_outs == 0
        assert history.sit_out_count == 0  # Past sessions don't count here; only this one.
        assert history.rounds_played == 0
        assert _recent_history_for("another-club", {player.id})[player.id] == {
            "sit_outs": 0,
            "rounds": 0,
        }


def test_session_count_resets_but_recent_window_keeps_past_sessions(
    app,
    client,
    session_setup,
):
    first, players, courts = session_setup(player_count=5, court_count=1, rounds=2)
    generated = client.post(f"/api/sessions/{first['id']}/rounds/generate").get_json()[
        "round"
    ]
    assert client.post(f"/api/rounds/{generated['id']}/complete").status_code == 200
    sit_out_id = generated["final"]["sit_out_player_ids"][0]
    with app.app_context():
        player = db.session.get(Player, sit_out_id)
        session = db.session.get(TennisSession, first["id"])
        history = _history_for(session, [player], 2)[0]
        assert (
            history.session_sit_outs,
            history.recent_sit_outs,
            history.recent_rounds,
        ) == (1, 1, 1)
        assert _recent_history_for(session.club_id, set()) == {}
    response = client.post(
        "/api/sessions",
        json={
            "name": "Another day",
            "starts_at": "2026-10-06T13:00:00",
            "number_of_rounds": 2,
            "round_duration_minutes": 30,
            "court_ids": [courts[0]["id"]],
            "player_ids": [player["id"] for player in players],
        },
    )
    assert response.status_code == 201
    with app.app_context():
        current = db.session.get(TennisSession, response.get_json()["session"]["id"])
        history = _history_for(current, [db.session.get(Player, sit_out_id)], 1)[0]
        assert history.session_sit_outs == 0
        assert history.consecutive_sit_outs == 0
        assert history.recent_sit_outs == 1
        assert history.recent_rounds == 1


def test_reopening_removes_round_from_ratio_until_completed_again(
    app,
    client,
    session_setup,
):
    session, _, _ = session_setup(player_count=5, court_count=1, rounds=2)
    first = client.post(f"/api/sessions/{session['id']}/rounds/generate").get_json()[
        "round"
    ]
    assert client.post(f"/api/rounds/{first['id']}/complete").status_code == 200
    player_id = first["final"]["sit_out_player_ids"][0]
    assert client.post(f"/api/rounds/{first['id']}/reopen").status_code == 200
    with app.app_context():
        history = _history_for(
            db.session.get(TennisSession, session["id"]),
            [db.session.get(Player, player_id)],
            1,
        )[0]
        assert (
            history.session_sit_outs,
            history.recent_sit_outs,
            history.recent_rounds,
        ) == (0, 0, 0)
    assert client.post(f"/api/rounds/{first['id']}/complete").status_code == 200
    with app.app_context():
        history = _history_for(
            db.session.get(TennisSession, session["id"]),
            [db.session.get(Player, player_id)],
            2,
        )[0]
        assert (
            history.session_sit_outs,
            history.recent_sit_outs,
            history.recent_rounds,
        ) == (1, 1, 1)
