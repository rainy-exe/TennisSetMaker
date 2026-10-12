from app.permissions import Role


def test_player_crud_duplicate_confirmation_and_soft_delete(client, create_player):
    first = create_player("Alex Smith", "M", 6)

    duplicate = client.post(
        "/api/players", json={"name": "Alex  Smith", "gender": "F", "skill": 4}
    )
    assert duplicate.status_code == 409
    assert duplicate.get_json()["error"] == "duplicate_confirmation_required"

    confirmed = client.post(
        "/api/players",
        json={
            "name": "Alex Smith",
            "gender": "F",
            "skill": 4,
            "confirm_duplicate": True,
        },
    )
    assert confirmed.status_code == 201
    assert confirmed.get_json()["player"]["id"] != first["id"]

    updated = client.patch(
        f"/api/players/{first['id']}", json={"name": "Alex Jones", "skill": 7}
    )
    assert updated.status_code == 200
    assert updated.get_json()["player"]["skill"] == 7

    assert client.delete(f"/api/players/{first['id']}").status_code == 204
    listed = client.get("/api/players").get_json()["players"]
    assert first["id"] not in {player["id"] for player in listed}
    all_players = client.get("/api/players?include_inactive=true").get_json()["players"]
    assert any(
        player["id"] == first["id"] and not player["active"] for player in all_players
    )


def test_distinct_unicode_names_do_not_collapse_to_one_duplicate(client, create_player):
    create_player("王明", "M", 5)
    second = client.post(
        "/api/players", json={"name": "李娜", "gender": "F", "skill": 5}
    )
    assert second.status_code == 201


def test_user_can_see_skill_but_cannot_write_skill_or_delete(client, set_actor):
    set_actor(Role.OWNER)
    created = client.post(
        "/api/players", json={"name": "Skilled Player", "gender": "M", "skill": 8}
    ).get_json()["player"]

    set_actor(Role.STAFF)
    fetched = client.get(f"/api/players/{created['id']}")
    assert fetched.status_code == 200
    assert fetched.get_json()["player"]["skill"] == 8
    assert (
        client.patch(f"/api/players/{created['id']}", json={"skill": 2}).status_code
        == 403
    )

    # Staff can't choose a rating; new players start at 1 so they can play.
    staff_added = client.post(
        "/api/players", json={"name": "Staff Added Player", "gender": "F"}
    )
    assert staff_added.status_code == 201
    assert staff_added.get_json()["player"]["skill_ready"] is True
    assert staff_added.get_json()["player"]["skill"] == 1


def test_club_isolation_returns_not_found(client, create_player, set_actor):
    player = create_player("Club A Player")
    set_actor(Role.OWNER, club_id="club-b")
    assert client.get(f"/api/players/{player['id']}").status_code == 404
    assert client.get("/api/players").get_json() == {"players": []}


def test_missing_auth_provider_is_denied(tmp_path):
    from app import create_app

    app = create_app(
        {
            "TESTING": True,
            "SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp_path / 'noauth.db'}",
        }
    )
    response = app.test_client().get("/api/players")
    assert response.status_code == 401
