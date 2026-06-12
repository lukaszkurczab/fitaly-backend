from typing import Any, cast

from pytest_mock import MockerFixture
from sentry_sdk.types import Event

from app.core import monitoring
from app.core.logging_privacy import sanitize_sentry_event


def test_init_sentry_skips_when_dsn_missing(mocker: MockerFixture) -> None:
    mocker.patch.object(monitoring.settings, "SENTRY_DSN", "")
    sentry_init = mocker.patch.object(monitoring.sentry_sdk, "init")

    monitoring.init_sentry()

    sentry_init.assert_not_called()


def test_init_sentry_skips_in_local_environment(mocker: MockerFixture) -> None:
    mocker.patch.object(monitoring.settings, "SENTRY_DSN", "https://example@sentry.io/1")
    mocker.patch.object(monitoring.settings, "ENVIRONMENT", "local")
    sentry_init = mocker.patch.object(monitoring.sentry_sdk, "init")

    monitoring.init_sentry()

    sentry_init.assert_not_called()


def test_init_sentry_skips_during_pytest(mocker: MockerFixture) -> None:
    mocker.patch.object(monitoring.settings, "SENTRY_DSN", "https://example@sentry.io/1")
    mocker.patch.object(monitoring.settings, "ENVIRONMENT", "production")
    mocker.patch.object(monitoring, "_running_under_pytest", return_value=True)
    sentry_init = mocker.patch.object(monitoring.sentry_sdk, "init")

    monitoring.init_sentry()

    sentry_init.assert_not_called()


def test_init_sentry_uses_configured_sentry_environment(mocker: MockerFixture) -> None:
    mocker.patch.object(monitoring.settings, "SENTRY_DSN", "https://example@sentry.io/1")
    mocker.patch.object(monitoring.settings, "ENVIRONMENT", "production")
    mocker.patch.object(monitoring.settings, "SENTRY_ENVIRONMENT", "staging")
    mocker.patch.object(monitoring.settings, "SENTRY_TRACES_SAMPLE_RATE", 0.25)
    mocker.patch.object(monitoring.settings, "VERSION", "1.2.3")
    mocker.patch.object(monitoring, "_running_under_pytest", return_value=False)
    sentry_init = mocker.patch.object(monitoring.sentry_sdk, "init")

    monitoring.init_sentry()

    assert sentry_init.call_count == 1
    kwargs = sentry_init.call_args.kwargs
    assert kwargs["dsn"] == "https://example@sentry.io/1"
    assert kwargs["environment"] == "staging"
    assert kwargs["release"] == "1.2.3"
    assert kwargs["send_default_pii"] is False
    assert kwargs["before_send"] is sanitize_sentry_event
    assert kwargs["traces_sample_rate"] == 0.25
    assert len(kwargs["integrations"]) == 2


def test_sentry_before_send_redacts_sensitive_event_fields() -> None:
    event: dict[str, Any] = {
        "message": (
            "Upload failed for jane@example.com at meals/user-1/private-image.jpg "
            "token=secret-token-123"
        ),
        "exception": {
            "values": [
                {
                    "type": "RuntimeError",
                    "value": "prompt: user meal description",
                    "stacktrace": {
                        "frames": [
                            {
                                "filename": (
                                    "https://api.example.test/error?token=secret"
                                    "&email=jane@example.com"
                                )
                            }
                        ]
                    },
                }
            ]
        },
        "logentry": {
            "message": "rawPrompt: secret-provider-prompt",
            "formatted": (
                "https://firebasestorage.googleapis.com/v0/b/demo.appspot.com/o/"
                "meals%2Fuser-1%2Fimage.jpg?alt=media&token=abc"
            ),
        },
        "extra": {
            "object_path": "meals/user-1/private-image.jpg",
            "apiKey": "AIzaSyD-example-provider-secret",
            "safe": "operation failed",
        },
        "contexts": {
            "request": {
                "url": "https://api.example.test/path?password=secret",
                "query_string": "password=secret&email=jane@example.com",
            }
        },
        "breadcrumbs": {
            "values": [
                {
                    "message": "Bearer eyJhbGciOiJIUzI1Ni.secret",
                    "data": {"authorization": "Bearer auth-secret-123"},
                }
            ]
        },
        "request": {
            "url": "https://api.example.test/path?token=secret",
            "headers": {"Authorization": "Bearer auth-secret-123"},
        },
        "user": {"id": "opaque-uid", "email": "jane@example.com", "username": "janedoe"},
    }

    sanitized = sanitize_sentry_event(cast(Event, event))
    sanitized_text = repr(sanitized)

    assert "jane@example.com" not in sanitized_text
    assert "secret-token-123" not in sanitized_text
    assert "secret-provider-prompt" not in sanitized_text
    assert "user meal description" not in sanitized_text
    assert "meals/user-1/private-image.jpg" not in sanitized_text
    assert "meals%2Fuser-1%2Fimage.jpg" not in sanitized_text
    assert "password=secret" not in sanitized_text
    assert "auth-secret-123" not in sanitized_text
    assert "AIzaSyD-example-provider-secret" not in sanitized_text
    assert "janedoe" not in sanitized_text
    assert "opaque-uid" in sanitized_text
    assert "operation failed" in sanitized_text
    assert "[REDACTED_" in sanitized_text
