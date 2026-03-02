from datetime import datetime, timezone

from fastapi.testclient import TestClient
from pytest_mock import MockerFixture

from app.main import app
from app.schemas.health import FirestoreHealthResponse
from app.services.health_service import FirestoreHealthcheckError

client = TestClient(app)


def test_health_check() -> None:
    response = client.get("/api/v1/health")

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert data["service"] == "fitaly-backend"
    assert "timestamp" in data


def test_health_check_without_version_not_found() -> None:
    response = client.get("/health")

    assert response.status_code == 404


def test_firestore_health_check(mocker: MockerFixture) -> None:
    mocker.patch(
        "app.api.routes.health.check_firestore_health",
        return_value=FirestoreHealthResponse(
            status="ok",
            service="fitaly-backend",
            database="firestore",
            project_id="calories-calculator-ai",
            timestamp=datetime.now(timezone.utc),
        ),
    )

    response = client.get("/api/v1/health/firestore")

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert data["service"] == "fitaly-backend"
    assert data["database"] == "firestore"
    assert data["project_id"] == "calories-calculator-ai"
    assert "timestamp" in data


def test_firestore_health_check_returns_503_when_unavailable(
    mocker: MockerFixture,
) -> None:
    mocker.patch(
        "app.api.routes.health.check_firestore_health",
        side_effect=FirestoreHealthcheckError("Firestore healthcheck failed."),
    )

    response = client.get("/api/v1/health/firestore")

    assert response.status_code == 503
    assert response.json() == {"detail": "Firestore healthcheck failed."}
