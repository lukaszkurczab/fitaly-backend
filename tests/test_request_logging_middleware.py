from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from pytest_mock import MockerFixture

from app.api.middleware.request_logging import RequestLoggingMiddleware


def create_test_client() -> TestClient:
    app = FastAPI()
    app.add_middleware(RequestLoggingMiddleware)

    @app.get("/ok")
    async def ok() -> dict[str, str]:
        return {"detail": "ok"}

    @app.get("/bad")
    async def bad() -> JSONResponse:
        return JSONResponse(content={"detail": "bad"}, status_code=400)

    return TestClient(app)


def test_request_logging_middleware_sets_request_id_and_logs_info(
    mocker: MockerFixture,
) -> None:
    log_info = mocker.patch("app.api.middleware.request_logging.log_info")
    log_warning = mocker.patch("app.api.middleware.request_logging.log_warning")
    client = create_test_client()

    response = client.get("/ok")

    assert response.status_code == 200
    assert "X-Request-ID" in response.headers
    assert len(response.headers["X-Request-ID"]) == 32
    log_warning.assert_not_called()
    log_info.assert_called_once()

    assert log_info.call_args.args[0] == "GET /ok → 200"
    assert log_info.call_args.kwargs["request_id"] == response.headers["X-Request-ID"]
    assert log_info.call_args.kwargs["path"] == "/ok"
    assert log_info.call_args.kwargs["method"] == "GET"
    assert log_info.call_args.kwargs["status_code"] == 200
    assert isinstance(log_info.call_args.kwargs["duration_ms"], float)


def test_request_logging_middleware_logs_warning_for_error_status(
    mocker: MockerFixture,
) -> None:
    log_info = mocker.patch("app.api.middleware.request_logging.log_info")
    log_warning = mocker.patch("app.api.middleware.request_logging.log_warning")
    client = create_test_client()

    response = client.get("/bad")

    assert response.status_code == 400
    assert "X-Request-ID" in response.headers
    assert len(response.headers["X-Request-ID"]) == 32
    log_info.assert_not_called()
    log_warning.assert_called_once()

    assert log_warning.call_args.args[0] == "GET /bad → 400"
    assert log_warning.call_args.kwargs["request_id"] == response.headers["X-Request-ID"]
    assert log_warning.call_args.kwargs["path"] == "/bad"
    assert log_warning.call_args.kwargs["method"] == "GET"
    assert log_warning.call_args.kwargs["status_code"] == 400
    assert isinstance(log_warning.call_args.kwargs["duration_ms"], float)
