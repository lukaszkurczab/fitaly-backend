from pytest_mock import MockerFixture

from app.services import error_logger


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
