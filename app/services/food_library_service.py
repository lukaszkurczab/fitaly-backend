"""Bounded Product/Ingredient search for manual autocomplete."""

from __future__ import annotations

import logging
import re
import unicodedata
from typing import Any, cast

from firebase_admin.exceptions import FirebaseError
from google.api_core.exceptions import GoogleAPICallError, RetryError
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
    IngredientProductProfileCompatibilityStatus,
    IngredientProductRankingSignal,
    IngredientProductSearchCachePolicy,
    IngredientProductSearchQueryEcho,
    IngredientProductSearchResponse,
    IngredientProductSearchRow,
    IngredientProductSourceType,
    IngredientProductWarningReasonCode,
)

logger = logging.getLogger(__name__)

MIN_SEARCH_QUERY_LENGTH = 2
DEFAULT_SEARCH_LIMIT = 8
MAX_SEARCH_LIMIT = 12
SEARCH_INDEX_FIELD = "searchPrefixes"
CANDIDATE_ONLY_SOURCE_TYPES: set[IngredientProductSourceType] = {
    "barcode_identity",
    "runtime_ai_candidate",
}
LOW_CONFIDENCE_LEVELS: set[IngredientProductConfidenceLevel] = {"unknown", "low"}


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


def _snapshot_payload(snapshot: Any) -> dict[str, Any]:
    payload = _as_dict(snapshot.to_dict())
    payload.setdefault("ingredientProductId", str(snapshot.id))
    return payload


def _stream_prefix_matches(collection_ref: Any, normalized_query: str, limit_count: int) -> list[Any]:
    query = collection_ref.where(
        filter=FieldFilter(SEARCH_INDEX_FIELD, "array_contains", normalized_query)
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
