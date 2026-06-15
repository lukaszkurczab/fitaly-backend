"""Backend-owned storage and controls for Smart Memory."""

from datetime import datetime, timezone
import hashlib
import json
import logging
from collections.abc import Sequence
from typing import Any, TypedDict, cast

from firebase_admin.exceptions import FirebaseError
from google.api_core.exceptions import GoogleAPICallError, RetryError
from google.cloud import firestore
from google.cloud.firestore_v1.base_query import FieldFilter

from app.core.exceptions import FirestoreServiceError
from app.core.firestore_constants import (
    SMART_MEMORY_CANDIDATES_SUBCOLLECTION,
    SMART_MEMORY_MUTATION_DEDUPE_SUBCOLLECTION,
    SMART_MEMORY_SETTINGS_DOCUMENT_ID,
    SMART_MEMORY_SETTINGS_SUBCOLLECTION,
    SMART_MEMORY_SUBCOLLECTION,
    SMART_MEMORY_TOMBSTONES_SUBCOLLECTION,
    USERS_COLLECTION,
)
from app.db.firebase import get_firestore
from app.schemas.smart_memory import (
    SMART_MEMORY_USER_VALUE_REASON_CODES,
    SmartMemoryCandidate,
    SmartMemoryCandidateUpsertRequest,
    SmartMemoryItem,
    SmartMemoryItemPatchRequest,
    SmartMemorySettingsUpdateRequest,
    SmartMemorySourceDeletedRequest,
    SmartMemoryType,
)

logger = logging.getLogger(__name__)

MAX_LIST_LIMIT = 250
MAX_SOURCE_REFS = 25
MAX_REASON_CODES = 20
MAX_CAPTURE_CONTROL_DOCS = 100
MAX_SOURCE_HASH_QUERY_DOCS = 250
MAX_EXPORT_COLLECTION_DOCS = 250
SOURCE_DELETION_SOURCE_REF_KIND = "meal_portion_observation"
MUTABLE_ITEM_STATES = {"active", "muted", "candidate"}
NON_SUGGESTING_STATES = {"muted", "deleted_suppressed", "disabled", "source_deleted"}
SUPPRESSED_CANDIDATE_STATES = {"deleted_suppressed", "source_deleted"}
USER_VALUE_ALLOWED_KEYS: dict[str, set[str]] = {
    "typical_portion": {"amount", "unit"},
    "review_correction": {"amount", "unit", "reasonCode"},
    "ingredient_product_selection": {"displayLabel", "alias", "ingredientProductId"},
}
PORTION_UNITS = {"g", "ml", "piece", "serving"}


class SmartMemoryNotFoundError(ValueError):
    """Raised when a Smart Memory item or candidate does not exist."""


class SmartMemoryMutationDedupeConflictError(ValueError):
    """Raised when a clientMutationId is reused for a different mutation."""


class SmartMemoryMutationResult(TypedDict):
    document: dict[str, Any]
    applied: bool


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _stable_payload_hash(value: dict[str, Any]) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _require_document_id(value: str, *, field_name: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"Missing {field_name}")
    if "/" in normalized:
        raise ValueError(f"Invalid {field_name}")
    if len(normalized) > 128:
        raise ValueError(f"{field_name} is too long")
    return normalized


