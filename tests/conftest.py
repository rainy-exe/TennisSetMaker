import pytest

from app import create_app
from app.algorithm_adapter import AlgorithmRound, CourtAssignment
from app.extensions import db
from app.permissions import CurrentActor, Role


class FakeAlgorithm:
    def __init__(self):
        self.calls = []
        self.invalid_result = False

    def generate(
        self,
        players,
        court_ids,
        round_number,
        mixed_mode,
        strict_format,
    ):
        self.calls.append(
            {
                "players": players,
                "court_ids": court_ids,
                "round_number": round_number,
                "mixed_mode": mixed_mode,
                "strict_format": strict_format,
            }
        )

        if self.invalid_result:
            player_id = players[0].id

            return AlgorithmRound(
                [
                    CourtAssignment(
                        court_ids[0],
                        [player_id, player_id],
                        [player_id, player_id],
                    )
                ],
                [],
            )

        play_count = min(
            len(players) // 4,
            len(court_ids),
        ) * 4

        playing = players[:play_count]

        assignments = []

        for index in range(0, play_count, 4):
            group = playing[index:index + 4]

            assignments.append(
                CourtAssignment(
                    court_ids[index // 4],
                    [group[0].id, group[1].id],
                    [group[2].id, group[3].id],
                )
            )

        return AlgorithmRound(
            assignments,
            [
                player.id
                for player in players[play_count:]
            ],
        )


@pytest.fixture
def app(tmp_path):
    state = {
        "actor": CurrentActor(
            "owner-1",
            "club-a",
            Role.OWNER,
        ),
    }

    algorithm = FakeAlgorithm()

    application = create_app(
        {
            "TESTING": True,

            # Disable CSRF only during automated tests.
            "WTF_CSRF_ENABLED": False,
            # These unit fixtures use opaque fake club/actor IDs. Real account
            # integration tests use the production FK and CSRF defaults.
            "SQLITE_FOREIGN_KEYS": False,

            "SQLALCHEMY_DATABASE_URI": (
                f"sqlite:///{tmp_path / 'test.db'}"
            ),

            # Tests provide their own fake authenticated actor.
            "CURRENT_ACTOR_PROVIDER": (
                lambda: state["actor"]
            ),

            "ALGORITHM_ENGINE": algorithm,

            "HISTORY_WINDOW_DAYS": 120,
        }
    )

    application.actor_state = state
    application.fake_algorithm = algorithm

    with application.app_context():
        db.create_all()

        yield application

        db.session.remove()
        db.drop_all()


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def set_actor(app):
    def setter(
        role=Role.OWNER,
        club_id="club-a",
        actor_id="actor-1",
    ):
        app.actor_state["actor"] = CurrentActor(
            actor_id,
            club_id,
            role,
        )

    return setter


@pytest.fixture
def create_player(client):
    counter = {
        "value": 0,
    }

    def create(
        name=None,
        gender=None,
        skill=5,
        include_skill=True,
    ):
        counter["value"] += 1

        payload = {
            "name": (
                name
                or f"Player {counter['value']}"
            ),
            "gender": (
                gender
                or (
                    "M"
                    if counter["value"] % 2
                    else "F"
                )
            ),
        }

        # Owner/Admin can create a player with a skill.
        #
        # Staff can create players but cannot set skill,
        # so Staff tests can call:
        #
        # create_player(include_skill=False)
        if include_skill:
            payload["skill"] = skill

        response = client.post(
            "/api/players",
            json=payload,
        )

        assert response.status_code == 201, (
            response.get_json()
        )

        return response.get_json()["player"]

    return create


@pytest.fixture
def session_setup(
    client,
    create_player,
):
    def create(
        player_count=9,
        court_count=2,
        rounds=3,
    ):
        players = [
            create_player()
            for _ in range(player_count)
        ]

        courts = []

        for number in range(
            1,
            court_count + 1,
        ):
            response = client.post(
                "/api/courts",
                json={
                    "number": number,
                    "court_type": "hard",
                    "priority": number - 1,
                },
            )

            assert response.status_code == 201

            courts.append(
                response.get_json()["court"]
            )

        response = client.post(
            "/api/sessions",
            json={
                "name": "Saturday Social",
                "starts_at": "2026-09-12T13:00:00",
                "number_of_rounds": rounds,
                "round_duration_minutes": 30,
                "round_format": "open",
                "strict_format": False,
                "court_ids": [
                    court["id"]
                    for court in courts
                ],
                "player_ids": [
                    player["id"]
                    for player in players
                ],
            },
        )

        assert response.status_code == 201, (
            response.get_json()
        )

        return (
            response.get_json()["session"],
            players,
            courts,
        )

    return create
