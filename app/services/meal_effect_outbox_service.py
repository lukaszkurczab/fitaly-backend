"""Durable Firestore outbox rows for meal side effects."""

from datetime import datetime, timedelta, timezone
import hashlib
from typing import Any
from uuid import uuid4

from google.cloud import firestore
from google.cloud.firestore_v1.base_query import FieldFilter

from app.core.firestore_constants import MEAL_EFFECT_OUTBOX_SUBCOLLECTION, USERS_COLLECTION

STATUS_PENDING = "pending"
STATUS_SUCCEEDED = "succeeded"
STATUS_DEAD_LETTER = "dead_letter"

KIND_MEAL_SAVED_STREAK_SYNC = "meal_saved.streak_sync"
KIND_MEAL_SAVED_SMART_MEMORY_CAPTURE = "meal_saved.smart_memory_capture"
KIND_MEAL_DELETED_STREAK_SYNC = "meal_deleted.streak_sync"
KIND_MEAL_DELETED_SMART_MEMORY_SOURCE_DELETE = (
    "meal_deleted.smart_memory_source_delete"
)

MAX_ERROR_MESSAGE_LENGTH = 240
MAX_RECONCILIATION_EVENTS = 50
MAX_ATTEMPT_COUNT = 5
INITIAL_BACKOFF_SECONDS = 60
MAX_BACKOFF_SECONDS = 3600
PROCESSING_LEASE_SECONDS = 300
DEFAULT_LEASE_OWNER = "meal_effect_outbox"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _parse_iso8601_utc(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _iso_after(seconds: int) -> str:
    return _iso_after_from(_now_iso(), seconds)


def _iso_after_from(now_iso: str, seconds: int) -> str:
    return (
        _parse_iso8601_utc(now_iso) + timedelta(seconds=max(seconds, 0))
    ).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def event_id(*, source_mutation_id: str, kind: str) -> str:
    digest = hashlib.sha256(f"{source_mutation_id}:{kind}".encode("utf-8")).hexdigest()
    return f"meal-effect-{digest}"


def meal_effect_outbox_collection(
    client: firestore.Client,
    user_id: str,
) -> firestore.CollectionReference:
    return (
        client.collection(USERS_COLLECTION)
        .document(user_id)
        .collection(MEAL_EFFECT_OUTBOX_SUBCOLLECTION)
    )


def meal_effect_outbox_ref(
    client: firestore.Client,
    user_id: str,
    event_id_value: str,
) -> firestore.DocumentReference:
    return meal_effect_outbox_collection(client, user_id).document(event_id_value)


def build_pending_event(
    *,
    owner_user_id: str,
    source_mutation_id: str,
    source_entity_id: str,
    kind: str,
    reference_day_key: str | None,
    result_meal: dict[str, Any],
) -> dict[str, Any]:
    now = _now_iso()
    event_id_value = event_id(source_mutation_id=source_mutation_id, kind=kind)
    return {
        "eventId": event_id_value,
        "ownerUserId": owner_user_id,
        "sourceMutationId": source_mutation_id,
        "sourceEntityId": source_entity_id,
        "sourceEntityType": "meal",
        "kind": kind,
        "status": STATUS_PENDING,
        "attemptCount": 0,
        "nextAttemptAt": now,
        "createdAt": now,
        "updatedAt": now,
        "lastErrorCode": None,
        "lastErrorMessage": None,
        "lastErrorAt": None,
        "leaseToken": None,
        "leaseOwner": None,
        "leaseExpiresAt": None,
        "leasedAt": None,
        "referenceDayKey": reference_day_key,
        "resultMeal": result_meal,
    }


def snapshot_to_event(snapshot: firestore.DocumentSnapshot) -> dict[str, Any] | None:
    if getattr(snapshot, "exists", False) is not True:
        return None
    payload = snapshot.to_dict() or {}
    if not isinstance(payload, dict):
        return None
    event = dict(payload)
    event.setdefault("eventId", snapshot.id)
    return event


def _next_attempt_count(event: dict[str, Any]) -> int:
    try:
        return int(event.get("attemptCount") or 0) + 1
    except (TypeError, ValueError):
        return 1


def _backoff_seconds_for_attempt(attempt_count: int) -> int:
    exponent = max(attempt_count - 1, 0)
    return min(INITIAL_BACKOFF_SECONDS * (2**exponent), MAX_BACKOFF_SECONDS)


def _is_event_due(event: dict[str, Any], *, now_iso: str) -> bool:
    if event.get("status") != STATUS_PENDING:
        return False
    next_attempt_at = event.get("nextAttemptAt")
    if not isinstance(next_attempt_at, str) or not next_attempt_at.strip():
        return True
    return next_attempt_at <= now_iso


def _is_lease_available(event: dict[str, Any], *, now_iso: str) -> bool:
    lease_token = event.get("leaseToken")
    lease_expires_at = event.get("leaseExpiresAt")
    if not isinstance(lease_token, str) or not lease_token.strip():
        return True
    if not isinstance(lease_expires_at, str) or not lease_expires_at.strip():
        return True
    try:
        return _parse_iso8601_utc(lease_expires_at) <= _parse_iso8601_utc(now_iso)
    except ValueError:
        return True


@firestore.transactional
def _claim_pending_event_transaction(
    transaction: firestore.Transaction,
    event_ref: firestore.DocumentReference,
    *,
    now_iso: str,
    lease_token: str,
    lease_owner: str,
    lease_expires_at: str,
) -> dict[str, Any] | None:
    event = snapshot_to_event(event_ref.get(transaction=transaction))
    if event is None:
        return None
    if not _is_event_due(event, now_iso=now_iso):
        return None
    if not _is_lease_available(event, now_iso=now_iso):
        return None

    lease_update = {
        "leaseToken": lease_token,
        "leaseOwner": lease_owner,
        "leaseExpiresAt": lease_expires_at,
        "leasedAt": now_iso,
        "nextAttemptAt": lease_expires_at,
        "updatedAt": now_iso,
    }
    transaction.set(event_ref, lease_update, merge=True)
    return {**event, **lease_update}


def claim_pending_event(
    client: firestore.Client,
    event_ref: firestore.DocumentReference,
    *,
    lease_owner: str = DEFAULT_LEASE_OWNER,
    lease_seconds: int = PROCESSING_LEASE_SECONDS,
) -> dict[str, Any] | None:
    now_iso = _now_iso()
    transaction = client.transaction()
    return _claim_pending_event_transaction(
        transaction,
        event_ref,
        now_iso=now_iso,
        lease_token=uuid4().hex,
        lease_owner=lease_owner,
        lease_expires_at=_iso_after_from(now_iso, lease_seconds),
    )


def _lease_matches(event: dict[str, Any], expected_event: dict[str, Any]) -> bool:
    lease_token = event.get("leaseToken")
    expected_lease_token = expected_event.get("leaseToken")
    return (
        isinstance(lease_token, str)
        and bool(lease_token)
        and lease_token == expected_lease_token
    )


def _clear_lease_update() -> dict[str, None]:
    return {
        "leaseToken": None,
        "leaseOwner": None,
        "leaseExpiresAt": None,
        "leasedAt": None,
    }


@firestore.transactional
def _mark_succeeded_transaction(
    transaction: firestore.Transaction,
    event_ref: firestore.DocumentReference,
    event: dict[str, Any],
    *,
    now_iso: str,
) -> bool:
    current_event = snapshot_to_event(event_ref.get(transaction=transaction))
    if current_event is None:
        return False
    if current_event.get("status") != STATUS_PENDING:
        return False
    if not _lease_matches(current_event, event):
        return False

    transaction.set(
        event_ref,
        {
            "status": STATUS_SUCCEEDED,
            "attemptCount": _next_attempt_count(current_event),
            "updatedAt": now_iso,
            "nextAttemptAt": None,
            "lastErrorCode": None,
            "lastErrorMessage": None,
            "lastErrorAt": None,
            "deadLetterAt": None,
            **_clear_lease_update(),
        },
        merge=True,
    )
    return True


def mark_succeeded(
    client: firestore.Client,
    event_ref: firestore.DocumentReference,
    event: dict[str, Any],
) -> bool:
    now = _now_iso()
    return _mark_succeeded_transaction(
        client.transaction(),
        event_ref,
        event,
        now_iso=now,
    )


@firestore.transactional
def _mark_failed_transaction(
    transaction: firestore.Transaction,
    event_ref: firestore.DocumentReference,
    event: dict[str, Any],
    *,
    exc: Exception,
    now_iso: str,
) -> bool:
    current_event = snapshot_to_event(event_ref.get(transaction=transaction))
    if current_event is None:
        return False
    if current_event.get("status") != STATUS_PENDING:
        return False
    if not _lease_matches(current_event, event):
        return False

    attempt_count = _next_attempt_count(current_event)
    reached_dead_letter = attempt_count >= MAX_ATTEMPT_COUNT
    error_message = str(exc)
    transaction.set(
        event_ref,
        {
            "status": STATUS_DEAD_LETTER if reached_dead_letter else STATUS_PENDING,
            "attemptCount": attempt_count,
            "updatedAt": now_iso,
            "nextAttemptAt": None
            if reached_dead_letter
            else _iso_after_from(now_iso, _backoff_seconds_for_attempt(attempt_count)),
            "lastErrorCode": exc.__class__.__name__,
            "lastErrorMessage": error_message[:MAX_ERROR_MESSAGE_LENGTH],
            "lastErrorAt": now_iso,
            "deadLetterAt": now_iso if reached_dead_letter else None,
            **_clear_lease_update(),
        },
        merge=True,
    )
    return True


def mark_failed(
    client: firestore.Client,
    event_ref: firestore.DocumentReference,
    event: dict[str, Any],
    exc: Exception,
) -> bool:
    now = _now_iso()
    return _mark_failed_transaction(
        client.transaction(),
        event_ref,
        event,
        exc=exc,
        now_iso=now,
    )


def list_pending_events(
    client: firestore.Client,
    user_id: str,
    *,
    limit_count: int = MAX_RECONCILIATION_EVENTS,
) -> list[tuple[firestore.DocumentReference, dict[str, Any]]]:
    limit = min(max(limit_count, 1), MAX_RECONCILIATION_EVENTS)
    now_iso = _now_iso()
    query = (
        meal_effect_outbox_collection(client, user_id)
        .where(filter=FieldFilter("status", "==", STATUS_PENDING))
        .order_by("nextAttemptAt")
    )
    events: list[tuple[firestore.DocumentReference, dict[str, Any]]] = []
    for snapshot in query.limit(limit).stream():
        event = snapshot_to_event(snapshot)
        if event is not None and _is_event_due(event, now_iso=now_iso):
            events.append((snapshot.reference, event))
    return events
