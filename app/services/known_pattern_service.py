from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import re
from typing import Any, cast

from app.schemas.known_patterns import (
    KnownPatternCandidate,
    KnownPatternCandidateQueryEcho,
    KnownPatternCandidatesResponse,
    KnownPatternCountBucket,
    KnownPatternExplanation,
    KnownPatternSourceRef,
)
from app.services import meal_service


KNOWN_PATTERN_RULE_VERSION = "known-pattern-v1"
KNOWN_PATTERN_MIN_SOURCE_COUNT = 3
KNOWN_PATTERN_MIN_DISTINCT_DAYS = 3
KNOWN_PATTERN_MAX_HISTORY_ITEMS = 100
KNOWN_PATTERN_MAX_SOURCE_REFS = 5
KNOWN_PATTERN_DEFAULT_LIMIT = 5
KNOWN_PATTERN_EXPIRES_AFTER_DAYS = 14

_WORD_RE = re.compile(r"[^a-z0-9ąćęłńóśźż]+", re.IGNORECASE)
_SPACE_RE = re.compile(r"\s+")


@dataclass(frozen=True, slots=True)
class _CandidateEvidence:
    meal_id: str
    meal_type: str
    normalized_name: str
    day_key: str
    logged_at: str
    logged_at_dt: datetime


def _sha256_short(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:32]


