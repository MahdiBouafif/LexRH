from fastapi.testclient import TestClient

from lexrh.api.main import app, get_readiness_check

client = TestClient(app)


def test_health_returns_ok() -> None:
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_ready_is_503_when_database_is_down() -> None:
    app.dependency_overrides[get_readiness_check] = lambda: lambda: False
    try:
        response = client.get("/ready")
    finally:
        app.dependency_overrides.clear()
    assert response.status_code == 503


def test_ready_is_200_when_database_answers() -> None:
    app.dependency_overrides[get_readiness_check] = lambda: lambda: True
    try:
        response = client.get("/ready")
    finally:
        app.dependency_overrides.clear()
    assert response.status_code == 200
    assert response.json() == {"status": "ready"}
