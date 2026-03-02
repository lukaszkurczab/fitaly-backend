"""Centralized application logging helpers with optional Sentry reporting."""

import logging
from typing import Any, Dict

from app.core.config import settings

try:
    import sentry_sdk
except ImportError:
    sentry_sdk = None

logger = logging.getLogger("caloriai")
logger.setLevel(logging.INFO)


def log_info(message: str, **context: Any) -> None:
    """Log an informational message with optional context such as request_id or userId."""
    logger.info(message, extra={"context": context})


def log_warning(message: str, **context: Any) -> None:
    """Log a warning message with optional context such as request_id or userId."""
    logger.warning(message, extra={"context": context})


def log_error(message: str, **context: Any) -> None:
    """Log an error message with optional context such as request_id or userId."""
    logger.error(message, extra={"context": context})
    if sentry_sdk and settings.SENTRY_DSN:
        sentry_sdk.capture_message(message, level="error")


def capture_exception(exc: Exception, **context: Any) -> None:
    """Log an exception stack trace with optional context such as request_id or userId."""
    logger.exception(str(exc), extra={"context": context})
    if sentry_sdk and settings.SENTRY_DSN:
        sentry_sdk.capture_exception(exc)
