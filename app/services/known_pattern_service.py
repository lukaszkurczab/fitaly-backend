from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import json
import logging
import re
from typing import Any, TypedDict, cast

from firebase_admin.exceptions import FirebaseError
from google.api_core.exceptions import GoogleAPICallError, RetryError
from google.cloud import firestore
from pydantic import ValidationError

from app.core.exceptions import FirestoreServiceError
from app.core.firestore_constants import (
    KNOWN_PATTERN_CONTROLS_SUBCOLLECTION,
    KNOWN_PATTERN_MUTATION_DEDUPE_SUBCOLLECTION,
    USERS_COLLECTION,
)
from app.db.firebase import get_firestore
from app.schemas.known_patterns import (
    KnownPatternCandidate,
    KnownPatternCandidateControl,
    KnownPatternCandidateControlRequest,
    KnownPatternCandidateQueryEcho,
    KnownPatternCandidatesResponse,
    KnownPatternCountBucket,
    KnownPatternExplanation,
    KnownPatternReviewDraft,
    KnownPatternReviewDraftRequest,
    KnownPatternSourceRef,
)
from app.schemas.meal import MealIngredient, MealTotals, MealType
from app.services import meal_service


logger = logging.getLogger(__name__)

KNOWN_PATTERN_RULE_VERSION = "known-pattern-v1"
KNOWN_PATTERN_MIN_SOURCE_COUNT = 3
KNOWN_PATTERN_MIN_DISTINCT_DAYS = 3
KNOWN_PATTERN_MAX_HISTORY_ITEMS = 100
KNOWN_PATTERN_MAX_SOURCE_REFS = 5
KNOWN_PATTERN_DEFAULT_LIMIT = 5
KNOWN_PATTERN_EXPIRES_AFTER_DAYS = 14
KNOWN_PATTERN_MAX_CONTROL_DOCS = 100

_WORD_RE = re.compile(r"[^a-z0-9ąćęłńóśźż]+", re.IGNORECASE)
_SPACE_RE = re.compile(r"\s+")


class KnownPatternNotFoundError(ValueError):
    """Raised when a known-pattern candidate is no longer available."""


class KnownPatternMutationDedupeConflictError(ValueError):
    """Raised when a clientMutationId is reused for a different known-pattern mutation."""


class KnownPatternControlMutationResult(TypedDict):
    document: dict[str, Any]
    applied: bool


class KnownPatternReviewDraftResult(TypedDict):
    draft: KnownPatternReviewDraft
    control: dict[str, Any]
    applied: bool


@dataclass(frozen=True, slots=True)
class _CandidateEvidence:
    meal_id: str
    meal_type: str
    display_name: str | None
    normalized_name: str
    day_key: str
    logged_at: str
    logged_at_dt: datetime
    ingredients: list[MealIngredient]
    totals: MealTotals


@dataclass(frozen=True, slots=True)
class _EvaluatedCandidate:
    candidate: KnownPatternCandidate
    evidence_items: list[_CandidateEvidence]


def _sha256_short(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:32]


def _stable_payload_hash(value: dict[str, Any]) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _now_iso() -> str:
    return _format_instant(datetime.now(timezone.utc))


