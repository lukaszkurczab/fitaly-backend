from fastapi.testclient import TestClient
from pytest import MonkeyPatch

from app.core.config import settings
from app.main import app

client = TestClient(app)
commit_sha = "0123456789abcdef0123456789abcdef01234567"


def test_api_version() -> None:
    response = client.get("/api/v1/version")

    assert response.status_code == 200
    data = response.json()
    assert data["version"] == settings.VERSION
    assert "commitSha" not in data


def test_api_version_includes_commit_sha_when_configured(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "BACKEND_COMMIT_SHA", commit_sha)

    response = client.get("/api/v1/version")

    assert response.status_code == 200
    data = response.json()
    assert data["version"] == settings.VERSION
    assert data["commitSha"] == commit_sha


def test_api_version_omits_commit_sha_when_missing(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "BACKEND_COMMIT_SHA", "")

    response = client.get("/api/v1/version")

    assert response.status_code == 200
    data = response.json()
    assert data["version"] == settings.VERSION
    assert "commitSha" not in data


def test_api_version_without_version_not_found() -> None:
    response = client.get("/version")

    assert response.status_code == 404
