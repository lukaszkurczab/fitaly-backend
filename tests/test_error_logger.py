from typing import Any

from pytest_mock import MockerFixture

from app.services import error_logger

RAW_LOG_MARKERS = (
    "jane.doe@example.com",
    "Bearer eyJhbGciOiJIUzI1Ni.secret",
    "https://api.example.test/path?token=secret&email=jane@example.com",
    "meals/user-1/private-image.jpg",
    "message=private user-authored meal notes",
)
RAW_LOG_MESSAGE = " | ".join(RAW_LOG_MARKERS)
RAW_LOG_CONTEXT: dict[str, Any] = {
    "email": "jane.doe@example.com",
    "authorization": "Bearer eyJhbGciOiJIUzI1Ni.secret",
    "storage": "meals/user-1/private-image.jpg",
    "query": "token=secret&email=jane@example.com",
    "nested": {"message": "private user-authored meal notes"},
}


def _assert_no_raw_markers_in_python_logger_call(call_payload: object) -> None:
    serialized_payload = repr(call_payload)
    for marker in RAW_LOG_MARKERS:
        assert marker not in serialized_payload
    assert "[REDACTED_" in serialized_payload


def test_log_info_logs_without_reporting_to_sentry(mocker: MockerFixture) -> None:
    logger_info = mocker.patch.object(error_logger.logger, "info")
    mock_sentry = mocker.Mock()
    mocker.patch.object(error_logger, "sentry_sdk", mock_sentry)
    mocker.patch.object(error_logger.settings, "SENTRY_DSN", "https://example@sentry.io/1")

    error_logger.log_info("info message", request_id="req-1", userId="user-1")

    logger_info.assert_called_once_with(
        "info message",
        extra={"context": {"request_id": "req-1", "userId": "user-1"}},
    )
    mock_sentry.capture_message.assert_not_called()


def test_log_warning_logs_without_reporting_to_sentry(mocker: MockerFixture) -> None:
    logger_warning = mocker.patch.object(error_logger.logger, "warning")
    mock_sentry = mocker.Mock()
    mocker.patch.object(error_logger, "sentry_sdk", mock_sentry)
    mocker.patch.object(error_logger.settings, "SENTRY_DSN", "https://example@sentry.io/1")

    error_logger.log_warning("warning message", request_id="req-2")

    logger_warning.assert_called_once_with(
        "warning message",
        extra={"context": {"request_id": "req-2"}},
    )
    mock_sentry.capture_message.assert_not_called()


def test_log_error_logs_and_reports_to_sentry(mocker: MockerFixture) -> None:
    logger_error = mocker.patch.object(error_logger.logger, "error")
    mock_sentry = mocker.Mock()
    mocker.patch.object(error_logger, "sentry_sdk", mock_sentry)
    mocker.patch.object(error_logger.settings, "SENTRY_DSN", "https://example@sentry.io/1")

    error_logger.log_error("error message", source="mobile", userId="user-2")

    logger_error.assert_called_once_with(
        "error message",
        extra={"context": {"source": "mobile", "userId": "user-2"}},
    )
    mock_sentry.capture_message.assert_called_once_with("error message", level="error")


def test_capture_exception_logs_and_reports_to_sentry(mocker: MockerFixture) -> None:
    logger_exception = mocker.patch.object(error_logger.logger, "exception")
    mock_sentry = mocker.Mock()
    mocker.patch.object(error_logger, "sentry_sdk", mock_sentry)
    mocker.patch.object(error_logger.settings, "SENTRY_DSN", "https://example@sentry.io/1")
    exc = ValueError("boom")

    error_logger.capture_exception(exc, request_id="req-3", userId="user-3")

    logger_exception.assert_called_once_with(
        "boom",
        extra={"context": {"request_id": "req-3", "userId": "user-3"}},
    )
    mock_sentry.capture_exception.assert_called_once_with(exc)


def test_log_info_sanitizes_message_and_context_before_python_logging(
    mocker: MockerFixture,
) -> None:
    logger_info = mocker.patch.object(error_logger.logger, "info")
    mock_sentry = mocker.Mock()
    mocker.patch.object(error_logger, "sentry_sdk", mock_sentry)
    mocker.patch.object(error_logger.settings, "SENTRY_DSN", "https://example@sentry.io/1")

    error_logger.log_info(RAW_LOG_MESSAGE, **RAW_LOG_CONTEXT)

    _assert_no_raw_markers_in_python_logger_call(logger_info.call_args)
    mock_sentry.capture_message.assert_not_called()


def test_log_warning_sanitizes_message_and_context_before_python_logging(
    mocker: MockerFixture,
) -> None:
    logger_warning = mocker.patch.object(error_logger.logger, "warning")
    mock_sentry = mocker.Mock()
    mocker.patch.object(error_logger, "sentry_sdk", mock_sentry)
    mocker.patch.object(error_logger.settings, "SENTRY_DSN", "https://example@sentry.io/1")

    error_logger.log_warning(RAW_LOG_MESSAGE, **RAW_LOG_CONTEXT)

    _assert_no_raw_markers_in_python_logger_call(logger_warning.call_args)
    mock_sentry.capture_message.assert_not_called()


def test_log_error_sanitizes_python_logging_and_sentry_message(
    mocker: MockerFixture,
) -> None:
    logger_error = mocker.patch.object(error_logger.logger, "error")
    mock_sentry = mocker.Mock()
    mocker.patch.object(error_logger, "sentry_sdk", mock_sentry)
    mocker.patch.object(error_logger.settings, "SENTRY_DSN", "https://example@sentry.io/1")

    error_logger.log_error(RAW_LOG_MESSAGE, **RAW_LOG_CONTEXT)

    _assert_no_raw_markers_in_python_logger_call(logger_error.call_args)
    _assert_no_raw_markers_in_python_logger_call(mock_sentry.capture_message.call_args)


def test_capture_exception_sanitizes_python_logging_and_keeps_sentry_exception(
    mocker: MockerFixture,
) -> None:
    logger_exception = mocker.patch.object(error_logger.logger, "exception")
    mock_sentry = mocker.Mock()
    mocker.patch.object(error_logger, "sentry_sdk", mock_sentry)
    mocker.patch.object(error_logger.settings, "SENTRY_DSN", "https://example@sentry.io/1")
    exc = ValueError(RAW_LOG_MESSAGE)

    error_logger.capture_exception(exc, **RAW_LOG_CONTEXT)

    _assert_no_raw_markers_in_python_logger_call(logger_exception.call_args)
    mock_sentry.capture_exception.assert_called_once_with(exc)
