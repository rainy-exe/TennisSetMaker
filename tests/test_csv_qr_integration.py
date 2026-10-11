"""Regression coverage for Kate's CSV and token-scoped results integration."""

from copy import deepcopy
from io import BytesIO
from urllib.parse import parse_qs, urlsplit

import pytest
from alembic.script import ScriptDirectory
from flask_migrate import downgrade, upgrade
from PIL import Image
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.extensions import db
from app.models import Club, Player, User
from tests.test_accounts_integration import register
from tests.test_migrations import MIGRATIONS, migration_app

pytest_plugins = ("tests.test_accounts_integration",)


def upload(client, content, filename="synthetic.csv", **kwargs):
    return client.post(
        "/api/players/import",
        data={"csv_file": (BytesIO(content), filename)},
        **kwargs,
    )


def import_result(response):
    assert response.status_code == 302
    return parse_qs(urlsplit(response.location).query)


def test_csv_bom_optional_details_and_real_session_generation(client):
    response = upload(
        client,
        (
            "\ufeff Name , Gender , Rating ,Notes,Court Preference\n"
            "Synthetic Alpha,Male,1,Left handed,Hard\n"
            "Synthetic Bravo,FEMALE,10,,Clay\n"
            "Synthetic Charlie,M,5,,\n"
            "Synthetic Delta,F,6,,\n"
        ).encode(),
    )
    assert import_result(response) == {"imported": ["4"], "skipped": ["0"]}
    players = client.get("/api/players").get_json()["players"]
    assert len({p["id"] for p in players}) == 4
    alpha = next(p for p in players if p["name"] == "Synthetic Alpha")
    assert (alpha["skill"], alpha["notes"], alpha["court_preference"]) == (
        1,
        "Left handed",
        "Hard",
    )
    court = client.post(
        "/api/courts", json={"number": 1, "court_type": "Hard"}
    ).get_json()["court"]
    session = client.post(
        "/api/sessions",
        json={
            "name": "Synthetic imported roster",
            "starts_at": "2026-10-10T13:00:00",
            "number_of_rounds": 1,
            "round_duration_minutes": 30,
            "court_ids": [court["id"]],
            "player_ids": [p["id"] for p in players],
        },
    )
    assert session.status_code == 201
    generated = client.post(
        f"/api/sessions/{session.get_json()['session']['id']}/rounds/generate"
    )
    assert generated.status_code == 201
    assert len(generated.get_json()["round"]["final"]["assignments"]) == 1


@pytest.mark.parametrize(
    "row",
    [
        "Too few,M",
        "Extra,M,5,unexpected",
        "No gender,,5",
        "Bad gender,X,5",
        "Low,M,0",
        "High,M,11",
        "Fraction,M,2.5",
        "No skill,M,",
    ],
)
def test_bad_csv_rows_are_skipped_not_server_errors(client, row):
    response = upload(
        client, f"Name,Gender,Rating\n{row}\nValid synthetic,F,5\n".encode()
    )
    assert import_result(response) == {"imported": ["1"], "skipped": ["1"]}
    assert [p["name"] for p in client.get("/api/players").get_json()["players"]] == [
        "Valid synthetic"
    ]


@pytest.mark.parametrize(
    "content,filename",
    [
        (b"", "empty.csv"),
        (b"Name,Gender\nA,M\n", "missing.csv"),
        (b"Name,name,Gender,Rating\nA,A,M,5\n", "duplicates.csv"),
        (b"Name,Gender,Rating\nA,M,5", "wrong.txt"),
        (b"Name,Gender,Rating\n\xff,M,5", "encoding.csv"),
        (b'Name,Gender,Rating\nValid,M,5\n"unfinished', "broken.csv"),
        (b"x" * (2 * 1024 * 1024 + 1), "large.csv"),
    ],
    ids=[
        "empty",
        "missing-columns",
        "duplicate-columns",
        "wrong-extension",
        "encoding",
        "broken-quotes",
        "too-large",
    ],
)
def test_invalid_csv_file_writes_nothing(client, content, filename):
    assert "import_error" in import_result(upload(client, content, filename))
    assert client.get("/api/players").get_json()["players"] == []