def _require_client_mutation_id(value: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError("Missing clientMutationId")
    return normalized


def _user_ref(client: firestore.Client, user_id: str) -> firestore.DocumentReference:
    return client.collection(USERS_COLLECTION).document(user_id)


def _memory_item_ref(
    client: firestore.Client,
    user_id: str,
    memory_item_id: str,
) -> firestore.DocumentReference:
    return _user_ref(client, user_id).collection(SMART_MEMORY_SUBCOLLECTION).document(
        memory_item_id
    )


def _candidate_ref(
    client: firestore.Client,
    user_id: str,
    candidate_id: str,
) -> firestore.DocumentReference:
    return _user_ref(client, user_id).collection(
        SMART_MEMORY_CANDIDATES_SUBCOLLECTION
    ).document(candidate_id)


def _settings_ref(client: firestore.Client, user_id: str) -> firestore.DocumentReference:
    return _user_ref(client, user_id).collection(
        SMART_MEMORY_SETTINGS_SUBCOLLECTION
    ).document(SMART_MEMORY_SETTINGS_DOCUMENT_ID)


def _mutation_ref(
    client: firestore.Client,
    user_id: str,
    client_mutation_id: str,
) -> firestore.DocumentReference:
    return _user_ref(client, user_id).collection(
        SMART_MEMORY_MUTATION_DEDUPE_SUBCOLLECTION
    ).document(client_mutation_id)


def _tombstone_ref(
    client: firestore.Client,
    user_id: str,
    tombstone_id: str,
) -> firestore.DocumentReference:
    return _user_ref(client, user_id).collection(
        SMART_MEMORY_TOMBSTONES_SUBCOLLECTION
    ).document(tombstone_id)


def _snapshot_document(snapshot: Any, *, document_id_field: str) -> dict[str, Any]:
    payload = dict(snapshot.to_dict() or {})
    payload.setdefault(document_id_field, snapshot.id)
    return payload


def _read_subcollection(
    user_ref: firestore.DocumentReference,
    subcollection_name: str,
) -> list[dict[str, Any]]:
    documents: list[dict[str, Any]] = []
    for snapshot in user_ref.collection(subcollection_name).stream():
        payload = dict(snapshot.to_dict() or {})
        payload.setdefault("id", snapshot.id)
        documents.append(payload)
    return documents


def _read_limited_subcollection(
    user_ref: firestore.DocumentReference,
    subcollection_name: str,
    *,
    document_id_field: str = "id",
    limit_count: int = MAX_EXPORT_COLLECTION_DOCS,
) -> list[dict[str, Any]]:
    collection_ref = user_ref.collection(subcollection_name)
    limit = min(max(limit_count, 1), MAX_EXPORT_COLLECTION_DOCS)
    snapshots = _stream_bounded_collection(collection_ref, limit_count=limit)
    if len(snapshots) == limit:
        logger.warning(
            "Smart Memory export reached bounded collection limit.",
            extra={"subcollection": subcollection_name, "limit": limit},
        )
    return [
        _snapshot_document(snapshot, document_id_field=document_id_field)
        for snapshot in snapshots
    ]


def _stream_bounded_collection(
    collection_ref: Any,
    *,
    limit_count: int = MAX_CAPTURE_CONTROL_DOCS,
) -> list[Any]:
    limit = min(max(limit_count, 1), MAX_LIST_LIMIT)
    limited_ref = collection_ref.limit(limit) if hasattr(collection_ref, "limit") else collection_ref
    return list(limited_ref.stream())[:limit]


def _chunks(values: Sequence[dict[str, Any]], chunk_size: int) -> list[list[dict[str, Any]]]:
    return [list(values[index : index + chunk_size]) for index in range(0, len(values), chunk_size)]


def _mutation_record(
    *,
    user_id: str,
    client_mutation_id: str,
    kind: str,
    target_id: str,
    payload_hash: str,
    result_document: dict[str, Any],
    applied: bool,
) -> dict[str, Any]:
    return {
        "ownerUserId": user_id,
        "clientMutationId": client_mutation_id,
        "kind": kind,
        "targetId": target_id,
        "payloadHash": payload_hash,
        "resultDocument": result_document,
        "applied": applied,
        "createdAt": _now_iso(),
    }


def _result_from_existing_mutation(
    data: dict[str, Any],
    *,
    client_mutation_id: str,
    kind: str,
    target_id: str,
    payload_hash: str,
) -> SmartMemoryMutationResult:
    if (
        data.get("clientMutationId") != client_mutation_id
        or data.get("kind") != kind
        or data.get("targetId") != target_id
        or data.get("payloadHash") != payload_hash
    ):
        raise SmartMemoryMutationDedupeConflictError(
            "clientMutationId was already used for a different Smart Memory mutation"
        )

    result_document = data.get("resultDocument")
    if not isinstance(result_document, dict):
        raise SmartMemoryMutationDedupeConflictError("clientMutationId record is incomplete")
    return {"document": dict(cast(dict[str, Any], result_document)), "applied": False}


def _next_revision(payload: dict[str, Any]) -> int:
    revision = payload.get("serverRevision")
    if isinstance(revision, int) and revision >= 1:
        return revision + 1
    return 1


def _validate_bounded_evidence(
    *,
    source_refs: list[dict[str, Any]],
    confidence_reason_codes: Sequence[str],
) -> None:
    if len(source_refs) > MAX_SOURCE_REFS:
        raise ValueError("Smart Memory sourceRefs exceeds bounded limit")
    if len(confidence_reason_codes) > MAX_REASON_CODES:
        raise ValueError("Smart Memory confidenceReasonCodes exceeds bounded limit")


def _subject_key(memory_type: SmartMemoryType, subject: dict[str, Any], fallback_id: str) -> str:
    if subject:
        return f"{memory_type}:{_stable_payload_hash(subject)}"
    return f"{memory_type}:{fallback_id}"


def _tombstone_id(memory_type: SmartMemoryType, subject: dict[str, Any], fallback_id: str) -> str:
    return hashlib.sha256(
        _subject_key(memory_type, subject, fallback_id).encode("utf-8")
    ).hexdigest()


def _tombstone_id_from_subject_key(subject_key: str) -> str:
    return hashlib.sha256(subject_key.encode("utf-8")).hexdigest()


def _tombstone_document(
    *,
    user_id: str,
    memory_type: SmartMemoryType,
    subject: dict[str, Any],
    fallback_id: str,
    deleted_at: str,
    delete_revision: int,
    reason_code: str,
) -> dict[str, Any]:
    subject_key = _subject_key(memory_type, subject, fallback_id)
    return {
        "tombstoneId": _tombstone_id_from_subject_key(subject_key),
        "ownerUserId": user_id,
        "memoryType": memory_type,
        "subjectKey": subject_key,
        "deletedAt": deleted_at,
        "deleteRevision": delete_revision,
        "reasonCode": reason_code,
    }


def _tombstone_document_from_subject_key(
    *,
    user_id: str,
    memory_type: SmartMemoryType,
    subject_key: str,
    deleted_at: str,
    reason_code: str,
) -> dict[str, Any]:
    return {
        "tombstoneId": _tombstone_id_from_subject_key(subject_key),
        "ownerUserId": user_id,
        "memoryType": memory_type,
        "subjectKey": subject_key,
        "deletedAt": deleted_at,
        "deleteRevision": 1,
        "reasonCode": reason_code,
    }


def _validate_source_refs_not_deleted(source_refs: list[dict[str, Any]]) -> None:
    for source_ref in source_refs:
        if source_ref.get("deleted") is True:
            raise ValueError("Smart Memory sourceRef is deleted")
        state = source_ref.get("state")
        if state in {"deleted", "deleted_suppressed", "source_deleted"}:
            raise ValueError("Smart Memory sourceRef is unavailable")


def _validate_candidate_suppression_checks(suppression_checks: dict[str, Any]) -> None:
    if suppression_checks.get("deletedSuppressed") is True:
        raise ValueError("Smart Memory candidate is suppressed by user delete")
    if suppression_checks.get("sourceDeleted") is True:
        raise ValueError("Smart Memory candidate source is deleted")


def _document_references_source_hash(
    document: dict[str, Any],
    source_hashes: set[str],
) -> bool:
    raw_source_refs = document.get("sourceRefs")
    if not isinstance(raw_source_refs, list):
        return False
    source_refs = cast(list[object], raw_source_refs)
    for raw_source_ref in source_refs:
        if not isinstance(raw_source_ref, dict):
            continue
        source_ref = cast(dict[object, object], raw_source_ref)
        source_hash = source_ref.get("sourceHash")
        if isinstance(source_hash, str) and source_hash in source_hashes:
            return True
    return False


def _source_refs_for_hash_query(source_hashes: set[str]) -> list[dict[str, Any]]:
    return [
        {"kind": SOURCE_DELETION_SOURCE_REF_KIND, "sourceHash": source_hash}
        for source_hash in sorted(source_hashes)
    ]


def _stream_documents_for_source_hashes(
    collection_ref: Any,
    source_hashes: set[str],
    *,
    limit_count: int = MAX_SOURCE_HASH_QUERY_DOCS,
) -> list[Any]:
    snapshots_by_id: dict[str, Any] = {}
    limit = min(max(limit_count, 1), MAX_SOURCE_HASH_QUERY_DOCS)
    for refs_chunk in _chunks(_source_refs_for_hash_query(source_hashes), 10):
        query = collection_ref.where(
            filter=FieldFilter("sourceRefs", "array_contains_any", refs_chunk)
        )
        for snapshot in _stream_bounded_collection(query, limit_count=limit):
            snapshots_by_id[snapshot.id] = snapshot
    return list(snapshots_by_id.values())


def _validate_user_value_for_memory_type(
    memory_type: str,
    user_value: dict[str, Any],
) -> None:
    allowed_keys = USER_VALUE_ALLOWED_KEYS.get(memory_type)
    if not allowed_keys:
        raise ValueError("Unsupported Smart Memory memoryType")
    unknown_keys = set(user_value) - allowed_keys
    if unknown_keys:
        raise ValueError("Smart Memory userValue contains unsupported fields")
    if memory_type in {"typical_portion", "review_correction"}:
        amount = user_value.get("amount")
        if isinstance(amount, bool) or not isinstance(amount, (int, float)) or amount <= 0:
            raise ValueError("Smart Memory userValue amount must be positive")
        unit = user_value.get("unit")
        if not isinstance(unit, str) or unit not in PORTION_UNITS:
            raise ValueError("Smart Memory userValue unit is unsupported")
        reason_code = user_value.get("reasonCode")
        if reason_code is not None and (
            not isinstance(reason_code, str) or not reason_code.strip()
        ):
            raise ValueError("Smart Memory userValue reasonCode must be a string")
        if (
            reason_code is not None
            and reason_code not in SMART_MEMORY_USER_VALUE_REASON_CODES
        ):
            raise ValueError("Smart Memory userValue reasonCode is unsupported")
    if memory_type == "ingredient_product_selection":
        for field_name in ("displayLabel", "alias", "ingredientProductId"):
            field_value = user_value.get(field_name)
            if field_value is not None and (
                not isinstance(field_value, str) or not field_value.strip()
            ):
                raise ValueError(
                    "Smart Memory ingredient/product userValue fields must be strings"
                )
        if not any(
            isinstance(user_value.get(field_name), str) and user_value[field_name].strip()
            for field_name in ("displayLabel", "alias", "ingredientProductId")
        ):
            raise ValueError("Smart Memory userValue requires a safe label or reference")


def _settings_document(
    *,
    user_id: str,
    enabled: bool,
    updated_at: str,
    disabled_at: str | None,
    server_revision: int,
    client_mutation_id: str | None,
) -> dict[str, Any]:
    return {
        "ownerUserId": user_id,
        "enabled": enabled,
        "disabledAt": disabled_at,
        "updatedAt": updated_at,
        "serverRevision": server_revision,
        "clientMutationId": client_mutation_id,
    }


async def list_items(user_id: str, *, limit_count: int = 100) -> list[dict[str, Any]]:
    limit = min(max(limit_count, 1), MAX_LIST_LIMIT)
    client = get_firestore()
    collection_ref = _user_ref(client, user_id).collection(SMART_MEMORY_SUBCOLLECTION)
    try:
        documents = [
            _snapshot_document(snapshot, document_id_field="memoryItemId")
            for snapshot in _stream_bounded_collection(collection_ref, limit_count=limit)
        ]
    except (FirebaseError, GoogleAPICallError, RetryError) as exc:
        logger.exception("Failed to list Smart Memory items.", extra={"user_id": user_id})
        raise FirestoreServiceError("Failed to list Smart Memory items.") from exc
    documents = [
        document
        for document in documents
        if document.get("state") not in {"deleted_suppressed"}
    ]
    documents.sort(key=lambda item: str(item.get("updatedAt") or ""), reverse=True)
    return documents[:limit]


async def get_item(user_id: str, memory_item_id: str) -> dict[str, Any]:
    client = get_firestore()
    item_id = _require_document_id(memory_item_id, field_name="memoryItemId")
    try:
        snapshot = _memory_item_ref(client, user_id, item_id).get()
    except (FirebaseError, GoogleAPICallError, RetryError) as exc:
        logger.exception("Failed to read Smart Memory item.", extra={"user_id": user_id})
        raise FirestoreServiceError("Failed to read Smart Memory item.") from exc
    if not snapshot.exists:
        raise SmartMemoryNotFoundError("Smart Memory item was not found")
    return _snapshot_document(snapshot, document_id_field="memoryItemId")


async def list_candidates(user_id: str, *, limit_count: int = 100) -> list[dict[str, Any]]:
    limit = min(max(limit_count, 1), MAX_LIST_LIMIT)
    client = get_firestore()
    collection_ref = _user_ref(client, user_id).collection(
        SMART_MEMORY_CANDIDATES_SUBCOLLECTION
    )
    try:
        documents = [
            _snapshot_document(snapshot, document_id_field="candidateId")
            for snapshot in _stream_bounded_collection(collection_ref, limit_count=limit)
        ]
    except (FirebaseError, GoogleAPICallError, RetryError) as exc:
        logger.exception("Failed to list Smart Memory candidates.", extra={"user_id": user_id})
        raise FirestoreServiceError("Failed to list Smart Memory candidates.") from exc
    documents = [document for document in documents if document.get("state") == "candidate"]
    documents.sort(key=lambda item: str(item.get("updatedAt") or ""), reverse=True)
    return documents[:limit]


async def get_candidate(user_id: str, candidate_id: str) -> dict[str, Any]:
    client = get_firestore()
    normalized_candidate_id = _require_document_id(candidate_id, field_name="candidateId")
    try:
        snapshot = _candidate_ref(client, user_id, normalized_candidate_id).get()
    except (FirebaseError, GoogleAPICallError, RetryError) as exc:
        logger.exception(
            "Failed to read Smart Memory candidate.",
            extra={"user_id": user_id},
        )
        raise FirestoreServiceError("Failed to read Smart Memory candidate.") from exc
    if not snapshot.exists:
        raise SmartMemoryNotFoundError("Smart Memory candidate was not found")
    return _snapshot_document(snapshot, document_id_field="candidateId")


async def get_settings(user_id: str) -> dict[str, Any]:
    client = get_firestore()
    try:
        snapshot = _settings_ref(client, user_id).get()
    except (FirebaseError, GoogleAPICallError, RetryError) as exc:
        logger.exception("Failed to read Smart Memory settings.", extra={"user_id": user_id})
        raise FirestoreServiceError("Failed to read Smart Memory settings.") from exc
    if snapshot.exists:
        return dict(snapshot.to_dict() or {})
    return _settings_document(
        user_id=user_id,
        enabled=True,
        updated_at=_now_iso(),
        disabled_at=None,
        server_revision=1,
        client_mutation_id=None,
    )


async def list_tombstone_subject_keys(
    user_id: str,
    *,
    memory_type: SmartMemoryType | None = None,
    limit_count: int = MAX_CAPTURE_CONTROL_DOCS,
) -> list[str]:
    client = get_firestore()
    try:
        snapshots = _stream_bounded_collection(
            _user_ref(client, user_id).collection(SMART_MEMORY_TOMBSTONES_SUBCOLLECTION),
            limit_count=limit_count,
        )
    except (FirebaseError, GoogleAPICallError, RetryError) as exc:
        logger.exception(
            "Failed to list Smart Memory tombstones.",
            extra={"user_id": user_id},
        )
        raise FirestoreServiceError("Failed to list Smart Memory tombstones.") from exc

    subject_keys: list[str] = []
    for snapshot in snapshots:
        document = _snapshot_document(snapshot, document_id_field="tombstoneId")
        if memory_type is not None and document.get("memoryType") != memory_type:
            continue
        subject_key = document.get("subjectKey")
        if isinstance(subject_key, str) and subject_key.strip():
            subject_keys.append(subject_key.strip())
    return sorted(set(subject_keys))


async def list_suppressed_subject_keys(
    user_id: str,
    *,
    memory_type: SmartMemoryType,
    limit_count: int = MAX_CAPTURE_CONTROL_DOCS,
) -> list[str]:
    client = get_firestore()
    user_ref = _user_ref(client, user_id)
    subject_keys = set(
        await list_tombstone_subject_keys(
            user_id,
            memory_type=memory_type,
            limit_count=limit_count,
        )
    )
    try:
        for snapshot in _stream_bounded_collection(
            user_ref.collection(SMART_MEMORY_SUBCOLLECTION),
            limit_count=limit_count,
        ):
            document = _snapshot_document(snapshot, document_id_field="memoryItemId")
            if document.get("memoryType") != memory_type:
                continue
            if document.get("state") not in {"deleted_suppressed", "source_deleted"}:
                continue
            subject = document.get("subject")
            if isinstance(subject, dict) and subject:
                subject_keys.add(
                    _subject_key(
                        memory_type,
                        cast(dict[str, Any], subject),
                        str(document.get("memoryItemId") or snapshot.id),
                    )
                )

        for snapshot in _stream_bounded_collection(
            user_ref.collection(SMART_MEMORY_CANDIDATES_SUBCOLLECTION),
            limit_count=limit_count,
        ):
            document = _snapshot_document(snapshot, document_id_field="candidateId")
            if document.get("memoryType") != memory_type:
                continue
            if document.get("state") not in SUPPRESSED_CANDIDATE_STATES:
                continue
            subject = document.get("subject")
            if isinstance(subject, dict) and subject:
                subject_keys.add(
                    _subject_key(
                        memory_type,
                        cast(dict[str, Any], subject),
                        str(document.get("candidateId") or snapshot.id),
                    )
                )
    except (FirebaseError, GoogleAPICallError, RetryError) as exc:
        logger.exception(
            "Failed to list Smart Memory suppressed subjects.",
            extra={"user_id": user_id},
        )
        raise FirestoreServiceError("Failed to list Smart Memory suppressed subjects.") from exc

    return sorted(subject_keys)


async def filter_existing_tombstone_subject_keys(
    user_id: str,
    subject_keys: Sequence[str],
    *,
    limit_count: int = MAX_CAPTURE_CONTROL_DOCS,
) -> list[str]:
    normalized_subject_keys: list[str] = []
    seen: set[str] = set()
    limit = min(max(limit_count, 1), MAX_LIST_LIMIT)
    for subject_key in subject_keys:
        if not isinstance(subject_key, str):
            continue
        normalized = subject_key.strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        normalized_subject_keys.append(normalized)
        if len(normalized_subject_keys) >= limit:
            break

    if not normalized_subject_keys:
        return []

    client = get_firestore()
    try:
        existing: list[str] = []
        for subject_key in normalized_subject_keys:
            snapshot = _tombstone_ref(
                client,
                user_id,
                _tombstone_id_from_subject_key(subject_key),
            ).get()
            if snapshot.exists:
                existing.append(subject_key)
    except (FirebaseError, GoogleAPICallError, RetryError) as exc:
        logger.exception(
            "Failed to check Smart Memory tombstones.",
            extra={"user_id": user_id},
        )
        raise FirestoreServiceError("Failed to check Smart Memory tombstones.") from exc

    return existing


async def update_settings(
    user_id: str,
    payload: SmartMemorySettingsUpdateRequest,
) -> SmartMemoryMutationResult:
    client = get_firestore()
    client_mutation_id = _require_client_mutation_id(payload.clientMutationId)
    mutation_payload: dict[str, Any] = {
        "kind": "settings_update",
        "targetId": SMART_MEMORY_SETTINGS_DOCUMENT_ID,
        "enabled": payload.enabled,
    }
    payload_hash = _stable_payload_hash(mutation_payload)
    try:
        return _update_settings_transaction(
            client.transaction(),
            client=client,
            user_id=user_id,
            enabled=payload.enabled,
            client_mutation_id=client_mutation_id,
            payload_hash=payload_hash,
        )
    except SmartMemoryMutationDedupeConflictError:
        raise
    except (FirebaseError, GoogleAPICallError, RetryError) as exc:
        logger.exception("Failed to update Smart Memory settings.", extra={"user_id": user_id})
        raise FirestoreServiceError("Failed to update Smart Memory settings.") from exc


@firestore.transactional
def _update_settings_transaction(
    transaction: firestore.Transaction,
    *,
    client: firestore.Client,
    user_id: str,
    enabled: bool,
    client_mutation_id: str,
    payload_hash: str,
) -> SmartMemoryMutationResult:
    kind = "settings_update"
    target_id = SMART_MEMORY_SETTINGS_DOCUMENT_ID
    mutation_ref = _mutation_ref(client, user_id, client_mutation_id)
    mutation_snapshot = mutation_ref.get(transaction=transaction)
    if mutation_snapshot.exists:
        return _result_from_existing_mutation(
            dict(mutation_snapshot.to_dict() or {}),
            client_mutation_id=client_mutation_id,
            kind=kind,
            target_id=target_id,
            payload_hash=payload_hash,
        )

    now = _now_iso()
    settings_ref = _settings_ref(client, user_id)
    settings_snapshot = settings_ref.get(transaction=transaction)
    existing = dict(settings_snapshot.to_dict() or {}) if settings_snapshot.exists else {}
    document = _settings_document(
        user_id=user_id,
        enabled=enabled,
        updated_at=now,
        disabled_at=None if enabled else now,
        server_revision=_next_revision(existing),
        client_mutation_id=client_mutation_id,
    )
    transaction.set(settings_ref, document, merge=False)
    transaction.set(
        mutation_ref,
        _mutation_record(
            user_id=user_id,
            client_mutation_id=client_mutation_id,
            kind=kind,
            target_id=target_id,
            payload_hash=payload_hash,
            result_document=document,
            applied=True,
        ),
        merge=False,
    )
    return {"document": document, "applied": True}


async def upsert_candidate(
    user_id: str,
    payload: SmartMemoryCandidateUpsertRequest,
) -> SmartMemoryMutationResult:
    client = get_firestore()
    candidate_id = _require_document_id(payload.candidateId, field_name="candidateId")
    client_mutation_id = _require_client_mutation_id(payload.clientMutationId)
    _validate_bounded_evidence(
        source_refs=payload.sourceRefs,
        confidence_reason_codes=payload.confidenceReasonCodes,
    )
    _validate_source_refs_not_deleted(payload.sourceRefs)
    _validate_candidate_suppression_checks(payload.suppressionChecks)
    if not payload.subject:
        raise ValueError("Smart Memory candidate subject is required")
    mutation_payload: dict[str, Any] = {
        "kind": "candidate_upsert",
        "targetId": candidate_id,
        "candidate": payload.model_dump(exclude={"clientMutationId"}),
    }
    payload_hash = _stable_payload_hash(mutation_payload)
    try:
        return _upsert_candidate_transaction(
            client.transaction(),
            client=client,
            user_id=user_id,
            candidate_id=candidate_id,
            payload=payload,
            client_mutation_id=client_mutation_id,
            payload_hash=payload_hash,
        )
    except SmartMemoryMutationDedupeConflictError:
        raise
    except (FirebaseError, GoogleAPICallError, RetryError) as exc:
        logger.exception("Failed to upsert Smart Memory candidate.", extra={"user_id": user_id})
        raise FirestoreServiceError("Failed to upsert Smart Memory candidate.") from exc


@firestore.transactional
def _upsert_candidate_transaction(
    transaction: firestore.Transaction,
    *,
    client: firestore.Client,
    user_id: str,
    candidate_id: str,
    payload: SmartMemoryCandidateUpsertRequest,
    client_mutation_id: str,
    payload_hash: str,
) -> SmartMemoryMutationResult:
    kind = "candidate_upsert"
    mutation_ref = _mutation_ref(client, user_id, client_mutation_id)
    mutation_snapshot = mutation_ref.get(transaction=transaction)
    if mutation_snapshot.exists:
        return _result_from_existing_mutation(
            dict(mutation_snapshot.to_dict() or {}),
            client_mutation_id=client_mutation_id,
            kind=kind,
            target_id=candidate_id,
            payload_hash=payload_hash,
        )

    now = _now_iso()
    candidate_ref = _candidate_ref(client, user_id, candidate_id)
    candidate_snapshot = candidate_ref.get(transaction=transaction)
    existing = dict(candidate_snapshot.to_dict() or {}) if candidate_snapshot.exists else {}
    if existing.get("state") in SUPPRESSED_CANDIDATE_STATES:
        raise ValueError("Smart Memory candidate is suppressed")
    settings_snapshot = _settings_ref(client, user_id).get(transaction=transaction)
    settings = dict(settings_snapshot.to_dict() or {}) if settings_snapshot.exists else {}
    if settings.get("enabled") is False:
        raise ValueError("Smart Memory is disabled")
    tombstone_id = _tombstone_id(payload.memoryType, payload.subject, candidate_id)
    tombstone_snapshot = _tombstone_ref(client, user_id, tombstone_id).get(
        transaction=transaction
    )
    if tombstone_snapshot.exists:
        raise ValueError("Smart Memory candidate is suppressed by user delete")
    document: dict[str, Any] = {
        "candidateId": candidate_id,
        "ownerUserId": user_id,
        "schemaVersion": 1,
        "memoryType": payload.memoryType,
        "state": "candidate",
        "subject": payload.subject,
        "evidenceSummary": payload.evidenceSummary,
        "sourceRefs": payload.sourceRefs,
        "confidenceReasonCodes": payload.confidenceReasonCodes,
        "suppressionChecks": payload.suppressionChecks,
        "createdAt": existing.get("createdAt") or now,
        "updatedAt": now,
        "firstSeenAt": payload.firstSeenAt,
        "lastSeenAt": payload.lastSeenAt,
        "serverRevision": _next_revision(existing),
    }
    SmartMemoryCandidate.model_validate(document)
    transaction.set(candidate_ref, document, merge=False)
    transaction.set(
        mutation_ref,
        _mutation_record(
            user_id=user_id,
            client_mutation_id=client_mutation_id,
            kind=kind,
            target_id=candidate_id,
            payload_hash=payload_hash,
            result_document=document,
            applied=True,
        ),
        merge=False,
    )
    return {"document": document, "applied": True}


async def patch_item(
    user_id: str,
    memory_item_id: str,
    payload: SmartMemoryItemPatchRequest,
) -> SmartMemoryMutationResult:
    return await _mutate_item(
        user_id,
        memory_item_id,
        kind="item_patch",
        client_mutation_id=payload.clientMutationId,
        patch_payload=payload.model_dump(exclude={"clientMutationId"}, exclude_none=True),
    )


async def mute_item(
    user_id: str,
    memory_item_id: str,
    *,
    client_mutation_id: str,
) -> SmartMemoryMutationResult:
    return await _mutate_item(
        user_id,
        memory_item_id,
        kind="item_mute",
        client_mutation_id=client_mutation_id,
        patch_payload={},
    )


async def restore_item(
    user_id: str,
    memory_item_id: str,
    *,
    client_mutation_id: str,
) -> SmartMemoryMutationResult:
    return await _mutate_item(
        user_id,
        memory_item_id,
        kind="item_restore",
        client_mutation_id=client_mutation_id,
        patch_payload={},
    )


async def delete_item(
    user_id: str,
    memory_item_id: str,
    *,
    client_mutation_id: str,
) -> SmartMemoryMutationResult:
    return await _mutate_item(
        user_id,
        memory_item_id,
        kind="item_delete",
        client_mutation_id=client_mutation_id,
        patch_payload={},
    )


async def mark_source_deleted(
    user_id: str,
    memory_item_id: str,
    *,
    client_mutation_id: str,
    source_ref: dict[str, Any],
) -> SmartMemoryMutationResult:
    checked_request = SmartMemorySourceDeletedRequest.model_validate(
        {
            "clientMutationId": client_mutation_id,
            "sourceRef": source_ref,
        }
    )
    return await _mutate_item(
        user_id,
        memory_item_id,
        kind="item_source_deleted",
        client_mutation_id=checked_request.clientMutationId,
        patch_payload={"sourceRef": checked_request.sourceRef},
    )


async def mark_sources_deleted_by_source_hashes(
    user_id: str,
    source_hashes: Sequence[str],
    *,
    memory_type: SmartMemoryType = "typical_portion",
    subject_keys: Sequence[str] = (),
) -> int:
    normalized_source_hashes = {
        source_hash.strip()
        for source_hash in source_hashes
        if isinstance(source_hash, str) and source_hash.strip()
    }
    normalized_subject_keys = {
        subject_key.strip()
        for subject_key in subject_keys
        if isinstance(subject_key, str) and subject_key.strip()
    }
    if not normalized_source_hashes and not normalized_subject_keys:
        return 0

    client = get_firestore()
    user_ref = _user_ref(client, user_id)
    now = _now_iso()
    updated_count = 0
    try:
        tombstones_collection = user_ref.collection(SMART_MEMORY_TOMBSTONES_SUBCOLLECTION)
        for subject_key in sorted(normalized_subject_keys)[:MAX_CAPTURE_CONTROL_DOCS]:
            tombstone = _tombstone_document_from_subject_key(
                user_id=user_id,
                memory_type=memory_type,
                subject_key=subject_key,
                deleted_at=now,
                reason_code="source_deleted",
            )
            tombstone_ref = tombstones_collection.document(tombstone["tombstoneId"])
            if not tombstone_ref.get().exists:
                tombstone_ref.set(tombstone, merge=False)
                updated_count += 1

        if not normalized_source_hashes:
            return updated_count

        item_collection = user_ref.collection(SMART_MEMORY_SUBCOLLECTION)
        for snapshot in _stream_documents_for_source_hashes(
            item_collection,
            normalized_source_hashes,
        ):
            document = _snapshot_document(snapshot, document_id_field="memoryItemId")
            if not _document_references_source_hash(document, normalized_source_hashes):
                continue
            if document.get("state") in {"deleted_suppressed", "source_deleted"}:
                continue
            document["ownerUserId"] = user_id
            document["memoryItemId"] = snapshot.id
            document["state"] = "source_deleted"
            document["updatedAt"] = now
            document["sourceDeletedAt"] = now
            document["serverRevision"] = _next_revision(document)
            control = dict(document.get("control") or {})
            control.update(
                {
                    "lastControlKind": "source_deleted_cascade",
                    "lastControlledAt": now,
                    "suggestionsSuppressed": True,
                }
            )
            document["control"] = control
            SmartMemoryItem.model_validate(document)
            item_collection.document(snapshot.id).set(document, merge=True)
            memory_type = cast(SmartMemoryType, document.get("memoryType"))
            subject = cast(dict[str, Any], document.get("subject") or {})
            if subject:
                tombstone = _tombstone_document(
                    user_id=user_id,
                    memory_type=memory_type,
                    subject=subject,
                    fallback_id=snapshot.id,
                    deleted_at=now,
                    delete_revision=int(document["serverRevision"]),
                    reason_code="source_deleted",
                )
                user_ref.collection(SMART_MEMORY_TOMBSTONES_SUBCOLLECTION).document(
                    tombstone["tombstoneId"]
                ).set(tombstone, merge=False)
            updated_count += 1

        candidate_collection = user_ref.collection(SMART_MEMORY_CANDIDATES_SUBCOLLECTION)
        for snapshot in _stream_documents_for_source_hashes(
            candidate_collection,
            normalized_source_hashes,
        ):
            document = _snapshot_document(snapshot, document_id_field="candidateId")
            if not _document_references_source_hash(document, normalized_source_hashes):
                continue
            if document.get("state") in SUPPRESSED_CANDIDATE_STATES:
                continue
            document["ownerUserId"] = user_id
            document["candidateId"] = snapshot.id
            document["state"] = "source_deleted"
            document["updatedAt"] = now
            document["lastSeenAt"] = document.get("lastSeenAt")
            document["serverRevision"] = _next_revision(document)
            suppression_checks = dict(document.get("suppressionChecks") or {})
            suppression_checks["sourceDeleted"] = True
            document["suppressionChecks"] = suppression_checks
            SmartMemoryCandidate.model_validate(document)
            candidate_collection.document(snapshot.id).set(document, merge=True)
            memory_type = cast(SmartMemoryType, document.get("memoryType"))
            subject = cast(dict[str, Any], document.get("subject") or {})
            if subject:
                tombstone = _tombstone_document(
                    user_id=user_id,
                    memory_type=memory_type,
                    subject=subject,
                    fallback_id=snapshot.id,
                    deleted_at=now,
                    delete_revision=int(document["serverRevision"]),
                    reason_code="source_deleted",
                )
                user_ref.collection(SMART_MEMORY_TOMBSTONES_SUBCOLLECTION).document(
                    tombstone["tombstoneId"]
                ).set(tombstone, merge=False)
            updated_count += 1
    except (FirebaseError, GoogleAPICallError, RetryError) as exc:
        logger.exception(
            "Failed to mark Smart Memory sources as deleted.",
            extra={"user_id": user_id},
        )
        raise FirestoreServiceError("Failed to mark Smart Memory sources as deleted.") from exc

    return updated_count


async def _mutate_item(
    user_id: str,
    memory_item_id: str,
    *,
    kind: str,
    client_mutation_id: str,
    patch_payload: dict[str, Any],
) -> SmartMemoryMutationResult:
    client = get_firestore()
    item_id = _require_document_id(memory_item_id, field_name="memoryItemId")
    normalized_mutation_id = _require_client_mutation_id(client_mutation_id)
    mutation_payload: dict[str, Any] = {
        "kind": kind,
        "targetId": item_id,
        "patch": patch_payload,
    }
    payload_hash = _stable_payload_hash(mutation_payload)
    try:
        return _mutate_item_transaction(
            client.transaction(),
            client=client,
            user_id=user_id,
            memory_item_id=item_id,
            kind=kind,
            client_mutation_id=normalized_mutation_id,
            payload_hash=payload_hash,
            patch_payload=patch_payload,
        )
    except (SmartMemoryMutationDedupeConflictError, SmartMemoryNotFoundError):
        raise
    except (FirebaseError, GoogleAPICallError, RetryError) as exc:
        logger.exception("Failed to mutate Smart Memory item.", extra={"user_id": user_id})
        raise FirestoreServiceError("Failed to mutate Smart Memory item.") from exc


@firestore.transactional
def _mutate_item_transaction(
    transaction: firestore.Transaction,
    *,
    client: firestore.Client,
    user_id: str,
    memory_item_id: str,
    kind: str,
    client_mutation_id: str,
    payload_hash: str,
    patch_payload: dict[str, Any],
) -> SmartMemoryMutationResult:
    mutation_ref = _mutation_ref(client, user_id, client_mutation_id)
    mutation_snapshot = mutation_ref.get(transaction=transaction)
    if mutation_snapshot.exists:
        return _result_from_existing_mutation(
            dict(mutation_snapshot.to_dict() or {}),
            client_mutation_id=client_mutation_id,
            kind=kind,
            target_id=memory_item_id,
            payload_hash=payload_hash,
        )

    item_ref = _memory_item_ref(client, user_id, memory_item_id)
    item_snapshot = item_ref.get(transaction=transaction)
    if not item_snapshot.exists:
        raise SmartMemoryNotFoundError("Smart Memory item was not found")

    now = _now_iso()
    document = _snapshot_document(item_snapshot, document_id_field="memoryItemId")
    state = str(document.get("state") or "")
    if kind != "item_delete" and state in {"deleted_suppressed", "source_deleted"}:
        raise ValueError("Smart Memory item is not mutable in its current state")

    next_revision = _next_revision(document)
    document["ownerUserId"] = user_id
    document["memoryItemId"] = memory_item_id
    document["updatedAt"] = now
    document["serverRevision"] = next_revision
    control = dict(document.get("control") or {})
    control.update(
        {
            "lastClientMutationId": client_mutation_id,
            "lastControlKind": kind,
            "lastControlledAt": now,
        }
    )

    if kind == "item_patch":
        if state not in MUTABLE_ITEM_STATES:
            raise ValueError("Smart Memory item is not editable in its current state")
        if patch_payload.get("userValue") is not None:
            user_value = cast(dict[str, Any], patch_payload["userValue"])
            _validate_user_value_for_memory_type(str(document.get("memoryType") or ""), user_value)
            document["userValue"] = user_value
        if patch_payload.get("stateReason") is not None:
            document["stateReason"] = patch_payload["stateReason"]
        document["editedAt"] = now
        control["editedFields"] = patch_payload.get("editedFields") or []
    elif kind == "item_mute":
        if state not in {"active", "candidate"}:
            raise ValueError("Smart Memory item is not mutable in its current state")
        document["state"] = "muted"
        document["mutedAt"] = now
    elif kind == "item_restore":
        if state != "muted":
            raise ValueError("Only muted Smart Memory items can be restored")
        document["state"] = "active"
        document["restoredAt"] = now
    elif kind == "item_source_deleted":
        memory_type = cast(SmartMemoryType, document.get("memoryType"))
        subject = cast(dict[str, Any], document.get("subject") or {})
        document["state"] = "source_deleted"
        document["sourceDeletedAt"] = now
        source_ref = patch_payload.get("sourceRef")
        if isinstance(source_ref, dict) and source_ref:
            source_refs = list(document.get("sourceRefs") or [])
            source_refs.append(source_ref)
            document["sourceRefs"] = source_refs[-MAX_SOURCE_REFS:]
        if subject:
            tombstone = _tombstone_document(
                user_id=user_id,
                memory_type=memory_type,
                subject=subject,
                fallback_id=memory_item_id,
                deleted_at=now,
                delete_revision=next_revision,
                reason_code="source_deleted",
            )
            transaction.set(
                _tombstone_ref(client, user_id, tombstone["tombstoneId"]),
                tombstone,
                merge=False,
            )
    elif kind == "item_delete":
        memory_type = cast(SmartMemoryType, document.get("memoryType"))
        subject = cast(dict[str, Any], document.get("subject") or {})
        created_at = str(document.get("createdAt") or now)
        document["state"] = "deleted_suppressed"
        document["deletedAt"] = now
        document["stateReason"] = "user_deleted"
        document["evidenceSummary"] = {}
        document["sourceRefs"] = []
        document["userValue"] = {}
        document["subject"] = {}
        document["threshold"] = {}
        document["confidence"] = {}
        document["confidenceReasonCodes"] = []
        document["lastEvaluatedAt"] = None
        document["mutedAt"] = None
        document["editedAt"] = None
        document["restoredAt"] = None
        document["sourceDeletedAt"] = None
        document["createdAt"] = created_at
        tombstone = _tombstone_document(
            user_id=user_id,
            memory_type=memory_type,
            subject=subject,
            fallback_id=memory_item_id,
            deleted_at=now,
            delete_revision=next_revision,
            reason_code="user_deleted",
        )
        transaction.set(
            _tombstone_ref(client, user_id, tombstone["tombstoneId"]),
            tombstone,
            merge=False,
        )
    else:
        raise ValueError("Unsupported Smart Memory mutation")

    if document.get("state") in NON_SUGGESTING_STATES:
        control["suggestionsSuppressed"] = True
    document["control"] = control
    SmartMemoryItem.model_validate(document)
    transaction.set(item_ref, document, merge=True)
    transaction.set(
        mutation_ref,
        _mutation_record(
            user_id=user_id,
            client_mutation_id=client_mutation_id,
            kind=kind,
            target_id=memory_item_id,
            payload_hash=payload_hash,
            result_document=document,
            applied=True,
        ),
        merge=False,
    )
    return {"document": document, "applied": True}


def read_export(user_ref: firestore.DocumentReference) -> dict[str, list[dict[str, Any]]]:
    items = [
        item
        for item in _read_limited_subcollection(
            user_ref,
            SMART_MEMORY_SUBCOLLECTION,
        )
        if item.get("state") not in {"deleted_suppressed"}
    ]
    candidates = [
        candidate
        for candidate in _read_limited_subcollection(
            user_ref,
            SMART_MEMORY_CANDIDATES_SUBCOLLECTION,
        )
        if candidate.get("state") == "candidate"
    ]
    return {
        "items": items,
        "candidates": candidates,
        "settings": _read_limited_subcollection(
            user_ref,
            SMART_MEMORY_SETTINGS_SUBCOLLECTION,
        ),
        "tombstones": _read_limited_subcollection(
            user_ref,
            SMART_MEMORY_TOMBSTONES_SUBCOLLECTION,
        ),
        "mutationDedupe": _read_limited_subcollection(
            user_ref,
            SMART_MEMORY_MUTATION_DEDUPE_SUBCOLLECTION,
        ),
    }
