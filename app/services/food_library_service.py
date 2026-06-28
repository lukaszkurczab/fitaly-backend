"""Bounded Product/Ingredient search for manual autocomplete."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import logging
import re
import unicodedata
from typing import Any, NoReturn, cast

from firebase_admin.exceptions import FirebaseError
from google.api_core.exceptions import GoogleAPICallError, RetryError
from google.cloud import firestore
from google.cloud.firestore_v1.base_query import FieldFilter
from pydantic import ValidationError

from app.core.exceptions import FirestoreServiceError
from app.core.firestore_constants import (
    INGREDIENT_PRODUCTS_COLLECTION,
    INGREDIENT_PRODUCTS_SUBCOLLECTION,
    USERS_COLLECTION,
)
from app.db.firebase import get_firestore
from app.schemas.food_library import (
    IngredientProductConfidence,
    IngredientProductConfidenceLevel,
    IngredientProductCreateRequest,
    IngredientProductProfileCompatibilityStatus,
    IngredientProductPulledRecord,
    IngredientProductPullResponse,
    IngredientProductRankingSignal,
    IngredientProductRemovedRecord,
    IngredientProductSearchCachePolicy,
    IngredientProductSearchQueryEcho,
    IngredientProductSearchResponse,
    IngredientProductSearchRow,
    IngredientProductSourceType,
    IngredientProductUpdateRequest,
    IngredientProductWarningReasonCode,
)

logger = logging.getLogger(__name__)

MIN_SEARCH_QUERY_LENGTH = 2
DEFAULT_SEARCH_LIMIT = 8
MAX_SEARCH_LIMIT = 12
DEFAULT_PULL_LIMIT = 100
MAX_PULL_LIMIT = 250
SEARCH_INDEX_FIELD = "searchPrefixes"
UPDATE_MUTATION_HISTORY_FIELD = "updateMutationHistory"
UPDATE_MUTATION_HISTORY_LIMIT = 12
UPDATE_MUTATION_FINGERPRINT_VERSION = "ingredient_product_update_v1"
UPDATE_SCALAR_FIELDS = (
    "displayName",
    "kind",
    "brandName",
    "ingredientName",
    "packageName",
    "category",
)
CANDIDATE_ONLY_SOURCE_TYPES: set[IngredientProductSourceType] = {
    "barcode_identity",
    "runtime_ai_candidate",
}
LOW_CONFIDENCE_LEVELS: set[IngredientProductConfidenceLevel] = {"unknown", "low"}


class IngredientProductMutationConflictError(ValueError):
    """Raised when a user-scoped Product/Ingredient mutation conflicts."""


class IngredientProductInvalidUpdateError(ValueError):
    """Raised when an Ingredient/Product update would violate the record contract."""


class IngredientProductNotFoundError(ValueError):
    """Raised when a user-scoped Product/Ingredient record is not owned by the user."""


def _raise_malformed_pull_record(payload: dict[str, Any], reason: str) -> NoReturn:
    logger.warning(
        "Rejecting malformed Ingredient/Product pull record.",
        extra={
            "ingredient_product_id": _as_str(payload.get("ingredientProductId")),
            "record_scope": _as_str(payload.get("recordScope")),
            "owner_user_id_present": bool(_as_str(payload.get("ownerUserId"))),
            "reason": reason,
        },
    )
    raise FirestoreServiceError("Malformed Ingredient/Product pull record.")


def normalize_search_query(query: str) -> str:
    normalized = unicodedata.normalize("NFKD", query.strip().lower())
    without_marks = "".join(
        character for character in normalized if not unicodedata.combining(character)
    )
    return re.sub(r"\s+", " ", without_marks).strip()


def clamp_search_limit(limit_count: int | None) -> int:
    if limit_count is None:
        return DEFAULT_SEARCH_LIMIT
    return min(max(limit_count, 1), MAX_SEARCH_LIMIT)


def clamp_pull_limit(limit_count: int | None) -> int:
    if limit_count is None:
        return DEFAULT_PULL_LIMIT
    return min(max(limit_count, 1), MAX_PULL_LIMIT)


def _parse_pull_cursor(updated_after: str | None) -> tuple[str | None, str | None]:
    if updated_after is None:
        return None, None
    stripped = updated_after.strip()
    if not stripped:
        raise ValueError("updatedAfter must be a non-empty timestamp when provided")
    if "|" not in stripped:
        return stripped, None
    updated_at, product_id = stripped.rsplit("|", 1)
    updated_at = updated_at.strip()
    product_id = product_id.strip()
    if not updated_at or not product_id:
        raise ValueError("updatedAfter cursor is malformed")
    return updated_at, product_id


def _pull_cursor(updated_at: str, ingredient_product_id: str) -> str:
    return f"{updated_at}|{ingredient_product_id}"


def _validate_document_id(value: str) -> str:
    document_id = value.strip()
    if not document_id:
        raise ValueError("ingredientProductId must be non-empty")
    if "/" in document_id:
        raise ValueError("ingredientProductId must be a document id, not a path.")
    return document_id


def _pull_snapshot_cursor(payload: dict[str, Any]) -> tuple[str, str] | None:
    updated_at = _as_str(payload.get("updatedAt"))
    ingredient_product_id = _as_str(payload.get("ingredientProductId"))
    if not updated_at or not ingredient_product_id:
        return None
    return updated_at, ingredient_product_id


def _validate_query(query: str) -> str:
    normalized_query = normalize_search_query(query)
    if len(normalized_query) < MIN_SEARCH_QUERY_LENGTH:
        raise ValueError("Ingredient/Product search query is too short")
    return normalized_query


def _query_echo(
    *,
    normalized_query: str,
    limit_count: int,
    include_user_scoped: bool,
    include_global: bool,
    locale: str | None,
) -> IngredientProductSearchQueryEcho:
    return IngredientProductSearchQueryEcho(
        normalizedQuery=normalized_query,
        queryLength=len(normalized_query),
        limit=limit_count,
        includeUserScoped=include_user_scoped,
        includeGlobal=include_global,
        locale=locale,
    )


def build_degraded_search_response(
    *,
    query: str,
    limit_count: int | None = None,
    include_user_scoped: bool = True,
    include_global: bool = True,
    locale: str | None = None,
) -> IngredientProductSearchResponse:
    normalized_query = _validate_query(query)
    resolved_limit = clamp_search_limit(limit_count)
    return IngredientProductSearchResponse(
        items=[],
        queryEcho=_query_echo(
            normalized_query=normalized_query,
            limit_count=resolved_limit,
            include_user_scoped=include_user_scoped,
            include_global=include_global,
            locale=locale,
        ),
        cachePolicy=None,
        warnings=["backend_degraded"],
    )


def _as_dict(value: object) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(cast(dict[str, Any], value))
    return {}


def _as_str(value: object) -> str:
    if isinstance(value, str):
        return value.strip()
    return ""


def _as_string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    raw_items = cast(list[object], value)
    return [item for item in raw_items if isinstance(item, str)]


def _utc_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace(
        "+00:00",
        "Z",
    )


def _search_prefixes(*values: str | None) -> list[str]:
    prefixes: set[str] = set()
    for value in values:
        if not value:
            continue
        normalized = normalize_search_query(value)
        if len(normalized) >= MIN_SEARCH_QUERY_LENGTH:
            for end_index in range(MIN_SEARCH_QUERY_LENGTH, len(normalized) + 1):
                prefixes.add(normalized[:end_index])
        for token in normalized.split():
            if len(token) < MIN_SEARCH_QUERY_LENGTH:
                continue
            for end_index in range(MIN_SEARCH_QUERY_LENGTH, len(token) + 1):
                prefixes.add(token[:end_index])
    return sorted(prefixes)[:120]


def _snapshot_payload(snapshot: Any) -> dict[str, Any]:
    payload = _as_dict(snapshot.to_dict())
    payload.setdefault("ingredientProductId", str(snapshot.id))
    return payload


def _stream_prefix_matches(collection_ref: Any, normalized_query: str, limit_count: int) -> list[Any]:
    query = collection_ref.where(
        filter=FieldFilter(SEARCH_INDEX_FIELD, "array_contains", normalized_query)
    )
    return list(query.limit(limit_count).stream())[:limit_count]


def _stream_user_pull_records(
    collection_ref: Any,
    *,
    updated_after: str | None,
    after_product_id: str | None,
    limit_count: int,
) -> list[Any]:
    query = collection_ref
    if updated_after and not after_product_id:
        query = query.where(filter=FieldFilter("updatedAt", ">=", updated_after))
    query = query.order_by("updatedAt").order_by("ingredientProductId")
    if updated_after and after_product_id:
        query = query.start_after(
            {
                "updatedAt": updated_after,
                "ingredientProductId": after_product_id,
            }
        )
    return list(query.limit(limit_count).stream())[:limit_count]


def _profile_status(payload: dict[str, Any]) -> IngredientProductProfileCompatibilityStatus:
    explicit = payload.get("profileCompatibility")
    if isinstance(explicit, str):
        return cast(IngredientProductProfileCompatibilityStatus, explicit)
    profile_flags = _as_dict(payload.get("profileFlags"))
    status = profile_flags.get("compatibilityStatus")
    if isinstance(status, str):
        return cast(IngredientProductProfileCompatibilityStatus, status)
    return "unknown"


def _confidence(payload: dict[str, Any]) -> IngredientProductConfidence:
    confidence = _as_dict(payload.get("confidence"))
    return IngredientProductConfidence(
        identity=cast(
            IngredientProductConfidenceLevel,
            confidence.get("identity") or "unknown",
        ),
        nutrition=cast(
            IngredientProductConfidenceLevel,
            confidence.get("nutrition") or "unknown",
        ),
        profile=cast(
            IngredientProductConfidenceLevel,
            confidence.get("profile") or "unknown",
        ),
    )


def _warning_reason_codes(
    payload: dict[str, Any],
    *,
    profile_status: IngredientProductProfileCompatibilityStatus,
    confidence: IngredientProductConfidence,
) -> list[IngredientProductWarningReasonCode]:
    warnings: list[IngredientProductWarningReasonCode] = []
    existing_codes = _as_string_list(payload.get("warningReasonCodes"))
    for code in existing_codes:
        if code not in warnings:
            warnings.append(cast(IngredientProductWarningReasonCode, code))
    if profile_status == "unknown" and "profile_unknown" not in warnings:
        warnings.append("profile_unknown")
    if profile_status == "warning" and "profile_warning" not in warnings:
        warnings.append("profile_warning")
    if profile_status == "incompatible" and "profile_incompatible" not in warnings:
        warnings.append("profile_incompatible")
    if confidence.nutrition in LOW_CONFIDENCE_LEVELS and "nutrition_low_confidence" not in warnings:
        warnings.append("nutrition_low_confidence")
    if not isinstance(payload.get("nutritionPer100"), dict) and "nutrition_missing" not in warnings:
        warnings.append("nutrition_missing")
    if payload.get("lifecycleState") == "candidate" and "pending_user_record" not in warnings:
        warnings.append("pending_user_record")
    return warnings[:10]


def _ranking_signals(
    payload: dict[str, Any],
    *,
    normalized_query: str,
    profile_status: IngredientProductProfileCompatibilityStatus,
    warnings: list[IngredientProductWarningReasonCode],
) -> list[IngredientProductRankingSignal]:
    signals: list[IngredientProductRankingSignal] = []
    record_scope = payload.get("recordScope")
    normalized_names = {
        normalize_search_query(value)
        for value in (
            payload.get("displayName"),
            payload.get("ingredientName"),
            payload.get("brandName"),
        )
        if isinstance(value, str)
    }
    exact_match = normalized_query in normalized_names
    if record_scope == "user_scoped":
        signals.append("user_scoped")
        if exact_match:
            signals.append("exact_user")
    elif exact_match:
        signals.append("exact_match")
    if record_scope == "global_seed":
        signals.append("verified_seed")
    if record_scope == "global_internal":
        signals.append("verified_global")
    if profile_status in {"warning", "incompatible"}:
        signals.append("profile_warning")
    if (
        "nutrition_low_confidence" in warnings
        or "nutrition_missing" in warnings
    ):
        signals.append("nutrition_warning")
    if "pending_user_record" in warnings:
        signals.append("pending_user_record")
    return list(dict.fromkeys(signals))


def _is_eligible(
    payload: dict[str, Any],
    *,
    user_id: str,
) -> tuple[bool, IngredientProductWarningReasonCode | None]:
    record_scope = payload.get("recordScope")
    lifecycle_state = payload.get("lifecycleState")
    if lifecycle_state == "rejected":
        return False, None
    source_attribution = _as_dict(payload.get("sourceAttribution"))
    source_type = source_attribution.get("sourceType")
    if source_type in CANDIDATE_ONLY_SOURCE_TYPES:
        return False, "source_candidate_only"
    if record_scope == "user_scoped":
        if payload.get("ownerUserId") != user_id:
            return False, None
        return lifecycle_state in {"verified", "candidate"}, None
    if record_scope in {"global_seed", "global_internal"}:
        return lifecycle_state == "verified", None
    return False, None


def _to_search_row(
    payload: dict[str, Any],
    *,
    normalized_query: str,
) -> IngredientProductSearchRow:
    profile_status = _profile_status(payload)
    confidence = _confidence(payload)
    warnings = _warning_reason_codes(
        payload,
        profile_status=profile_status,
        confidence=confidence,
    )
    ranking_signals = _ranking_signals(
        payload,
        normalized_query=normalized_query,
        profile_status=profile_status,
        warnings=warnings,
    )
    profile_flags = _as_dict(payload.get("profileFlags"))
    row_payload: dict[str, Any] = {
        "ingredientProductId": payload.get("ingredientProductId"),
        "recordScope": payload.get("recordScope"),
        "lifecycleState": payload.get("lifecycleState"),
        "displayName": payload.get("displayName"),
        "kind": payload.get("kind"),
        "defaultServing": payload.get("defaultServing"),
        "nutritionPer100": payload.get("nutritionPer100"),
        "confidence": confidence.model_dump(mode="json"),
        "sourceAttribution": payload.get("sourceAttribution"),
        "profileCompatibility": {
            "status": profile_status,
            "dietaryFlags": _as_string_list(profile_flags.get("dietaryFlags")),
            "allergenFlags": _as_string_list(profile_flags.get("allergenFlags")),
        },
        "warningReasonCodes": warnings,
        "rankingSignals": ranking_signals,
        "brandName": payload.get("brandName"),
        "ingredientName": payload.get("ingredientName"),
        "packageName": payload.get("packageName"),
        "category": payload.get("category"),
        "servingSizes": payload.get("servingSizes") or [],
        "dietaryFlags": payload.get("dietaryFlags") or [],
        "allergenFlags": payload.get("allergenFlags") or [],
        "cacheState": payload.get("cacheState"),
        "ownerUserId": payload.get("ownerUserId"),
    }
    return IngredientProductSearchRow.model_validate(row_payload)


def _row_sort_key(row: IngredientProductSearchRow) -> tuple[int, str, str]:
    warning_count = len(row.warningReasonCodes)
    if "exact_user" in row.rankingSignals:
        bucket = 0
    elif warning_count == 0 and row.recordScope in {"global_seed", "global_internal"}:
        bucket = 1
    elif warning_count == 0:
        bucket = 2
    elif "profile_incompatible" in row.warningReasonCodes:
        bucket = 5
    elif "nutrition_low_confidence" in row.warningReasonCodes:
        bucket = 4
    else:
        bucket = 3
    return (bucket, normalize_search_query(row.displayName), row.ingredientProductId)


def _ensure_kind_specific_fields(payload: dict[str, Any]) -> None:
    kind = payload.get("kind")
    if kind == "generic_ingredient" and not _as_str(payload.get("ingredientName")):
        raise IngredientProductInvalidUpdateError(
            "ingredientName is required for generic Ingredient/Product records."
        )
    if kind == "branded_product" and not _as_str(payload.get("brandName")):
        raise IngredientProductInvalidUpdateError(
            "brandName is required for branded Ingredient/Product records."
        )


def _normalized_update_request_payload(
    request: IngredientProductUpdateRequest,
) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    for field_name in UPDATE_SCALAR_FIELDS:
        if field_name not in request.model_fields_set:
            continue
        value = getattr(request, field_name)
        payload[field_name] = value if value != "" else None

    if "defaultServing" in request.model_fields_set:
        payload["defaultServing"] = (
            request.defaultServing.model_dump(mode="json")
            if request.defaultServing is not None
            else None
        )
    if "nutritionPer100" in request.model_fields_set:
        payload["nutritionPer100"] = (
            request.nutritionPer100.model_dump(mode="json")
            if request.nutritionPer100 is not None
            else None
        )
    if "servingSizes" in request.model_fields_set:
        payload["servingSizes"] = [
            serving.model_dump(mode="json") for serving in (request.servingSizes or [])
        ]
    if "dietaryFlags" in request.model_fields_set:
        payload["dietaryFlags"] = request.dietaryFlags or []
    if "allergenFlags" in request.model_fields_set:
        payload["allergenFlags"] = request.allergenFlags or []
    return payload


def _update_request_fingerprint(request: IngredientProductUpdateRequest) -> str:
    fingerprint_payload: dict[str, Any] = {
        "version": UPDATE_MUTATION_FINGERPRINT_VERSION,
        "payload": _normalized_update_request_payload(request),
    }
    encoded = json.dumps(
        fingerprint_payload,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _update_request_matches_payload(
    request: IngredientProductUpdateRequest,
    payload: dict[str, Any],
) -> bool:
    for field_name, value in _normalized_update_request_payload(request).items():
        if payload.get(field_name) != value:
            return False
    return True


def _update_mutation_history(payload: dict[str, Any]) -> list[dict[str, str]]:
    raw_history = payload.get(UPDATE_MUTATION_HISTORY_FIELD)
    if not isinstance(raw_history, list):
        return []

    history: list[dict[str, str]] = []
    for raw_entry in cast(list[object], raw_history):
        if not isinstance(raw_entry, dict):
            continue
        raw_entry_payload = cast(dict[str, Any], raw_entry)
        mutation_id = _as_str(raw_entry_payload.get("clientMutationId"))
        payload_fingerprint = _as_str(raw_entry_payload.get("payloadFingerprint"))
        if not mutation_id or not payload_fingerprint:
            continue
        entry = {
            "clientMutationId": mutation_id,
            "payloadFingerprint": payload_fingerprint,
            "fingerprintVersion": (
                _as_str(raw_entry_payload.get("fingerprintVersion"))
                or UPDATE_MUTATION_FINGERPRINT_VERSION
            ),
        }
        updated_at = _as_str(raw_entry_payload.get("updatedAt"))
        if updated_at:
            entry["updatedAt"] = updated_at
        history.append(entry)
        if len(history) >= UPDATE_MUTATION_HISTORY_LIMIT:
            break
    return history


def _update_mutation_history_entry(
    history: list[dict[str, str]],
    mutation_id: str,
) -> dict[str, str] | None:
    for entry in history:
        if entry.get("clientMutationId") == mutation_id:
            return entry
    return None


def _next_update_mutation_history(
    payload: dict[str, Any],
    *,
    mutation_id: str,
    payload_fingerprint: str,
    updated_at: str,
) -> list[dict[str, str]]:
    next_history = [
        {
            "clientMutationId": mutation_id,
            "payloadFingerprint": payload_fingerprint,
            "fingerprintVersion": UPDATE_MUTATION_FINGERPRINT_VERSION,
            "updatedAt": updated_at,
        }
    ]
    for entry in _update_mutation_history(payload):
        if entry.get("clientMutationId") == mutation_id:
            continue
        next_history.append(entry)
        if len(next_history) >= UPDATE_MUTATION_HISTORY_LIMIT:
            break
    return next_history


async def search_ingredient_products(
    user_id: str,
    *,
    query: str,
    locale: str | None = None,
    limit_count: int | None = None,
    include_user_scoped: bool = True,
    include_global: bool = True,
) -> IngredientProductSearchResponse:
    normalized_query = _validate_query(query)
    resolved_limit = clamp_search_limit(limit_count)
    request_warnings: list[IngredientProductWarningReasonCode] = []
    rows_by_id: dict[str, IngredientProductSearchRow] = {}

    try:
        client = get_firestore()
        snapshots: list[Any] = []
        if include_user_scoped:
            user_collection = (
                client.collection(USERS_COLLECTION)
                .document(user_id)
                .collection(INGREDIENT_PRODUCTS_SUBCOLLECTION)
            )
            snapshots.extend(
                _stream_prefix_matches(user_collection, normalized_query, resolved_limit)
            )
        if include_global:
            global_collection = client.collection(INGREDIENT_PRODUCTS_COLLECTION)
            snapshots.extend(
                _stream_prefix_matches(global_collection, normalized_query, resolved_limit)
            )
    except (GoogleAPICallError, RetryError, FirebaseError, ValueError) as exc:
        raise FirestoreServiceError("Failed to search Ingredient/Product records.") from exc

    for snapshot in snapshots:
        payload = _snapshot_payload(snapshot)
        eligible, warning = _is_eligible(payload, user_id=user_id)
        if warning is not None and warning not in request_warnings:
            request_warnings.append(warning)
        if not eligible:
            continue
        try:
            row = _to_search_row(payload, normalized_query=normalized_query)
        except (ValidationError, ValueError):
            logger.warning(
                "Skipping malformed Ingredient/Product search record.",
                extra={"record_scope": _as_str(payload.get("recordScope"))},
            )
            continue
        existing = rows_by_id.get(row.ingredientProductId)
        if existing is None or _row_sort_key(row) < _row_sort_key(existing):
            rows_by_id[row.ingredientProductId] = row

    rows = sorted(rows_by_id.values(), key=_row_sort_key)[:resolved_limit]
    return IngredientProductSearchResponse(
        items=rows,
        queryEcho=_query_echo(
            normalized_query=normalized_query,
            limit_count=resolved_limit,
            include_user_scoped=include_user_scoped,
            include_global=include_global,
            locale=locale,
        ),
        cachePolicy=IngredientProductSearchCachePolicy(
            cacheGeneration="ingredient_product_search_v1",
            maxAgeSeconds=86400,
        ),
        warnings=request_warnings,
    )


async def pull_user_ingredient_products(
    user_id: str,
    *,
    updated_after: str | None = None,
    limit_count: int | None = None,
) -> IngredientProductPullResponse:
    resolved_limit = clamp_pull_limit(limit_count)
    cursor_updated_at, cursor_product_id = _parse_pull_cursor(updated_after)

    try:
        collection_ref = (
            get_firestore()
            .collection(USERS_COLLECTION)
            .document(user_id)
            .collection(INGREDIENT_PRODUCTS_SUBCOLLECTION)
        )
        snapshots = _stream_user_pull_records(
            collection_ref,
            updated_after=cursor_updated_at,
            after_product_id=cursor_product_id,
            limit_count=resolved_limit,
        )
    except (GoogleAPICallError, RetryError, FirebaseError, ValueError) as exc:
        raise FirestoreServiceError("Failed to pull Ingredient/Product records.") from exc

    records: list[IngredientProductPulledRecord] = []
    removed_records: list[IngredientProductRemovedRecord] = []
    last_cursor: tuple[str, str] | None = None
    for snapshot in snapshots:
        payload = _snapshot_payload(snapshot)
        snapshot_cursor = _pull_snapshot_cursor(payload)
        if snapshot_cursor is None:
            _raise_malformed_pull_record(payload, "missing_cursor")
        updated_at, ingredient_product_id = snapshot_cursor

        if (
            payload.get("recordScope") == "user_scoped"
            and payload.get("ownerUserId") == user_id
            and payload.get("lifecycleState") == "rejected"
        ):
            try:
                removed_records.append(
                    IngredientProductRemovedRecord(
                        ingredientProductId=ingredient_product_id,
                        updatedAt=updated_at,
                        removalReason="rejected",
                    )
                )
            except (ValidationError, ValueError):
                _raise_malformed_pull_record(payload, "invalid_removed_record")
            last_cursor = snapshot_cursor
            continue

        if (
            payload.get("recordScope") != "user_scoped"
            or payload.get("ownerUserId") != user_id
        ):
            _raise_malformed_pull_record(payload, "scope_owner_mismatch")

        eligible, _warning = _is_eligible(payload, user_id=user_id)
        if not eligible:
            _raise_malformed_pull_record(payload, "ineligible_user_record")
        try:
            row = _to_search_row(
                payload,
                normalized_query=normalize_search_query(
                    _as_str(payload.get("displayName")) or ingredient_product_id
                ),
            )
        except (ValidationError, ValueError):
            _raise_malformed_pull_record(payload, "invalid_search_row")
        source_attribution = _as_dict(payload.get("sourceAttribution"))
        creation_client_mutation_id = (
            _as_str(payload.get("creationClientMutationId"))
            or _as_str(source_attribution.get("sourceId"))
            or None
        )
        records.append(
            IngredientProductPulledRecord(
                item=row,
                updatedAt=updated_at,
                creationClientMutationId=creation_client_mutation_id,
            )
        )
        last_cursor = snapshot_cursor

    records = sorted(
        records,
        key=lambda record: (record.updatedAt, record.item.ingredientProductId),
    )
    removed_records = sorted(
        removed_records,
        key=lambda record: (record.updatedAt, record.ingredientProductId),
    )
    next_updated_after = (
        _pull_cursor(last_cursor[0], last_cursor[1]) if last_cursor else None
    )
    return IngredientProductPullResponse(
        records=records,
        removedRecords=removed_records,
        nextUpdatedAfter=next_updated_after,
    )


async def create_user_ingredient_product(
    user_id: str,
    request: IngredientProductCreateRequest,
) -> tuple[IngredientProductSearchRow, bool]:
    product_id = request.ingredientProductId
    now = _utc_timestamp()
    derived_ingredient_name = (
        request.ingredientName
        or (request.displayName if request.kind == "generic_ingredient" else None)
    )
    if request.kind == "branded_product" and not request.brandName:
        raise ValueError("brandName is required for branded Ingredient/Product records.")
    client = get_firestore()
    collection_ref = (
        client.collection(USERS_COLLECTION)
        .document(user_id)
        .collection(INGREDIENT_PRODUCTS_SUBCOLLECTION)
    )
    document_ref = collection_ref.document(product_id)

    nutrition_confidence: IngredientProductConfidenceLevel = (
        "low" if request.nutritionPer100 is not None else "unknown"
    )
    payload: dict[str, Any] = {
        "ingredientProductId": product_id,
        "recordScope": "user_scoped",
        "lifecycleState": "candidate",
        "ownerUserId": user_id,
        "kind": request.kind,
        "displayName": request.displayName,
        "defaultServing": request.defaultServing.model_dump(mode="json"),
        "nutritionPer100": (
            request.nutritionPer100.model_dump(mode="json")
            if request.nutritionPer100 is not None
            else None
        ),
        "confidence": {
            "identity": "low",
            "nutrition": nutrition_confidence,
            "profile": "unknown",
        },
        "sourceAttribution": {
            "sourceType": "user_created",
            "sourceId": request.clientMutationId,
            "sourceName": "manual_entry",
            "observedAt": now,
        },
        "profileFlags": {
            "compatibilityStatus": "unknown",
            "dietaryFlags": request.dietaryFlags,
            "allergenFlags": request.allergenFlags,
        },
        "servingSizes": [
            serving.model_dump(mode="json") for serving in request.servingSizes
        ],
        "dietaryFlags": request.dietaryFlags,
        "allergenFlags": request.allergenFlags,
        "searchPrefixes": _search_prefixes(
            request.displayName,
            request.ingredientName,
            request.brandName,
            request.packageName,
            request.category,
        ),
        "creationClientMutationId": request.clientMutationId,
        "createdAt": now,
        "updatedAt": now,
    }
    for field_name in ("brandName", "packageName", "category"):
        value = getattr(request, field_name)
        if value:
            payload[field_name] = value
    if derived_ingredient_name:
        payload["ingredientName"] = derived_ingredient_name

    try:
        return _create_user_ingredient_product_transaction(
            client.transaction(),
            document_ref=document_ref,
            payload=payload,
            normalized_query=normalize_search_query(request.displayName),
            client_mutation_id=request.clientMutationId,
        )
    except IngredientProductMutationConflictError:
        raise
    except (GoogleAPICallError, RetryError, FirebaseError, ValueError) as exc:
        raise FirestoreServiceError("Failed to create Ingredient/Product record.") from exc


@firestore.transactional
def _create_user_ingredient_product_transaction(
    transaction: firestore.Transaction,
    *,
    document_ref: firestore.DocumentReference,
    payload: dict[str, Any],
    normalized_query: str,
    client_mutation_id: str,
) -> tuple[IngredientProductSearchRow, bool]:
    existing_snapshot = document_ref.get(transaction=transaction)
    if existing_snapshot.exists:
        existing_payload = _snapshot_payload(existing_snapshot)
        if existing_payload.get("creationClientMutationId") == client_mutation_id:
            return _to_search_row(
                existing_payload,
                normalized_query=normalized_query,
            ), False
        raise IngredientProductMutationConflictError(
            "Ingredient/Product record already exists for a different mutation."
        )

    transaction.set(document_ref, payload, merge=False)
    return _to_search_row(payload, normalized_query=normalized_query), True


@firestore.transactional
def _update_user_ingredient_product_transaction(
    transaction: firestore.Transaction,
    *,
    document_ref: firestore.DocumentReference,
    user_id: str,
    product_id: str,
    request: IngredientProductUpdateRequest,
    mutation_id: str,
    request_fingerprint: str,
) -> tuple[IngredientProductSearchRow, bool]:
    existing_snapshot = document_ref.get(transaction=transaction)
    if not existing_snapshot.exists:
        raise IngredientProductNotFoundError("Ingredient/Product record was not found.")

    existing_payload = _snapshot_payload(existing_snapshot)
    if (
        existing_payload.get("recordScope") != "user_scoped"
        or existing_payload.get("ownerUserId") != user_id
        or existing_payload.get("lifecycleState") == "rejected"
    ):
        raise IngredientProductNotFoundError("Ingredient/Product record was not found.")

    history = _update_mutation_history(existing_payload)
    history_entry = _update_mutation_history_entry(history, mutation_id)
    if history_entry is not None:
        if history_entry.get("payloadFingerprint") != request_fingerprint:
            raise IngredientProductMutationConflictError(
                "Ingredient/Product update mutation id was reused with a different payload."
            )
        return _to_search_row(
            existing_payload,
            normalized_query=normalize_search_query(
                _as_str(existing_payload.get("displayName")) or product_id
            ),
        ), False

    if existing_payload.get("updateClientMutationId") == mutation_id:
        if not _update_request_matches_payload(request, existing_payload):
            raise IngredientProductMutationConflictError(
                "Ingredient/Product update mutation id was reused with a different payload."
            )
        return _to_search_row(
            existing_payload,
            normalized_query=normalize_search_query(
                _as_str(existing_payload.get("displayName")) or product_id
            ),
        ), False

    now = _utc_timestamp()
    merged_payload = dict(existing_payload)
    normalized_update_payload = _normalized_update_request_payload(request)
    update_payload: dict[str, Any] = {
        "ingredientProductId": product_id,
        "recordScope": "user_scoped",
        "ownerUserId": user_id,
        "updateClientMutationId": mutation_id,
        "updatedAt": now,
        UPDATE_MUTATION_HISTORY_FIELD: _next_update_mutation_history(
            existing_payload,
            mutation_id=mutation_id,
            payload_fingerprint=request_fingerprint,
            updated_at=now,
        ),
    }
    update_payload.update(normalized_update_payload)

    if "nutritionPer100" in request.model_fields_set:
        confidence = _as_dict(merged_payload.get("confidence"))
        confidence["nutrition"] = (
            "low" if request.nutritionPer100 is not None else "unknown"
        )
        update_payload["confidence"] = confidence
    profile_flags = _as_dict(merged_payload.get("profileFlags"))
    if "dietaryFlags" in request.model_fields_set:
        profile_flags["dietaryFlags"] = update_payload["dietaryFlags"]
    if "allergenFlags" in request.model_fields_set:
        profile_flags["allergenFlags"] = update_payload["allergenFlags"]
    if (
        "dietaryFlags" in request.model_fields_set
        or "allergenFlags" in request.model_fields_set
    ):
        profile_flags.setdefault("compatibilityStatus", "unknown")
        update_payload["profileFlags"] = profile_flags

    merged_payload.update(update_payload)
    update_payload["searchPrefixes"] = _search_prefixes(
        _as_str(merged_payload.get("displayName")),
        _as_str(merged_payload.get("ingredientName")),
        _as_str(merged_payload.get("brandName")),
        _as_str(merged_payload.get("packageName")),
        _as_str(merged_payload.get("category")),
    )
    merged_payload["searchPrefixes"] = update_payload["searchPrefixes"]
    _ensure_kind_specific_fields(merged_payload)
    try:
        row = _to_search_row(
            merged_payload,
            normalized_query=normalize_search_query(
                _as_str(merged_payload.get("displayName")) or product_id
            ),
        )
    except (ValidationError, ValueError) as exc:
        raise FirestoreServiceError(
            "Malformed Ingredient/Product record after update."
        ) from exc

    transaction.set(document_ref, update_payload, merge=True)
    return row, True


async def update_user_ingredient_product(
    user_id: str,
    *,
    ingredient_product_id: str,
    request: IngredientProductUpdateRequest,
) -> tuple[IngredientProductSearchRow, bool]:
    product_id = _validate_document_id(ingredient_product_id)
    mutation_id = request.clientMutationId.strip()
    if not mutation_id:
        raise ValueError("clientMutationId must be non-empty")

    request_fingerprint = _update_request_fingerprint(request)
    client = get_firestore()
    document_ref = (
        client
        .collection(USERS_COLLECTION)
        .document(user_id)
        .collection(INGREDIENT_PRODUCTS_SUBCOLLECTION)
        .document(product_id)
    )

    try:
        return _update_user_ingredient_product_transaction(
            client.transaction(),
            document_ref=document_ref,
            user_id=user_id,
            product_id=product_id,
            request=request,
            mutation_id=mutation_id,
            request_fingerprint=request_fingerprint,
        )
    except IngredientProductMutationConflictError:
        raise
    except IngredientProductInvalidUpdateError:
        raise
    except IngredientProductNotFoundError:
        raise
    except (GoogleAPICallError, RetryError, FirebaseError, ValueError) as exc:
        raise FirestoreServiceError("Failed to update Ingredient/Product record.") from exc


async def delete_user_ingredient_product(
    user_id: str,
    *,
    ingredient_product_id: str,
    client_mutation_id: str,
) -> tuple[str, str, bool]:
    product_id = _validate_document_id(ingredient_product_id)
    mutation_id = client_mutation_id.strip()
    if not mutation_id:
        raise ValueError("clientMutationId must be non-empty")

    now = _utc_timestamp()
    document_ref = (
        get_firestore()
        .collection(USERS_COLLECTION)
        .document(user_id)
        .collection(INGREDIENT_PRODUCTS_SUBCOLLECTION)
        .document(product_id)
    )

    try:
        existing_snapshot = document_ref.get()
        if not existing_snapshot.exists:
            raise IngredientProductNotFoundError("Ingredient/Product record was not found.")
        payload = _snapshot_payload(existing_snapshot)
        if (
            payload.get("recordScope") != "user_scoped"
            or payload.get("ownerUserId") != user_id
        ):
            raise IngredientProductNotFoundError("Ingredient/Product record was not found.")

        existing_updated_at = _as_str(payload.get("updatedAt"))
        if payload.get("lifecycleState") == "rejected" and existing_updated_at:
            return product_id, existing_updated_at, False

        update_payload = {
            "ingredientProductId": product_id,
            "recordScope": "user_scoped",
            "ownerUserId": user_id,
            "lifecycleState": "rejected",
            "deletionClientMutationId": mutation_id,
            "rejectedAt": now,
            "rejectionReason": "user_deleted",
            "updatedAt": now,
        }
        document_ref.set(update_payload, merge=True)
        return product_id, now, True
    except IngredientProductNotFoundError:
        raise
    except (GoogleAPICallError, RetryError, FirebaseError, ValueError) as exc:
        raise FirestoreServiceError("Failed to delete Ingredient/Product record.") from exc
