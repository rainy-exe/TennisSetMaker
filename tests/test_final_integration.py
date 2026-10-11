"""Synthetic tests of the latest frontend/backend/Prototype4 contracts."""

from copy import deepcopy
from pathlib import Path

import pytest
from flask_migrate import upgrade
from sqlalchemy import event, text

from app.algorithm_adapter import AlgorithmPlayer, PrototypeAlgorithmEngine
from app.errors import ApiError
from app.extensions import db
from app.models import Round, RoundPlayerOutcome
from app.permissions import Role
from tests.test_migrations import MIGRATIONS, migration_app

P4 = Path(__file__).resolve().parents[1] / "Backend" / "Set_Algorithm" / "Prototype4"


@pytest.mark.parametrize("court_number", [0, -1, 3, True, "1"])
def test_invalid_prototype_court_index_cannot_map_to_a_real_court(
    court_number, monkeypatch
):
    engine = PrototypeAlgorithmEngine(P4, seed=17)
    engine._load()

    def invalid_output(players, *args):
        return [{"court": court_number, "team_a": players[:2], "team_b": players[2:4]}]

    monkeypatch.setattr(engine._algorithm, "assign_courts_for_round", invalid_output)
    players = [AlgorithmPlayer(f"synthetic-{i}", "M", 5) for i in range(4)]
    with pytest.raises(ApiError) as error:
        engine.generate(players, ["court-1", "court-2"], 1, 0, False)
    assert error.value.code == "invalid_algorithm_result"
    assert error.value.status_code == 502


@pytest.mark.parametrize("invalid", [[], {}, None, 2, "unknown"])
def test_bad_session_format_is_a_validation_error_not_server_error(
    client, session_setup, invalid
):
    _, players, courts = session_setup(player_count=4, court_count=1)
    response = client.post(
        "/api/sessions",
        json={
            "name": "Invalid Format",
            "starts_at": "2026-10-10T13:00:00",
            "number_of_rounds": 3,
            "round_duration_minutes": 30,
            "round_format": invalid,
            "court_ids": [c["id"] for c in courts],
            "player_ids": [p["id"] for p in players],
        },
    )
    assert response.status_code == 400
    assert len(client.get("/api/sessions").get_json()["sessions"]) == 1


@pytest.mark.parametrize("mode", [0, 1, 2])
def test_gender_mode_reaches_both_algorithm_stages(mode, monkeypatch):
    engine = PrototypeAlgorithmEngine(P4, seed=17)
    engine._load()
    select_players = engine._algorithm.select_round_players
    assign = engine._algorithm.assign_courts_for_round
    calls = []

    def capture_selection(players, num_to_play, rng, mixed_mode=0):
        calls.append(("selection", mixed_mode))
        return select_players(players, num_to_play, rng, mixed_mode=mixed_mode)

    def capture_assignment(players, num_courts, mixed_mode, variety_mode):
        calls.append(("assignment", mixed_mode))
        return assign(players, num_courts, mixed_mode, variety_mode)

    monkeypatch.setattr(engine._algorithm, "select_round_players", capture_selection)
    monkeypatch.setattr(
        engine._algorithm, "assign_courts_for_round", capture_assignment
    )
    players = [
        AlgorithmPlayer(f"stable-{i}", "M" if i < 4 else "F", 5) for i in range(8)
    ]
    original = deepcopy(players)
    result = engine.generate(players, ["court-1", "court-2"], 1, mode, True)
    assert calls == [("selection", mode), ("assignment", mode)]
    assert players == original
    ids = [
        p for a in result.assignments for p in a.team_a + a.team_b
    ] + result.sit_out_player_ids
    assert len(ids) == len(set(ids)) == len(players)


