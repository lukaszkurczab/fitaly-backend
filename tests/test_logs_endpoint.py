from fastapi import FastAPI
from fastapi.testclient import TestClient
from pytest_mock import MockerFixture

from app.api.routes.logs import router as logs_router


def create_test_client() -> TestClient:
    app = FastAPI()
    app.include_router(logs_router, prefix="/api/v1")
    return TestClient(app)


def test_logs_error_endpoint_returns_201_and_calls_logger(mocker: MockerFixture) -> None:
    log_error = mocker.patch("app.api.routes.logs.error_logger.log_error")
    client = create_test_client()

    response = client.post(
        "/api/v1/logs/error",
        json={
            "source": "mobile.scan-screen",
            "message": "Camera permission check failed",
            "stack": "stack trace",
            "context": {"platform": "ios"},
            "userId": "abc123",
        },
    )

    assert response.status_code == 201
    assert response.json() == {"detail": "logged"}
    log_error.assert_called_once_with(
        "Camera permission check failed",
        source="mobile.scan-screen",
        stack="stack trace",
        context={"platform": "ios"},
        userId="abc123",
    )


def test_logs_error_endpoint_returns_500_when_logger_fails(mocker: MockerFixture) -> None:
    mocker.patch(
        "app.api.routes.logs.error_logger.log_error",
        side_effect=RuntimeError("logger failed"),
    )
    capture_exception = mocker.patch("app.api.routes.logs.error_logger.capture_exception")
    client = create_test_client()

    response = client.post(
        "/api/v1/logs/error",
        json={
            "source": "mobile",
            "message": "Failed to submit error log",
            "stack": None,
            "context": None,
            "userId": None,
        },
    )

    assert response.status_code == 500
    assert response.json() == {"detail": "Failed to log error"}
    capture_exception.assert_called_once()
    assert str(capture_exception.call_args.args[0]) == "logger failed"