def _require_client_mutation_id(value: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError("Missing clientMutationId")
    if "/" in normalized:
        raise ValueError("Invalid clientMutationId")
    if len(normalized) > 128:
        raise ValueError("clientMutationId is too long")
    return normalized


def _require_document_id(value: str, *, field_name: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"Missing {field_name}")
    if "/" in normalized:
        raise ValueError(f"Invalid {field_name}")
    if len(normalized) > 128:
        raise ValueError(f"{field_name} is too long")
    return normalized


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


def _coerce_float(value: object) -> float:
    if isinstance(value, bool):
        return 0.0
    if isinstance(value, int | float):
        return float(value)
    return 0.0


def _normalize_draft_ingredients(value: object) -> list[MealIngredient]:
    if not isinstance(value, list):
        return []

    ingredients: list[MealIngredient] = []
    raw_items = cast(list[object], value)
    for index, raw_item in enumerate(raw_items[:50]):
        if not isinstance(raw_item, dict):
            continue
        item = cast(dict[str, Any], raw_item)
        name = item.get("name")
        if not isinstance(name, str) or not name.strip():
            continue
        ingredient_id = item.get("id")
        if not isinstance(ingredient_id, str) or not ingredient_id.strip():
            ingredient_id = _sha256_short(f"known-pattern-ingredient|{index}|{name}")
        try:
            ingredients.append(
                MealIngredient.model_validate(
                    {
                        "id": ingredient_id,
                        "name": name.strip(),
                        "amount": _coerce_float(item.get("amount")),
                        "unit": item.get("unit") if item.get("unit") in {"g", "ml"} else None,
                        "kcal": _coerce_float(item.get("kcal")),
                        "protein": _coerce_float(item.get("protein")),
                        "fat": _coerce_float(item.get("fat")),
                        "carbs": _coerce_float(item.get("carbs")),
                    }
                )
            )
        except ValueError:
            continue
    return ingredients


def _normalize_draft_totals(
    value: object,
    ingredients: list[MealIngredient],
) -> MealTotals:
    if isinstance(value, dict):
        try:
            return MealTotals.model_validate(value)
        except ValueError:
            pass

    return MealTotals(
        protein=sum(ingredient.protein for ingredient in ingredients),
        fat=sum(ingredient.fat for ingredient in ingredients),
        carbs=sum(ingredient.carbs for ingredient in ingredients),
        kcal=sum(ingredient.kcal for ingredient in ingredients),
    )


def _evidence_from_meal(raw_meal: object) -> _CandidateEvidence | None:
    meal = _as_meal_map(raw_meal)
    if meal is None or bool(meal.get("deleted")):
        return None

    normalized_name = _normalize_name(meal.get("name"))
    logged_at_dt = _parse_logged_at(meal.get("loggedAt") or meal.get("timestamp"))
    if normalized_name is None or logged_at_dt is None:
        return None

    logged_at = _format_instant(logged_at_dt)
    ingredients = _normalize_draft_ingredients(meal.get("ingredients"))
    return _CandidateEvidence(
        meal_id=_meal_id(meal, normalized_name, logged_at),
        meal_type=_normalize_meal_type(meal.get("type")),
        display_name=raw_name.strip()
        if isinstance((raw_name := meal.get("name")), str) and raw_name.strip()
        else None,
        normalized_name=normalized_name,
        day_key=_day_key(meal.get("dayKey"), logged_at_dt),
        logged_at=logged_at,
        logged_at_dt=logged_at_dt,
        ingredients=ingredients,
        totals=_normalize_draft_totals(meal.get("totals"), ingredients),
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


def _evaluated_candidates_from_meals(
    meals: Iterable[object],
    *,
    now: datetime | None = None,
) -> list[_EvaluatedCandidate]:
    evaluation_now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    grouped: dict[str, list[_CandidateEvidence]] = {}
    for raw_meal in meals:
        evidence = _evidence_from_meal(raw_meal)
        if evidence is None:
            continue
        subject_key = f"{evidence.meal_type}|{evidence.normalized_name}"
        grouped.setdefault(subject_key, []).append(evidence)

    evaluated: list[_EvaluatedCandidate] = []
    for subject_key, evidence_items in grouped.items():
        candidate = _candidate_from_group(
            subject_key,
            evidence_items,
            now=evaluation_now,
        )
        if candidate is not None:
            evaluated.append(_EvaluatedCandidate(candidate=candidate, evidence_items=evidence_items))

    evaluated.sort(
        key=lambda item: (
            item.candidate.confidenceBucket == "high",
            item.candidate.sourceCountBucket == "5_plus",
            item.candidate.lastSeenAt,
            item.candidate.candidateId,
        ),
        reverse=True,
    )
    return evaluated


def _control_key(candidate: KnownPatternCandidate) -> tuple[str, str]:
    return (candidate.subjectKeyHash, candidate.createdByRuleVersion)


def _control_expires_after(
    control: dict[str, Any],
    *,
    now: datetime,
) -> bool:
    expires_at = _parse_logged_at(control.get("expiresAt"))
    return expires_at is None or expires_at <= now


def _apply_known_pattern_controls(
    candidates: list[KnownPatternCandidate],
    controls: Iterable[dict[str, Any]],
    *,
    now: datetime,
    limit: int,
) -> list[KnownPatternCandidate]:
    controls_by_key: dict[tuple[str, str], dict[str, Any]] = {}
    for control in controls:
        subject_key_hash = control.get("subjectKeyHash")
        rule_version = control.get("createdByRuleVersion")
        if (
            isinstance(subject_key_hash, str)
            and isinstance(rule_version, str)
            and not _control_expires_after(control, now=now)
        ):
            controls_by_key[(subject_key_hash, rule_version)] = control

    items: list[KnownPatternCandidate] = []
    for candidate in candidates:
        control = controls_by_key.get(_control_key(candidate))
        control_state = control.get("state") if control else None
        if control_state == "declined":
            continue
        if control_state == "shown":
            items.append(candidate.model_copy(update={"state": "shown"}))
        else:
            items.append(candidate)
        if len(items) >= limit:
            break
    return items


def evaluate_known_pattern_candidates(
    meals: Iterable[object],
    *,
    limit: int = KNOWN_PATTERN_DEFAULT_LIMIT,
    now: datetime | None = None,
    controls: Iterable[dict[str, Any]] = (),
) -> KnownPatternCandidatesResponse:
    evaluation_now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    evaluated = _evaluated_candidates_from_meals(meals, now=evaluation_now)
    items = _apply_known_pattern_controls(
        [item.candidate for item in evaluated],
        controls,
        now=evaluation_now,
        limit=limit,
    )

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


def _user_ref(client: firestore.Client, user_id: str) -> firestore.DocumentReference:
    return client.collection(USERS_COLLECTION).document(user_id)


def _control_id(*, subject_key_hash: str, rule_version: str) -> str:
    return _sha256_short(f"{rule_version}|control|{subject_key_hash}")


def _control_ref(
    client: firestore.Client,
    user_id: str,
    control_id: str,
) -> firestore.DocumentReference:
    return _user_ref(client, user_id).collection(
        KNOWN_PATTERN_CONTROLS_SUBCOLLECTION
    ).document(control_id)


def _mutation_ref(
    client: firestore.Client,
    user_id: str,
    client_mutation_id: str,
) -> firestore.DocumentReference:
    return _user_ref(client, user_id).collection(
        KNOWN_PATTERN_MUTATION_DEDUPE_SUBCOLLECTION
    ).document(client_mutation_id)


def _snapshot_document(snapshot: Any, *, document_id_field: str) -> dict[str, Any]:
    payload = dict(snapshot.to_dict() or {})
    payload.setdefault(document_id_field, snapshot.id)
    return payload


def _stream_known_pattern_controls(user_id: str) -> list[dict[str, Any]]:
    client: firestore.Client = get_firestore()
    collection_ref = _user_ref(client, user_id).collection(
        KNOWN_PATTERN_CONTROLS_SUBCOLLECTION
    )
    return [
        _snapshot_document(snapshot, document_id_field="controlId")
        for snapshot in collection_ref.limit(KNOWN_PATTERN_MAX_CONTROL_DOCS).stream()
    ]


async def list_known_pattern_candidates_for_user(
    user_id: str,
    *,
    limit: int = KNOWN_PATTERN_DEFAULT_LIMIT,
) -> KnownPatternCandidatesResponse:
    try:
        meals, _next_cursor = await meal_service.list_history(
            user_id,
            limit_count=KNOWN_PATTERN_MAX_HISTORY_ITEMS,
        )
        controls = _stream_known_pattern_controls(user_id)
        return evaluate_known_pattern_candidates(meals, limit=limit, controls=controls)
    except FirestoreServiceError:
        raise
    except (FirebaseError, GoogleAPICallError, RetryError) as exc:
        logger.exception(
            "Failed to list Known Pattern candidates.",
            extra={"user_id": user_id},
        )
        raise FirestoreServiceError("Failed to list Known Pattern candidates.") from exc


def _mutation_record(
    *,
    user_id: str,
    client_mutation_id: str,
    kind: str,
    target_id: str,
    payload_hash: str,
    result_document: dict[str, Any],
    applied: bool,
    result_draft: dict[str, Any] | None = None,
) -> dict[str, Any]:
    record: dict[str, Any] = {
        "ownerUserId": user_id,
        "clientMutationId": client_mutation_id,
        "kind": kind,
        "targetId": target_id,
        "payloadHash": payload_hash,
        "resultDocument": result_document,
        "applied": applied,
        "createdAt": _now_iso(),
    }
    if result_draft is not None:
        record["resultDraft"] = result_draft
    return record


def _result_from_existing_mutation(
    data: dict[str, Any],
    *,
    client_mutation_id: str,
    kind: str,
    target_id: str,
    payload_hash: str,
) -> KnownPatternControlMutationResult:
    if (
        data.get("clientMutationId") != client_mutation_id
        or data.get("kind") != kind
        or data.get("targetId") != target_id
        or data.get("payloadHash") != payload_hash
    ):
        raise KnownPatternMutationDedupeConflictError(
            "clientMutationId was already used for a different Known Pattern mutation"
        )

    result_document = data.get("resultDocument")
    if not isinstance(result_document, dict):
        raise KnownPatternMutationDedupeConflictError(
            "clientMutationId record is incomplete"
        )
    return {"document": dict(cast(dict[str, Any], result_document)), "applied": False}


def _existing_mutation_document(
    client: firestore.Client,
    user_id: str,
    client_mutation_id: str,
) -> dict[str, Any] | None:
    snapshot = _mutation_ref(client, user_id, client_mutation_id).get()
    if not snapshot.exists:
        return None
    return _snapshot_document(snapshot, document_id_field="id")


def _existing_control_mutation_result(
    client: firestore.Client,
    *,
    user_id: str,
    client_mutation_id: str,
    kind: str,
    target_id: str,
    payload_hash: str,
) -> KnownPatternControlMutationResult | None:
    data = _existing_mutation_document(client, user_id, client_mutation_id)
    if data is None:
        return None
    return _result_from_existing_mutation(
        data,
        client_mutation_id=client_mutation_id,
        kind=kind,
        target_id=target_id,
        payload_hash=payload_hash,
    )


def _existing_review_draft_mutation_result(
    client: firestore.Client,
    *,
    user_id: str,
    client_mutation_id: str,
    kind: str,
    target_id: str,
    payload_hash: str,
) -> KnownPatternReviewDraftResult | None:
    data = _existing_mutation_document(client, user_id, client_mutation_id)
    if data is None:
        return None
    control_result = _result_from_existing_mutation(
        data,
        client_mutation_id=client_mutation_id,
        kind=kind,
        target_id=target_id,
        payload_hash=payload_hash,
    )
    result_draft = data.get("resultDraft")
    if not isinstance(result_draft, dict):
        raise KnownPatternMutationDedupeConflictError(
            "clientMutationId record is incomplete"
        )
    try:
        draft = KnownPatternReviewDraft.model_validate(result_draft)
    except ValidationError as exc:
        raise KnownPatternMutationDedupeConflictError(
            "clientMutationId record is incomplete"
        ) from exc
    return {
        "draft": draft,
        "control": control_result["document"],
        "applied": False,
    }


def _find_evaluated_candidate(
    meals: Iterable[object],
    *,
    candidate_id: str,
    subject_key_hash: str,
    rule_version: str,
) -> _EvaluatedCandidate:
    for item in _evaluated_candidates_from_meals(meals):
        candidate = item.candidate
        if (
            candidate.candidateId == candidate_id
            and candidate.subjectKeyHash == subject_key_hash
            and candidate.createdByRuleVersion == rule_version
        ):
            return item
    raise KnownPatternNotFoundError("Known Pattern candidate was not found")


def _control_document(
    *,
    user_id: str,
    candidate: KnownPatternCandidate,
    state: str,
    existing: dict[str, Any],
    now_iso: str,
) -> dict[str, Any]:
    return KnownPatternCandidateControl(
        controlId=_control_id(
            subject_key_hash=candidate.subjectKeyHash,
            rule_version=candidate.createdByRuleVersion,
        ),
        candidateId=candidate.candidateId,
        subjectKeyHash=candidate.subjectKeyHash,
        state=cast(Any, state),
        createdByRuleVersion=candidate.createdByRuleVersion,
        expiresAt=candidate.expiresAt,
        createdAt=str(existing.get("createdAt") or now_iso),
        updatedAt=now_iso,
    ).model_dump()


@firestore.transactional
def _mutate_control_transaction(
    transaction: firestore.Transaction,
    *,
    client: firestore.Client,
    user_id: str,
    candidate: KnownPatternCandidate,
    state: str,
    client_mutation_id: str,
    payload_hash: str,
    kind: str,
    result_draft: dict[str, Any] | None = None,
) -> KnownPatternControlMutationResult:
    control_id = _control_id(
        subject_key_hash=candidate.subjectKeyHash,
        rule_version=candidate.createdByRuleVersion,
    )
    control_ref = _control_ref(client, user_id, control_id)
    mutation_ref = _mutation_ref(client, user_id, client_mutation_id)
    mutation_snapshot = mutation_ref.get(transaction=transaction)
    if mutation_snapshot.exists:
        return _result_from_existing_mutation(
            _snapshot_document(mutation_snapshot, document_id_field="id"),
            client_mutation_id=client_mutation_id,
            kind=kind,
            target_id=candidate.candidateId,
            payload_hash=payload_hash,
        )

    control_snapshot = control_ref.get(transaction=transaction)
    existing = (
        _snapshot_document(control_snapshot, document_id_field="controlId")
        if control_snapshot.exists
        else {}
    )
    document = _control_document(
        user_id=user_id,
        candidate=candidate,
        state=state,
        existing=existing,
        now_iso=_now_iso(),
    )
    transaction.set(control_ref, document, merge=False)
    transaction.set(
        mutation_ref,
        _mutation_record(
            user_id=user_id,
            client_mutation_id=client_mutation_id,
            kind=kind,
            target_id=candidate.candidateId,
            payload_hash=payload_hash,
            result_document=document,
            applied=True,
            result_draft=result_draft,
        ),
        merge=False,
    )
    return {"document": document, "applied": True}


async def mark_known_pattern_candidate_control_for_user(
    user_id: str,
    candidate_id: str,
    request: KnownPatternCandidateControlRequest,
) -> KnownPatternControlMutationResult:
    normalized_candidate_id = _require_document_id(
        candidate_id,
        field_name="candidateId",
    )
    client_mutation_id = _require_client_mutation_id(request.clientMutationId)
    payload = request.model_dump()
    payload_hash = _stable_payload_hash(
        {
            "kind": "known_pattern_control",
            "candidateId": normalized_candidate_id,
            "request": payload,
        }
    )

    try:
        client: firestore.Client = get_firestore()
        existing_result = _existing_control_mutation_result(
            client,
            user_id=user_id,
            client_mutation_id=client_mutation_id,
            kind="known_pattern_control",
            target_id=normalized_candidate_id,
            payload_hash=payload_hash,
        )
        if existing_result is not None:
            return existing_result

        meals, _next_cursor = await meal_service.list_history(
            user_id,
            limit_count=KNOWN_PATTERN_MAX_HISTORY_ITEMS,
        )
        evaluated = _find_evaluated_candidate(
            meals,
            candidate_id=normalized_candidate_id,
            subject_key_hash=request.subjectKeyHash,
            rule_version=request.createdByRuleVersion,
        )
        return _mutate_control_transaction(
            client.transaction(),
            client=client,
            user_id=user_id,
            candidate=evaluated.candidate,
            state=request.action,
            client_mutation_id=client_mutation_id,
            payload_hash=payload_hash,
            kind="known_pattern_control",
        )
    except (KnownPatternMutationDedupeConflictError, KnownPatternNotFoundError):
        raise
    except FirestoreServiceError:
        raise
    except (FirebaseError, GoogleAPICallError, RetryError) as exc:
        logger.exception(
            "Failed to update Known Pattern control.",
            extra={"user_id": user_id, "candidate_id": normalized_candidate_id},
        )
        raise FirestoreServiceError("Failed to update Known Pattern control.") from exc


def _review_draft_from_evaluated(item: _EvaluatedCandidate) -> KnownPatternReviewDraft:
    latest = sorted(item.evidence_items, key=lambda evidence: evidence.logged_at_dt)[-1]
    meal_type = latest.meal_type if latest.meal_type in {"breakfast", "lunch", "dinner", "snack", "other"} else "other"
    return KnownPatternReviewDraft(
        name=latest.display_name,
        type=cast(MealType, meal_type),
        ingredients=latest.ingredients,
        totals=latest.totals,
        notes=None,
        tags=[],
    )


async def open_known_pattern_review_draft_for_user(
    user_id: str,
    candidate_id: str,
    request: KnownPatternReviewDraftRequest,
) -> KnownPatternReviewDraftResult:
    normalized_candidate_id = _require_document_id(
        candidate_id,
        field_name="candidateId",
    )
    client_mutation_id = _require_client_mutation_id(request.clientMutationId)
    payload = request.model_dump()
    payload_hash = _stable_payload_hash(
        {
            "kind": "known_pattern_review_draft",
            "candidateId": normalized_candidate_id,
            "request": payload,
        }
    )

    try:
        client: firestore.Client = get_firestore()
        existing_result = _existing_review_draft_mutation_result(
            client,
            user_id=user_id,
            client_mutation_id=client_mutation_id,
            kind="known_pattern_review_draft",
            target_id=normalized_candidate_id,
            payload_hash=payload_hash,
        )
        if existing_result is not None:
            return existing_result

        meals, _next_cursor = await meal_service.list_history(
            user_id,
            limit_count=KNOWN_PATTERN_MAX_HISTORY_ITEMS,
        )
        evaluated = _find_evaluated_candidate(
            meals,
            candidate_id=normalized_candidate_id,
            subject_key_hash=request.subjectKeyHash,
            rule_version=request.createdByRuleVersion,
        )
        draft = _review_draft_from_evaluated(evaluated)
        control_result = _mutate_control_transaction(
            client.transaction(),
            client=client,
            user_id=user_id,
            candidate=evaluated.candidate,
            state="shown",
            client_mutation_id=client_mutation_id,
            payload_hash=payload_hash,
            kind="known_pattern_review_draft",
            result_draft=draft.model_dump(),
        )
        return {
            "draft": draft,
            "control": control_result["document"],
            "applied": control_result["applied"],
        }
    except (KnownPatternMutationDedupeConflictError, KnownPatternNotFoundError):
        raise
    except FirestoreServiceError:
        raise
    except (FirebaseError, GoogleAPICallError, RetryError) as exc:
        logger.exception(
            "Failed to open Known Pattern review draft.",
            extra={"user_id": user_id, "candidate_id": normalized_candidate_id},
        )
        raise FirestoreServiceError("Failed to open Known Pattern review draft.") from exc


def read_export(user_ref: firestore.DocumentReference) -> dict[str, list[dict[str, Any]]]:
    return {
        "controls": [
            _snapshot_document(snapshot, document_id_field="controlId")
            for snapshot in user_ref.collection(KNOWN_PATTERN_CONTROLS_SUBCOLLECTION).stream()
        ],
        "mutationDedupe": [
            _snapshot_document(snapshot, document_id_field="id")
            for snapshot in user_ref.collection(KNOWN_PATTERN_MUTATION_DEDUPE_SUBCOLLECTION)
            .stream()
        ],
    }
