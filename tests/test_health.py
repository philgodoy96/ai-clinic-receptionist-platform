import pytest
from fastapi.testclient import TestClient

from app.api.routes import health as health_route
from app.main import create_app


def test_health_check_returns_service_status() -> None:
    client = TestClient(create_app())

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "service": "ai-clinic-receptionist-platform",
        "environment": "local",
    }


def test_dependency_health_returns_ok_when_database_is_available(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = TestClient(create_app())

    def fake_database_check() -> None:
        return None

    monkeypatch.setattr(health_route, "check_database_connection", fake_database_check)

    response = client.get("/health/dependencies")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "service": "ai-clinic-receptionist-platform",
        "environment": "local",
        "dependencies": {
            "database": {
                "status": "ok",
            },
        },
    }


def test_dependency_health_returns_degraded_when_database_is_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = TestClient(create_app())

    def fake_database_check() -> None:
        raise RuntimeError("database is unavailable")

    monkeypatch.setattr(health_route, "check_database_connection", fake_database_check)

    response = client.get("/health/dependencies")

    assert response.status_code == 200
    assert response.json() == {
        "status": "degraded",
        "service": "ai-clinic-receptionist-platform",
        "environment": "local",
        "dependencies": {
            "database": {
                "status": "unavailable",
                "detail": "RuntimeError",
            },
        },
    }