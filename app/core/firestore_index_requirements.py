"""Helpers for enforcing Firestore composite-index requirements."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
import logging
from typing import Any

from google.api_core.exceptions import FailedPrecondition

_MISSING_INDEX_MARKER = "requires an index"


def is_missing_index_error(exc: BaseException) -> bool:
    """Return True when Firestore rejected a query because a composite index is missing."""
    if not isinstance(exc, FailedPrecondition):
        return False
    return _MISSING_INDEX_MARKER in str(exc).lower()


def stream_required_indexed_query(
    *,
    indexed_query: Any,
    logger: logging.Logger,
    query_name: str,
    extra: Mapping[str, object] | None = None,
) -> Iterable[Any]:
    """Stream an indexed query and fail explicitly when its composite index is missing."""
    try:
        yield from indexed_query.stream()
        return
    except FailedPrecondition as exc:
        if not is_missing_index_error(exc):
            raise
        logger.warning(
            "Missing Firestore composite index for %s; failing query explicitly.",
            query_name,
            extra=dict(extra or {}),
        )
        raise
