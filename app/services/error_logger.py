"""Centralized application logging helpers with optional Sentry reporting."""

import logging
from typing import Any, cast

from app.core.config import settings
from app.core.logging_privacy import redact_sensitive_log_text, sanitize_observability_value

try:
    import sentry_sdk
except ImportError:
    sentry_sdk = None

logger = logging.getLogger("fitaly")
logger.setLevel(logging.INFO)


def _sanitized_context(context: dict[str, Any]) -> dict[str, Any]:
    sanitized = sanitize_observability_value(context)
    if isinstance(sanitized, dict):
        return cast("dict[str, Any]", sanitized)
    return {"redacted": sanitized}


def log_info(message: str, **context: Any) -> None:
    """Log an informational message with optional context such as request_id or userId."""
    logger.info(redact_sensitive_log_text(message), extra={"context": _sanitized_context(context)})


def log_warning(message: str, **context: Any) -> None:
    """Log a warning message with optional context such as request_id or userId."""
    logger.warning(redact_sensitive_log_text(message), extra={"context": _sanitized_context(context)})


def log_error(message: str, **context: Any) -> None:
    """Log an error message with optional context such as request_id or userId."""
    sanitized_message = redact_sensitive_log_text(message)
    logger.error(sanitized_message, extra={"context": _sanitized_context(context)})
    if sentry_sdk and settings.SENTRY_DSN:
        sentry_sdk.capture_message(sanitized_message, level="error")


def capture_exception(exc: Exception, **context: Any) -> None:
    """Log an exception stack trace with optional context such as request_id or userId."""
    logger.exception(redact_sensitive_log_text(str(exc)), extra={"context": _sanitized_context(context)})
    if sentry_sdk and settings.SENTRY_DSN:
        sentry_sdk.capture_exception(exc)
