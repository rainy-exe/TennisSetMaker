def test_flask_health_smoke(client):
    assert client.get("/api/health").get_json() == {"status": "ok"}