@pytest.mark.parametrize("males,females", [(5, 4), (6, 2), (4, 0)])
def test_same_gender_persists_and_accepts_balanced_spares(
    app, client, create_player, males, females
):
    app.config["ALGORITHM_ENGINE"] = PrototypeAlgorithmEngine(P4, seed=17)
    players = [
        create_player(gender=gender)
        for gender, n in [("M", males), ("F", females)]
        for _ in range(n)
    ]
    courts = [
        client.post(
            "/api/courts", json={"number": i + 1, "court_type": "Hard"}
        ).get_json()["court"]
        for i in range(len(players) // 4)
    ]
    template_response = client.post(
        "/api/session-templates",
        json={
            "name": "Synthetic Same Gender",
            "round_format": "same_gender",
            "strict_format": True,
            "court_ids": [c["id"] for c in courts],
        },
    )
    assert template_response.status_code == 201
    template = template_response.get_json()["session_template"]
    response = client.post(
        "/api/sessions",
        json={
            "name": "Synthetic Same Gender",
            "template_id": template["id"],
            "starts_at": "2026-10-10T13:00:00",
            "player_ids": [p["id"] for p in players],
        },
    )
    assert response.status_code == 201
    session = response.get_json()["session"]
    assert session["round_format"] == "same_gender"
    response = client.post(f"/api/sessions/{session['id']}/rounds/generate")
    assert response.status_code == 201, response.get_json()
    round_ = response.get_json()["round"]
    genders = {p["id"]: p["gender"] for p in players}
    for assignment in round_["final"]["assignments"]:
        for team in (assignment["team_a_player_ids"], assignment["team_b_player_ids"]):
            assert len({genders[p] for p in team}) == 1
    assert (
        client.get(f"/api/rounds/{round_['id']}").get_json()["round"]["final"]
        == round_["final"]
    )
    assert (
        client.put(
            f"/api/rounds/{round_['id']}/final", json=round_["final"]
        ).status_code
        == 200
    )


def test_strict_same_gender_failure_is_atomic_and_relaxation_is_explicit(
    app, client, create_player
):
    app.config["ALGORITHM_ENGINE"] = PrototypeAlgorithmEngine(P4, seed=17)
    players = [create_player(gender="F" if i == 7 else "M") for i in range(8)]
    courts = [
        client.post(
            "/api/courts", json={"number": i + 1, "court_type": "Hard"}
        ).get_json()["court"]
        for i in range(2)
    ]
    session = client.post(
        "/api/sessions",
        json={
            "name": "Synthetic Uneven",
            "starts_at": "2026-10-10T13:00:00",
            "number_of_rounds": 3,
            "round_duration_minutes": 30,
            "round_format": "same_gender",
            "strict_format": True,
            "court_ids": [c["id"] for c in courts],
            "player_ids": [p["id"] for p in players],
        },
    ).get_json()["session"]
    response = client.post(f"/api/sessions/{session['id']}/rounds/generate")
    assert response.status_code == 422
    assert response.get_json()["error"] == "no_valid_solution"
    assert (
        db.session.query(Round).count()
        == db.session.query(RoundPlayerOutcome).count()
        == 0
    )
    assert (
        client.get(f"/api/sessions/{session['id']}").get_json()["session"]["status"]
        == "draft"
    )
    assert (
        client.patch(
            f"/api/sessions/{session['id']}", json={"strict_format": False}
        ).status_code
        == 200
    )
    assert (
        client.post(f"/api/sessions/{session['id']}/rounds/generate").status_code == 201
    )


def test_invalid_same_gender_override_does_not_change_either_snapshot(
    app, client, create_player
):
    app.config["ALGORITHM_ENGINE"] = PrototypeAlgorithmEngine(P4, seed=17)
    players = [create_player(gender="M" if i < 4 else "F") for i in range(8)]
    courts = [
        client.post(
            "/api/courts", json={"number": i + 1, "court_type": "Hard"}
        ).get_json()["court"]
        for i in range(2)
    ]
    session = client.post(
        "/api/sessions",
        json={
            "name": "Synthetic Override",
            "starts_at": "2026-10-10T13:00:00",
            "number_of_rounds": 3,
            "round_duration_minutes": 30,
            "round_format": "same_gender",
            "strict_format": True,
            "court_ids": [c["id"] for c in courts],
            "player_ids": [p["id"] for p in players],
        },
    ).get_json()["session"]
    round_ = client.post(f"/api/sessions/{session['id']}/rounds/generate").get_json()[
        "round"
    ]
    invalid = deepcopy(round_["final"])
    first, second = invalid["assignments"]
    first["team_a_player_ids"][0], second["team_a_player_ids"][0] = (
        second["team_a_player_ids"][0],
        first["team_a_player_ids"][0],
    )
    assert (
        client.put(f"/api/rounds/{round_['id']}/final", json=invalid).status_code == 400
    )
    saved = client.get(f"/api/rounds/{round_['id']}").get_json()["round"]
    assert saved["generated"] == round_["generated"]
    assert saved["final"] == round_["final"]
    assert saved["override_revision"] == 0


def test_pages_include_real_data_once_and_accounts_follow_permissions(
    client, set_actor
):
    for path in (
        "/sessions",
        "/history",
        "/accounts",
        "/templates/add",
        "/sessions/create",
    ):
        response = client.get(path)
        assert response.status_code == 200
        assert response.text.count('id="setmaker-page-data"') == 1
        assert response.text.count("js/integration.js") == 1
        assert "waiting for" not in response.text
    set_actor(Role.STAFF)
    assert client.get("/accounts").status_code == 403
    assert client.get("/sessions").status_code == 200
    assert client.get("/history").status_code == 200


def test_format_migration_rolls_back_both_parent_tables(tmp_path):
    application = migration_app(tmp_path / "failed-format.db")
    with application.app_context():
        upgrade(directory=MIGRATIONS, revision="187dfc1e85e4")
        original = db.session.execute(
            text("SELECT name,sql FROM sqlite_master WHERE type='table' ORDER BY name")
        ).all()
        db.session.remove()

        def fail_second_table(
            conn, cursor, statement, parameters, context, executemany
        ):
            if statement.strip().startswith("DROP TABLE tennis_sessions"):
                raise RuntimeError("Synthetic migration failure")

        event.listen(db.engine, "before_cursor_execute", fail_second_table)
        try:
            result = application.test_cli_runner().invoke(args=["db", "upgrade"])
            assert result.exit_code != 0
        finally:
            event.remove(db.engine, "before_cursor_execute", fail_second_table)
        assert (
            original
            == db.session.execute(
                text(
                    "SELECT name,sql FROM sqlite_master WHERE type='table' ORDER BY name"
                )
            ).all()
        )
        assert (
            db.session.execute(text("SELECT version_num FROM alembic_version")).scalar()
            == "187dfc1e85e4"
        )
        assert db.session.execute(text("PRAGMA foreign_keys")).scalar() == 1
        assert not db.session.execute(text("PRAGMA foreign_key_check")).all()
        db.session.remove()
        upgrade(directory=MIGRATIONS)


def test_downgrade_refuses_populated_full_names(tmp_path):
    application = migration_app(tmp_path / "names.db")
    with application.app_context():
        upgrade(directory=MIGRATIONS)
        db.session.execute(
            text(
                "INSERT INTO clubs(id,name,active,created_at,updated_at) VALUES ('club','Synthetic',1,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)"
            )
        )
        db.session.execute(
            text(
                "INSERT INTO users(id,club_id,email,password_hash,role,active,full_name,created_at,updated_at) VALUES ('user','club','name@example.invalid','synthetic','owner',1,'Synthetic Member',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)"
            )
        )
        db.session.commit()
        result = application.test_cli_runner().invoke(
            args=["db", "downgrade", "b7d482f19c06"]
        )
        assert result.exit_code != 0
        assert "Back up account full names" in result.output
        assert (
            db.session.execute(text("SELECT full_name FROM users")).scalar()
            == "Synthetic Member"
        )
        assert not db.session.execute(text("PRAGMA foreign_key_check")).all()


def test_same_gender_migration_downgrade_preserves_sessions(tmp_path):
    application = migration_app(tmp_path / "same-format.db")
    with application.app_context():
        upgrade(directory=MIGRATIONS)
        db.session.execute(
            text(
                "INSERT INTO clubs(id,name,active,created_at,updated_at) VALUES ('club','Synthetic',1,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)"
            )
        )
        db.session.execute(
            text(
                "INSERT INTO users(id,club_id,email,password_hash,role,active,created_at,updated_at) VALUES ('user','club','name@example.invalid','synthetic','owner',1,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)"
            )
        )
        db.session.execute(
            text(
                "INSERT INTO tennis_sessions(id,club_id,name,starts_at,number_of_rounds,session_duration_minutes,round_duration_minutes,round_format,strict_format,status,created_by,created_at,updated_at) VALUES ('session','club','Synthetic',CURRENT_TIMESTAMP,1,30,30,'same_gender',1,'draft','user',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)"
            )
        )
        db.session.commit()
        result = application.test_cli_runner().invoke(
            args=["db", "downgrade", "5b8fefaec99e"]
        )
        assert result.exit_code != 0
        assert "must be backed up" in result.output
        assert (
            db.session.execute(
                text("SELECT round_format FROM tennis_sessions")
            ).scalar()
            == "same_gender"
        )
        assert (
            db.session.execute(text("SELECT version_num FROM alembic_version")).scalar()
            == "c82e741bd509"
        )
