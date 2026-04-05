import pytest
from pytest_mock import MockerFixture

from app import main


def _set_valid_production_config(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(main.settings, "ENVIRONMENT", "production")
    monkeypatch.setattr(main.settings, "CORS_ORIGINS", "https://app.fitaly.com")
    monkeypatch.setattr(main.settings, "OPENAI_API_KEY", "test-openai-key")
    monkeypatch.setattr(main.settings, "FIREBASE_PROJECT_ID", "fitaly-prod")
    monkeypatch.setattr(main.settings, "GOOGLE_APPLICATION_CREDENTIALS", "")
    monkeypatch.setattr(main.settings, "FIREBASE_CLIENT_EMAIL", "svc@example.com")
    monkeypatch.setattr(
        main.settings,
        "FIREBASE_PRIVATE_KEY",
        "-----BEGIN PRIVATE KEY-----\nkey\n-----END PRIVATE KEY-----",
    )


def test_resolve_cors_origins_non_production_defaults_to_wildcard(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(main.settings, "ENVIRONMENT", "development")
    monkeypatch.setattr(main.settings, "CORS_ORIGINS", "")

    assert main._resolve_cors_origins() == ["*"]


def test_resolve_cors_origins_production_rejects_wildcard(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_valid_production_config(monkeypatch)
    monkeypatch.setattr(main.settings, "CORS_ORIGINS", "*")

    with pytest.raises(RuntimeError, match="wildcard CORS"):
        main._resolve_cors_origins()


def test_resolve_cors_origins_production_requires_openai_api_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_valid_production_config(monkeypatch)
    monkeypatch.setattr(main.settings, "OPENAI_API_KEY", "")

    with pytest.raises(RuntimeError, match="OPENAI_API_KEY"):
        main._resolve_cors_origins()


def test_resolve_cors_origins_production_requires_firebase_credentials_source(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_valid_production_config(monkeypatch)
    monkeypatch.setattr(main.settings, "FIREBASE_CLIENT_EMAIL", "")
    monkeypatch.setattr(main.settings, "FIREBASE_PRIVATE_KEY", "")

    with pytest.raises(RuntimeError, match="GOOGLE_APPLICATION_CREDENTIALS"):
        main._resolve_cors_origins()


def test_create_app_non_production_logs_firebase_startup_failure(
    monkeypatch: pytest.MonkeyPatch,
    mocker: MockerFixture,
) -> None:
    monkeypatch.setattr(main.settings, "ENVIRONMENT", "development")
    mocker.patch("app.main.get_firestore", side_effect=RuntimeError("firebase down"))

    app = main.create_app()

    assert app is not None


def test_create_app_production_raises_on_firebase_startup_failure(
    monkeypatch: pytest.MonkeyPatch,
    mocker: MockerFixture,
) -> None:
    _set_valid_production_config(monkeypatch)
    mocker.patch("app.main.get_firestore", side_effect=RuntimeError("firebase down"))

    with pytest.raises(RuntimeError, match="firebase down"):
        main.create_app()
