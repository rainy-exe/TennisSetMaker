"""Closeout paths with migrations, real login/CSRF/FKs and bundled Prototype4."""

from copy import deepcopy
from io import BytesIO

import pytest
from flask_migrate import upgrade
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app import create_app
from app.algorithm_adapter import PrototypeAlgorithmEngine, get_algorithm_engine
from app.extensions import db
from app.models import Club, User
from tests.test_accounts_integration import register, write
from tests.test_migrations import MIGRATIONS
from tests.test_page_integration import page_data


@pytest.fixture
def live_backend(tmp_path):
    application = create_app(
        {
            "TESTING": True,
            "SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp_path / 'closeout.db'}",
            "RECAPTCHA_SECRET_KEY": None,
            "ALGORITHM_RANDOM_SEED": 17,
        }
    )
    with application.app_context():
        upgrade(directory=MIGRATIONS)
        assert db.session.execute(text("PRAGMA foreign_keys")).scalar() == 1
        assert get_algorithm_engine().algorithm_path.name == "Prototype4"
    client = application.test_client()
    owner = register(client, "closeout@example.invalid", "Synthetic Closeout Club")
    token = client.get("/csrf-token").get_json()["csrf_token"]
    names = [
        "Aster",
        "Birch",
        "Cedar",
        "Dahlia",
        "Elm",
        "Fern",
        "Ginkgo",
        "Hazel",
        "Iris",
        "Juniper",
    ]
    rows = ["Name,Gender,Rating"] + [
        f"{name},{'M' if i < 5 else 'F'},5" for i, name in enumerate(names)
    ]
    imported = client.post(
        "/api/players/import",
        data={"csv_file": (BytesIO("\n".join(rows).encode()), "synthetic.csv")},
        headers={"X-CSRFToken": token},
    )
    assert imported.status_code == 302
    assert "imported=10" in imported.location
    players = sorted(
        client.get("/api/players").get_json()["players"], key=lambda p: p["name"]
    )
    court = write(client, "POST", "/api/courts", {"number": 1, "court_type": "Hard"})
    assert court.status_code == 201
    yield application, client, owner, players, court.get_json()["court"]
    with application.app_context():
        db.session.remove()
        db.engine.dispose()


def create_session(client, players, court, round_format="open"):
    response = write(
        client,
        "POST",
        "/api/sessions",
        {
            "name": f"Synthetic {round_format} closeout",
            "starts_at": "2026-10-11T13:00:00",
            "number_of_rounds": 3,
            "round_duration_minutes": 30,
            "round_format": round_format,
            "strict_format": False,
            "court_ids": [court["id"]],
            "player_ids": [p["id"] for p in players[:9]],
        },
    )
    assert response.status_code == 201, response.get_json()
    return response.get_json()["session"]


@pytest.mark.parametrize(
    "round_format,mode", [("open", 0), ("mixed", 1), ("same_gender", 2)]
)
def test_csv_override_live_changes_end_and_restart_use_actual_history(
    live_backend, monkeypatch, round_format, mode
):
    application, client, owner, players, court = live_backend
    calls = []
    real_generate = PrototypeAlgorithmEngine.generate

    def capture(engine, eligible, court_ids, number, gender_mode, strict):
        calls.append((deepcopy(eligible), gender_mode))
        return real_generate(engine, eligible, court_ids, number, gender_mode, strict)

    monkeypatch.setattr(PrototypeAlgorithmEngine, "generate", capture)
    session = create_session(client, players, court, round_format)
    generated = write(client, "POST", f"/api/sessions/{session['id']}/rounds/generate")
    assert generated.status_code == 201, generated.get_json()
    first = generated.get_json()["round"]
    final = deepcopy(first["final"])
    now_playing = final["sit_out_player_ids"].pop()
    now_sitting = final["assignments"][0]["team_a_player_ids"][0]
    final["assignments"][0]["team_a_player_ids"][0] = now_playing
    final["sit_out_player_ids"].append(now_sitting)
    saved = write(client, "PUT", f"/api/rounds/{first['id']}/final", final)
    assert saved.status_code == 200
    persisted_final = saved.get_json()["round"]["final"]
    assert persisted_final["assignments"] == final["assignments"]
    assert set(persisted_final["sit_out_player_ids"]) == set(
        final["sit_out_player_ids"]
    )
    final = persisted_final
    assert (
        write(client, "POST", f"/api/rounds/{first['id']}/complete").status_code == 200
    )
    assert (
        write(
            client,
            "POST",
            f"/api/sessions/{session['id']}/players",
            {"player_id": players[9]["id"]},
        ).status_code
        == 201
    )
    leaving = final["assignments"][0]["team_b_player_ids"][0]
    assert (
        write(
            client,
            "PATCH",
            f"/api/sessions/{session['id']}/players/{leaving}",
            {"reason": "Synthetic early leave"},
        ).status_code
        == 200
    )
    second = write(client, "POST", f"/api/sessions/{session['id']}/rounds/generate")
    assert second.status_code == 201, second.get_json()
    round_two = second.get_json()["round"]
    active_page = client.get(f"/sessions/{session['id']}")
    assert active_page.status_code == 200
    for link in ("history", "settings"):
        assert f'href="/{link}" class="nav-link"' in active_page.text
        assert f'href="/{link}" aria-disabled="true"' not in active_page.text
    pool = {p.id: p for p in calls[-1][0]}
    assert calls[-1][1] == mode
    assert players[9]["id"] in pool and leaving not in pool
    assert pool[now_sitting].session_sit_outs == 1
    assert pool[now_playing].session_sit_outs == 0
    actual_partner = final["assignments"][0]["team_a_player_ids"][1]
    assert actual_partner in pool[now_playing].partner_ids
    unavailable = next(e for e in round_two["eligibility"] if e["player_id"] == leaving)
    assert unavailable["status"] == "unavailable"
    assert leaving not in round_two["final"]["sit_out_player_ids"]
    ended = write(client, "POST", f"/api/sessions/{session['id']}/end")
    assert ended.status_code == 200, ended.get_json()
    closed = ended.get_json()["session"]
    assert closed["status"] == "completed"
    assert len(closed["rounds"]) == 2  # Third planned round is not invented.
    assert all(r["status"] == "completed" for r in closed["rounds"])
    assert closed["rounds"][0]["generated"] == first["generated"]
    assert closed["rounds"][0]["final"] == final
    assert closed["rounds"][1]["final"] == round_two["final"]
    assert (
        write(client, "POST", f"/api/sessions/{session['id']}/end").status_code == 409
    )
    assert client.get(f"/api/sessions/{session['id']}").get_json()["session"] == closed
    assert (
        write(client, "PUT", f"/api/rounds/{first['id']}/final", final).status_code
        == 409
    )
    assert (
        write(
            client, "POST", f"/api/sessions/{session['id']}/rounds/generate"
        ).status_code
        == 409
    )
    assert (
        write(
            client,
            "PATCH",
            f"/api/sessions/{session['id']}/players/{now_playing}",
            {"reason": "Too late"},
        ).status_code
        == 409
    )
    assert page_data(client.get("/history"))["page"] == "history"
    with application.app_context():
        public_token = db.session.get(Club, owner["club_id"]).public_token
    public = application.test_client().get(f"/club/{public_token}/live")
    assert public.status_code == 200
    assert "Round 2" in public.text and "gender-dot" in public.text
    assert "Synthetic early leave" not in public.text
    restarted = create_app({**application.config, "ALGORITHM_RANDOM_SEED": 17})
    fresh_client = restarted.test_client()
    assert fresh_client.get(f"/api/sessions/{session['id']}").status_code == 401
    assert (
        write(
            fresh_client,
            "POST",
            "/login",
            {"email": "closeout@example.invalid", "password": "Synthetic-password"},
        ).status_code
        == 200
    )
    assert (
        fresh_client.get(f"/api/sessions/{session['id']}").get_json()["session"]
        == closed
    )
    assert (
        page_data(fresh_client.get(f"/sessions/{session['id']}"))["session"] == closed
    )
    with restarted.app_context():
        assert not db.session.execute(text("PRAGMA foreign_key_check")).all()
        db.session.remove()
        db.engine.dispose()


