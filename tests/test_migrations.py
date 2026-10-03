import sqlite3
from pathlib import Path

from flask_migrate import upgrade
from sqlalchemy import inspect, text

from app import create_app
from app.extensions import db

MIGRATIONS = str(Path(__file__).resolve().parents[1] / "migrations")


def migration_app(path):
    return create_app({"TESTING": True, "SQLALCHEMY_DATABASE_URI": f"sqlite:///{path}"})


def test_fresh_database_upgrade_schema_and_downgrade(tmp_path):
    app = migration_app(tmp_path / "fresh.db")
    with app.app_context():
        upgrade(directory=MIGRATIONS)
        assert {"clubs", "users", "invitations", "rounds"} <= set(
            inspect(db.engine).get_table_names()
        )
        assert not db.session.execute(text("PRAGMA foreign_key_check")).all()
    result = app.test_cli_runner().invoke(args=["db", "check"])
    assert result.exit_code == 0, result.output
    result = app.test_cli_runner().invoke(args=["db", "downgrade", "0be1c6c74d5a"])
    assert result.exit_code == 0, result.output
    with app.app_context():
        assert "clubs" not in inspect(db.engine).get_table_names()
        upgrade(directory=MIGRATIONS)


def test_legacy_upgrade_stops_before_changes_and_preserves_round_history(tmp_path):
    path = tmp_path / "legacy.db"
    app = migration_app(path)
    with app.app_context():
        upgrade(directory=MIGRATIONS, revision="0be1c6c74d5a")
    with sqlite3.connect(path) as connection:
        connection.execute(
            "INSERT INTO players (id, club_id, name, normalized_name, gender, skill, active, created_at, updated_at) VALUES ('p1','legacy-club','Synthetic','synthetic','F',5,1,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)"
        )
        connection.execute(
            "INSERT INTO tennis_sessions (id,club_id,name,starts_at,number_of_rounds,session_duration_minutes,round_duration_minutes,round_format,strict_format,status,created_by,created_at,updated_at) VALUES ('s1','legacy-club','Synthetic Social',CURRENT_TIMESTAMP,1,30,30,'open',0,'completed','legacy-owner',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)"
        )
        connection.execute(
            "INSERT INTO rounds (id,session_id,number,status,override_revision,overridden_by,generated_at,created_at,updated_at) VALUES ('r1','s1',1,'completed',1,'legacy-owner',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)"
        )
        for version in ("generated", "final"):
            connection.execute(
                "INSERT INTO round_player_outcomes (id,round_id,player_id,version,outcome) VALUES (?,'r1','p1',?,'sit_out')",
                (version, version),
            )
        before = list(connection.iterdump())
    with app.app_context():
        result = app.test_cli_runner().invoke(args=["db", "upgrade"])
        assert result.exit_code == 1
        assert "explicit legacy club/user ID mapping" in result.output
        db.session.remove()
    with sqlite3.connect(path) as connection:
        assert (
            connection.execute("SELECT version_num FROM alembic_version").fetchone()[0]
            == "0be1c6c74d5a"
        )
        assert before == list(connection.iterdump())