def test_csv_no_file_and_over_row_limit_are_atomic(client):
    assert "import_error" in import_result(client.post("/api/players/import"))
    content = "Name,Gender,Rating\n" + "".join(
        f"Synthetic {i},M,5\n" for i in range(1001)
    )
    assert "import_error" in import_result(upload(client, content.encode()))
    assert db.session.query(Player).count() == 0


def test_csv_duplicates_do_not_cross_clubs_and_namesakes_can_be_confirmed(
    client, create_player, set_actor
):
    create_player("Jordan Smith", "M")
    set_actor(club_id="club-b")
    create_player("Club B Person", "F")
    set_actor(club_id="club-a")
    response = upload(
        client,
        b"Name,Gender,Rating\nJordan Smith,M,5\nJORDAN SMITH,F,5\nClub B Person,F,5\n",
    )
    assert import_result(response) == {"imported": ["1"], "skipped": ["2"]}
    # CSV keeps Kate's skip policy; individual CRUD permits confirmed namesakes.
    duplicate = client.post(
        "/api/players", json={"name": "Jordan Smith", "gender": "F", "skill": 5}
    )
    assert duplicate.status_code == 409
    confirmed = client.post(
        "/api/players",
        json={
            "name": "Jordan Smith",
            "gender": "F",
            "skill": 5,
            "confirm_duplicate": True,
        },
    )
    assert confirmed.status_code == 201
    assert len({p["id"] for p in client.get("/api/players").get_json()["players"]}) == 3


def test_csv_database_failure_rolls_back_the_whole_import(client, monkeypatch):
    real_commit = db.session.commit

    def fail_commit():
        db.session.flush()
        raise IntegrityError("synthetic insert", {}, Exception("synthetic failure"))

    monkeypatch.setattr(db.session, "commit", fail_commit)
    response = upload(client, b"Name,Gender,Rating\nAlpha,M,5\nBravo,F,5\n")
    assert "Database error" in import_result(response)["import_error"][0]
    assert db.session.query(Player).count() == 0
    monkeypatch.setattr(db.session, "commit", real_commit)
    assert import_result(upload(client, b"Name,Gender,Rating\nAlpha,M,5\n"))[
        "imported"
    ] == ["1"]


def test_csv_uses_real_login_csrf_and_manager_permissions(real_app):
    client = real_app.test_client()
    register(client)
    csv = b"Name,Gender,Rating\nSynthetic Import,M,5\n"
    assert upload(client, csv).get_json()["error"] == "csrf_failed"
    token = client.get("/csrf-token").get_json()["csrf_token"]
    assert import_result(upload(client, csv, headers={"X-CSRFToken": token}))[
        "imported"
    ] == ["1"]
    assert 'action="/api/players/import"' in client.get("/players").text
    with real_app.app_context():
        user = db.session.query(User).one()
        user.role = "staff"
        db.session.commit()
    assert upload(client, csv, headers={"X-CSRFToken": token}).status_code == 403
    assert 'id="csv-import-form"' not in client.get("/players").text


def test_qr_png_encodes_configured_reachable_address_and_requires_login(
    real_app, monkeypatch
):
    client = real_app.test_client()
    assert client.get("/club/qr-code").status_code == 401
    user = register(client)
    with real_app.app_context():
        token = db.session.get(Club, user["club_id"]).public_token
    from app.routes import qrcode

    original = qrcode.QRCode.add_data
    encoded = []

    def capture(self, value, *args, **kwargs):
        encoded.append(value)
        return original(self, value, *args, **kwargs)

    monkeypatch.setattr(qrcode.QRCode, "add_data", capture)
    monkeypatch.setenv("PUBLIC_BASE_URL", "http://192.0.2.10:5000/")
    response = client.get("/club/qr-code")
    assert response.status_code == 200
    assert response.mimetype == "image/png"
    Image.open(BytesIO(response.data)).verify()
    assert encoded == [f"http://192.0.2.10:5000/club/{token}/live"]
    assert "no-store" in response.headers["Cache-Control"]
    monkeypatch.setenv("PUBLIC_BASE_URL", "javascript:invalid")
    assert client.get("/club/qr-code").status_code == 503