@pytest.mark.parametrize("role", ["owner", "admin", "staff"])
def test_end_permissions_csrf_club_isolation_and_reopen(live_backend, role):
    application, client, owner, players, court = live_backend
    session = create_session(client, players, court)
    generated = write(
        client, "POST", f"/api/sessions/{session['id']}/rounds/generate"
    ).get_json()["round"]
    endpoint = f"/api/sessions/{session['id']}/end"
    assert client.post(endpoint).get_json()["error"] == "csrf_failed"
    anonymous = application.test_client()
    assert write(anonymous, "POST", endpoint).status_code == 401
    other = application.test_client()
    register(other, "other-closeout@example.invalid", "Synthetic Other Club")
    assert write(other, "POST", endpoint).status_code == 404
    with application.app_context():
        db.session.get(User, owner["id"]).role = role
        db.session.commit()
    assert write(client, "POST", endpoint).status_code == 200
    before = client.get(f"/api/sessions/{session['id']}").get_json()["session"]
    reopened = write(client, "POST", f"/api/rounds/{generated['id']}/reopen")
    assert reopened.status_code == (403 if role == "staff" else 200)
    after = client.get(f"/api/sessions/{session['id']}").get_json()["session"]
    if role == "staff":
        assert after == before
    else:
        assert after["status"] == "active"
        assert after["rounds"][0]["completed_at"] is None
        assert after["rounds"][0]["generated"] == generated["generated"]


def test_end_database_failure_rolls_back_round_and_session(live_backend, monkeypatch):
    _, client, _, players, court = live_backend
    session = create_session(client, players, court)
    assert (
        write(
            client, "POST", f"/api/sessions/{session['id']}/rounds/generate"
        ).status_code
        == 201
    )
    before = client.get(f"/api/sessions/{session['id']}").get_json()["session"]

    def fail_after_flush():
        db.session.flush()
        raise SQLAlchemyError("Synthetic commit failure")

    with monkeypatch.context() as patch:
        patch.setattr(db.session, "commit", fail_after_flush)
        failed = write(client, "POST", f"/api/sessions/{session['id']}/end")
    assert failed.status_code == 500
    assert failed.get_json()["error"] == "session_persistence_failed"
    assert client.get(f"/api/sessions/{session['id']}").get_json()["session"] == before
    assert (
        write(client, "POST", f"/api/sessions/{session['id']}/end").status_code == 200
    )


def test_ending_draft_does_not_invent_played_history(live_backend):
    _, client, _, players, court = live_backend
    session = create_session(client, players, court)
    ended = write(client, "POST", f"/api/sessions/{session['id']}/end")
    assert ended.status_code == 200
    assert ended.get_json()["session"]["rounds"] == []
    assert ended.get_json()["session"]["status"] == "completed"


def test_history_pages_include_latest_pagination_and_one_initializer(client):
    page = client.get("/history")
    assert page.status_code == 200
    assert page.text.count('id="setmaker-page-data"') == 1
    assert "history-pagination" in page.text
    script = client.get("/static/js/integration.js").text
    assert script.count('if (data.page === "history")') == 1
    assert 'data.page === "sessions" || data.page === "history"' not in script
    assert "Math.ceil(filtered.length / pageSize)" in script
    assert 'same_gender: "Same-Gender Doubles"' in script