def _normalize_name(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = _WORD_RE.sub(" ", value.casefold())
    normalized = _SPACE_RE.sub(" ", normalized).strip()
    return normalized if len(normalized) >= 2 else None


def _normalize_meal_type(value: object) -> str:
    return value if isinstance(value, str) and value else "other"


def _parse_logged_at(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _format_instant(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(
        timespec="milliseconds",
    ).replace("+00:00", "Z")


def _day_key(value: object, logged_at: datetime) -> str:
    if isinstance(value, str) and re.match(r"^\d{4}-\d{2}-\d{2}$", value):
        return value
    return logged_at.date().isoformat()


def _meal_id(meal: dict[str, Any], normalized_name: str, logged_at: str) -> str:
    for key in ("id", "mealId", "cloudId"):
        value = meal.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return _sha256_short(f"{normalized_name}|{logged_at}")


def _as_meal_map(value: object) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    return cast(dict[str, Any], value)


def _evidence_from_meal(raw_meal: object) -> _CandidateEvidence | None:
    meal = _as_meal_map(raw_meal)
    if meal is None or bool(meal.get("deleted")):
        return None

    normalized_name = _normalize_name(meal.get("name"))
    logged_at_dt = _parse_logged_at(meal.get("loggedAt") or meal.get("timestamp"))
    if normalized_name is None or logged_at_dt is None:
        return None

    logged_at = _format_instant(logged_at_dt)
    return _CandidateEvidence(
        meal_id=_meal_id(meal, normalized_name, logged_at),
        meal_type=_normalize_meal_type(meal.get("type")),
        normalized_name=normalized_name,
        day_key=_day_key(meal.get("dayKey"), logged_at_dt),
        logged_at=logged_at,
        logged_at_dt=logged_at_dt,
    )


def _bucket_count(value: int) -> KnownPatternCountBucket:
    return "5_plus" if value >= 5 else "3_4"


def _source_ref(evidence: _CandidateEvidence) -> KnownPatternSourceRef:
    return KnownPatternSourceRef(
        sourceType="meal_snapshot",
        sourceHash=_sha256_short(
            f"{KNOWN_PATTERN_RULE_VERSION}|meal|{evidence.meal_id}|{evidence.logged_at}"
        ),
    )


def _candidate_from_group(
    subject_key: str,
    evidence_items: list[_CandidateEvidence],
    *,
    now: datetime,
) -> KnownPatternCandidate | None:
    unique_days = {evidence.day_key for evidence in evidence_items}
    if (
        len(evidence_items) < KNOWN_PATTERN_MIN_SOURCE_COUNT
        or len(unique_days) < KNOWN_PATTERN_MIN_DISTINCT_DAYS
    ):
        return None

    sorted_evidence = sorted(evidence_items, key=lambda item: item.logged_at_dt)
    first_seen = sorted_evidence[0].logged_at_dt
    last_seen = sorted_evidence[-1].logged_at_dt
    subject_hash = _sha256_short(f"{KNOWN_PATTERN_RULE_VERSION}|{subject_key}")
    candidate_hash = _sha256_short(f"{KNOWN_PATTERN_RULE_VERSION}|candidate|{subject_hash}")
    expires_at = last_seen + timedelta(days=KNOWN_PATTERN_EXPIRES_AFTER_DAYS)
    if expires_at <= now:
        return None

    source_refs = [
        _source_ref(evidence)
        for evidence in sorted_evidence[-KNOWN_PATTERN_MAX_SOURCE_REFS:]
    ]

    return KnownPatternCandidate(
        candidateId=candidate_hash,
        candidateType="repeated_meal_snapshot",
        subjectKeyHash=subject_hash,
        state="candidate",
        confidenceBucket="high" if len(unique_days) >= 5 else "medium",
        sourceCountBucket=_bucket_count(len(evidence_items)),
        distinctDayCountBucket=_bucket_count(len(unique_days)),
        firstSeenAt=_format_instant(first_seen),
        lastSeenAt=_format_instant(last_seen),
        expiresAt=_format_instant(expires_at),
        sourceRefs=source_refs,
        explanation=KnownPatternExplanation(
            key="knownPattern.explanation.repeatedMealSnapshot",
            reasonCode="repeated_meal_recent_distinct_days",
        ),
        suggestedAction="open_review_draft",
        createdByRuleVersion=KNOWN_PATTERN_RULE_VERSION,
    )


def evaluate_known_pattern_candidates(
    meals: Iterable[object],
    *,
    limit: int = KNOWN_PATTERN_DEFAULT_LIMIT,
    now: datetime | None = None,
) -> KnownPatternCandidatesResponse:
    evaluation_now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    grouped: dict[str, list[_CandidateEvidence]] = {}
    for raw_meal in meals:
        evidence = _evidence_from_meal(raw_meal)
        if evidence is None:
            continue
        subject_key = f"{evidence.meal_type}|{evidence.normalized_name}"
        grouped.setdefault(subject_key, []).append(evidence)

    candidates = [
        candidate
        for subject_key, evidence_items in grouped.items()
        if (
            candidate := _candidate_from_group(
                subject_key,
                evidence_items,
                now=evaluation_now,
            )
        )
        is not None
    ]
    candidates.sort(
        key=lambda candidate: (
            candidate.confidenceBucket == "high",
            candidate.sourceCountBucket == "5_plus",
            candidate.lastSeenAt,
            candidate.candidateId,
        ),
        reverse=True,
    )
    items = candidates[:limit]

    return KnownPatternCandidatesResponse(
        items=items,
        queryEcho=KnownPatternCandidateQueryEcho(
            ruleVersion=KNOWN_PATTERN_RULE_VERSION,
            minSourceCount=KNOWN_PATTERN_MIN_SOURCE_COUNT,
            minDistinctDays=KNOWN_PATTERN_MIN_DISTINCT_DAYS,
            maxHistoryItems=KNOWN_PATTERN_MAX_HISTORY_ITEMS,
            returnedCandidates=len(items),
        ),
    )


async def list_known_pattern_candidates_for_user(
    user_id: str,
    *,
    limit: int = KNOWN_PATTERN_DEFAULT_LIMIT,
) -> KnownPatternCandidatesResponse:
    meals, _next_cursor = await meal_service.list_history(
        user_id,
        limit_count=KNOWN_PATTERN_MAX_HISTORY_ITEMS,
    )
    return evaluate_known_pattern_candidates(meals, limit=limit)