def test_token_results_show_saved_final_override_not_generated_or_private_fields(
    app, client, session_setup, set_actor, monkeypatch
):
    club = Club(id="club-a", name="Synthetic Club")
    db.session.add(club)
    db.session.commit()
    session, players, _ = session_setup(player_count=5, court_count=1)
    generated = client.post(
        f"/api/sessions/{session['id']}/rounds/generate"
    ).get_json()["round"]
    final = deepcopy(generated["final"])
    sitout = final["sit_out_player_ids"][0]
    played = final["assignments"][0]["team_a_player_ids"][0]
    final["assignments"][0]["team_a_player_ids"][0] = sitout
    final["sit_out_player_ids"] = [played]
    assert (
        client.put(f"/api/rounds/{generated['id']}/final", json=final).status_code
        == 200
    )
    saved = client.get(f"/api/rounds/{generated['id']}").get_json()["round"]
    assert saved["generated"] == generated["generated"]
    assert saved["final"] == final
    # A newer session in another club must not replace the token's own results.
    db.session.add(Club(id="club-b", name="Private Other Club"))
    db.session.commit()
    set_actor(club_id="club-b")
    other, _, _ = session_setup(player_count=4, court_count=1)
    assert (
        client.post(f"/api/sessions/{other['id']}/rounds/generate").status_code == 201
    )
    from app import routes

    render = routes.render_template
    displayed = {}

    def capture(template, **context):
        displayed.update(context.get("result", {}))
        return render(template, **context)

    monkeypatch.setattr(routes, "render_template", capture)
    app.actor_state["actor"] = None
    response = client.get(f"/club/{club.public_token}/live")
    assert response.status_code == 200
    assert displayed["club_name"] == "Synthetic Club"
    assert "Private Other Club" not in response.text
    names = {p["id"]: p["name"] for p in players}
    genders = {p["id"]: p["gender"] for p in players}
    assert displayed["matches"][0]["team_a"] == [
        {"name": names[p], "gender": genders[p]}
        for p in final["assignments"][0]["team_a_player_ids"]
    ]
    assert displayed["sit_out_players"] == [
        {"name": names[played], "gender": genders[played]}
    ]
    assert all(
        set(player) == {"name", "gender"}
        for match in displayed["matches"]
        for player in match["team_a"] + match["team_b"]
    )
    assert (
        not {"skill", "notes", "email", "password_hash", "generated"} & displayed.keys()
    )
    assert "no-store" in response.headers["Cache-Control"]
    assert response.headers["Referrer-Policy"] == "no-referrer"
    assert client.get("/club/not-a-token/live").status_code == 404
    club.active = False
    db.session.commit()
    assert client.get(f"/club/{club.public_token}/live").status_code == 404


def test_qr_backfill_keeps_existing_tokens_and_has_one_migration_head(tmp_path):
    application = migration_app(tmp_path / "qr-upgrade.db")
    with application.app_context():
        upgrade(directory=MIGRATIONS, revision="187dfc1e85e4")
        for club_id, token in [
            ("new-token", None),
            ("existing-token", "synthetic-existing-token"),
        ]:
            db.session.execute(
                text(
                    "INSERT INTO clubs(id,name,active,public_token,created_at,updated_at) VALUES (:id,'Synthetic',1,:token,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)"
                ),
                {"id": club_id, "token": token},
            )
        db.session.commit()
        upgrade(directory=MIGRATIONS)
        tokens = dict(
            db.session.execute(text("SELECT id,public_token FROM clubs")).all()
        )
        assert len(tokens["new-token"]) == 32
        assert tokens["existing-token"] == "synthetic-existing-token"
        assert not db.session.execute(text("PRAGMA foreign_key_check")).all()
        downgrade(directory=MIGRATIONS, revision="c82e741bd509")
        upgrade(directory=MIGRATIONS)
        assert tokens == dict(
            db.session.execute(text("SELECT id,public_token FROM clubs")).all()
        )
        assert ScriptDirectory(MIGRATIONS).get_heads() == ["d947ef8621ab"]
        check = application.test_cli_runner().invoke(args=["db", "check"])
        assert check.exit_code == 0, check.output
