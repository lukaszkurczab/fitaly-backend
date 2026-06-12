"""Backend-owned storage and uploads for saved meals."""

from datetime import datetime, timezone
import logging
from typing import Any, cast
from uuid import uuid4
from fastapi import UploadFile
from firebase_admin.exceptions import FirebaseError
from google.api_core.exceptions import GoogleAPICallError, RetryError
from google.cloud import firestore

from app.core.coercion import coerce_optional_str
from app.core.exceptions import FirestoreServiceError
from app.core.firestore_constants import MEAL_TEMPLATES_SUBCOLLECTION, USERS_COLLECTION
from app.db.firebase import get_firestore
from app.services import meal_storage
from app.services.meal_service import (
    MealMutationDedupeConflictError,
    MealMutationResult,
    _meal_mutation_ref,
    _normalize_image_ref,
    _normalize_ingredients,
    _normalize_totals,
    _require_client_mutation_id,
    _stable_payload_hash,
    coerce_iso8601,
)

logger = logging.getLogger(__name__)
UTC = timezone.utc
_MEAL_TYPE_HINTS = {"breakfast", "lunch", "dinner", "snack", "other"}
_LOGGED_MEAL_ONLY_TEMPLATE_FIELDS = frozenset(
    {
        "id",
        "mealId",
        "cloudId",
        "loggedAt",
        "timestamp",
        "dayKey",
        "loggedAtLocalMin",
        "tzOffsetMin",
        "type",
        "name",
        "ingredients",
        "syncState",
        "source",
        "inputMethod",
        "aiMeta",
        "notes",
        "tags",
        "totals",
        "userUid",
        "imageId",
        "photoUrl",
        "savedMealRefId",
    }
)
_REQUIRED_STORED_TEMPLATE_FIELDS = frozenset(
    {
        "templateId",
        "ownerUserId",
        "templateVersion",
        "displayName",
        "description",
        "mealTypeHint",
        "draftItems",
        "draftTotals",
        "nutritionSnapshot",
        "imageRef",
        "createdAt",
        "updatedAt",
        "deleted",
    }
)


class StoredMealTemplateDocumentError(FirestoreServiceError):
    """Raised when a stored meal template document violates the canonical shape."""


def _my_meals_collection(user_id: str) -> firestore.CollectionReference:
    client: firestore.Client = get_firestore()
    return client.collection(USERS_COLLECTION).document(user_id).collection(
        MEAL_TEMPLATES_SUBCOLLECTION
    )


def _my_meal_ref(user_id: str, meal_id: str) -> firestore.DocumentReference:
    return _my_meals_collection(user_id).document(meal_id)


def _my_meal_ref_for_client(
    client: firestore.Client,
    user_id: str,
    meal_id: str,
) -> firestore.DocumentReference:
    return client.collection(USERS_COLLECTION).document(user_id).collection(
        MEAL_TEMPLATES_SUBCOLLECTION
    ).document(meal_id)


async def list_changes(
    user_id: str,
    *,
    limit_count: int = 100,
    after_cursor: str | None = None,
) -> tuple[list[dict[str, Any]], str | None]:
    items, next_cursor = await meal_storage.list_changes_paginated(
        _my_meals_collection(user_id),
        user_id,
        _normalize_saved_meal_snapshot,
        limit_count=limit_count,
        after_cursor=after_cursor,
        require_document_id_cursor=True,
        error_message="Failed to list saved meal changes.",
    )
    if next_cursor is None and len(items) == limit_count and items:
        last_item = items[-1]
        template_id = coerce_optional_str(last_item.get("templateId"))
        updated_at = coerce_optional_str(last_item.get("updatedAt"))
        if template_id and updated_at:
            next_cursor = meal_storage.build_cursor(updated_at, template_id)
    return items, next_cursor


def _as_object_map(value: object) -> dict[str, object] | None:
    if not isinstance(value, dict):
        return None
    raw_map = cast(dict[object, object], value)
    result: dict[str, object] = {}
    for key, item in raw_map.items():
        if isinstance(key, str):
            result[key] = item
    return result


def _now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _stored_template_error(template_id: str, reason: str) -> StoredMealTemplateDocumentError:
    return StoredMealTemplateDocumentError(
        f"Invalid meal template document {template_id}: {reason}"
    )


def _require_canonical_stored_template(
    user_id: str,
    template_id: str,
    payload: dict[str, Any],
) -> None:
    logged_meal_fields = sorted(_LOGGED_MEAL_ONLY_TEMPLATE_FIELDS.intersection(payload))
    if logged_meal_fields:
        raise _stored_template_error(
            template_id,
            f"contains logged-meal-only fields: {', '.join(logged_meal_fields)}",
        )

    extra_fields = sorted(set(payload).difference(_REQUIRED_STORED_TEMPLATE_FIELDS))
    if extra_fields:
        raise _stored_template_error(
            template_id,
            f"contains non-canonical fields: {', '.join(extra_fields)}",
        )

    missing_fields = sorted(_REQUIRED_STORED_TEMPLATE_FIELDS.difference(payload))
    if missing_fields:
        raise _stored_template_error(
            template_id,
            f"missing canonical fields: {', '.join(missing_fields)}",
        )

    stored_template_id = coerce_optional_str(payload.get("templateId"))
    if stored_template_id != template_id:
        raise _stored_template_error(template_id, "templateId does not match document id")

    owner_user_id = coerce_optional_str(payload.get("ownerUserId"))
    if owner_user_id != user_id:
        raise _stored_template_error(template_id, "ownerUserId does not match user")

    template_version = payload.get("templateVersion")
    if not isinstance(template_version, int) or isinstance(template_version, bool):
        raise _stored_template_error(template_id, "templateVersion must be an integer")
    if template_version < 1:
        raise _stored_template_error(template_id, "templateVersion must be at least 1")

    for field_name in ("displayName", "description"):
        value = payload.get(field_name)
        if value is not None and not isinstance(value, str):
            raise _stored_template_error(template_id, f"{field_name} must be a string or null")

    meal_type_hint = coerce_optional_str(payload.get("mealTypeHint"))
    if meal_type_hint not in _MEAL_TYPE_HINTS:
        raise _stored_template_error(template_id, "mealTypeHint is invalid")

    if not isinstance(payload.get("draftItems"), list):
        raise _stored_template_error(template_id, "draftItems must be a list")
    if not isinstance(payload.get("draftTotals"), dict):
        raise _stored_template_error(template_id, "draftTotals must be an object")
    if not isinstance(payload.get("nutritionSnapshot"), dict):
        raise _stored_template_error(template_id, "nutritionSnapshot must be an object")

    image_ref = payload.get("imageRef")
    if image_ref is not None and not isinstance(image_ref, dict):
        raise _stored_template_error(template_id, "imageRef must be an object or null")

    if not coerce_optional_str(payload.get("createdAt")):
        raise _stored_template_error(template_id, "createdAt must be present")
    if not coerce_optional_str(payload.get("updatedAt")):
        raise _stored_template_error(template_id, "updatedAt must be present")

    if type(payload.get("deleted")) is not bool:
        raise _stored_template_error(template_id, "deleted must be a boolean")


def _meal_template_from_document(
    user_id: str,
    template_id: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    _require_canonical_stored_template(user_id, template_id, payload)

    image_ref_map = _as_object_map(payload.get("imageRef"))
    draft_items = payload.get("draftItems")
    if not isinstance(draft_items, list):
        draft_items = []
    draft_totals = _as_object_map(payload.get("draftTotals")) or {}
    nutrition_snapshot = _as_object_map(payload.get("nutritionSnapshot")) or draft_totals
    meal_type_hint = coerce_optional_str(payload.get("mealTypeHint")) or "other"
    if meal_type_hint not in _MEAL_TYPE_HINTS:
        meal_type_hint = "other"
    template_version_raw = payload.get("templateVersion")
    template_version = template_version_raw if isinstance(template_version_raw, int) else 1
    if template_version < 1:
        template_version = 1
    updated_at = coerce_optional_str(payload.get("updatedAt")) or _now_iso()
    created_at = coerce_optional_str(payload.get("createdAt")) or updated_at

    return {
        "templateId": template_id,
        "ownerUserId": coerce_optional_str(payload.get("ownerUserId")) or user_id,
        "templateVersion": template_version,
        "displayName": coerce_optional_str(payload.get("displayName")),
        "description": coerce_optional_str(payload.get("description")),
        "mealTypeHint": meal_type_hint,
        "draftItems": draft_items,
        "draftTotals": draft_totals,
        "nutritionSnapshot": nutrition_snapshot,
        "imageRef": image_ref_map,
        "createdAt": created_at,
        "updatedAt": updated_at,
        "deleted": bool(payload.get("deleted")),
    }


def _meal_template_mutation_record(
    *,
    user_id: str,
    client_mutation_id: str,
    kind: str,
    template_id: str,
    payload_hash: str,
    result_template: dict[str, Any],
    applied: bool,
) -> dict[str, Any]:
    return {
        "userId": user_id,
        "clientMutationId": client_mutation_id,
        "kind": kind,
        "templateId": template_id,
        "payloadHash": payload_hash,
        "resultTemplate": result_template,
        "applied": applied,
        "createdAt": _now_iso(),
    }


def _result_from_existing_template_mutation(
    data: dict[str, Any],
    *,
    client_mutation_id: str,
    kind: str,
    template_id: str,
    payload_hash: str,
) -> MealMutationResult:
    if (
        data.get("clientMutationId") != client_mutation_id
        or data.get("kind") != kind
        or data.get("templateId") != template_id
        or data.get("payloadHash") != payload_hash
    ):
        raise MealMutationDedupeConflictError(
            "clientMutationId was already used for a different meal template mutation"
        )

    result_template = _as_object_map(data.get("resultTemplate"))
    if result_template is None:
        raise MealMutationDedupeConflictError("clientMutationId record is incomplete")
    return {
        "meal": dict(result_template),
        "applied": False,
        "reference_day_key": None,
    }


def _normalize_saved_meal_document(
    user_id: str,
    payload: dict[str, Any],
    *,
    fallback_cloud_id: str | None = None,
    fallback_updated_at: str | None = None,
) -> tuple[str, dict[str, Any]]:
    now_iso = _now_iso()
    template_id = coerce_optional_str(payload.get("templateId")) or fallback_cloud_id
    if not template_id:
        raise ValueError("Missing templateId")

    draft_items = _normalize_ingredients(payload.get("draftItems"))
    updated_at = coerce_iso8601(payload.get("updatedAt"), fallback=fallback_updated_at or now_iso)
    created_at = coerce_iso8601(payload.get("createdAt"), fallback=updated_at)
    draft_totals = _normalize_totals(payload.get("draftTotals"), draft_items)
    nutrition_snapshot = _normalize_totals(payload.get("nutritionSnapshot"), draft_items)
    meal_type_hint = coerce_optional_str(payload.get("mealTypeHint")) or "other"
    if meal_type_hint not in _MEAL_TYPE_HINTS:
        meal_type_hint = "other"
    template_version_raw = payload.get("templateVersion")
    template_version = template_version_raw if isinstance(template_version_raw, int) else 1
    if template_version < 1:
        template_version = 1

    return template_id, {
        "templateId": template_id,
        "ownerUserId": user_id,
        "templateVersion": template_version,
        "displayName": coerce_optional_str(payload.get("displayName")),
        "description": coerce_optional_str(payload.get("description")),
        "mealTypeHint": meal_type_hint,
        "draftItems": draft_items,
        "draftTotals": draft_totals,
        "nutritionSnapshot": nutrition_snapshot,
        "imageRef": _normalize_image_ref(
            user_id,
            template_id,
            payload,
            storage_collection="mealTemplates",
            derive_storage_path=False,
        ),
        "createdAt": created_at,
        "updatedAt": updated_at,
        "deleted": bool(payload.get("deleted")),
    }


def _normalize_saved_meal_snapshot(
    user_id: str,
    snapshot: firestore.DocumentSnapshot,
) -> dict[str, Any]:
    data: dict[str, Any] = dict(snapshot.to_dict() or {})
    return _meal_template_from_document(user_id, snapshot.id, data)


def _template_upsert_payload_hash(
    *,
    payload: dict[str, Any],
    template_id: str,
    normalized_document: dict[str, Any],
) -> str:
    hash_document = dict(normalized_document)
    if coerce_optional_str(payload.get("createdAt")) is None:
        hash_document.pop("createdAt", None)
    if coerce_optional_str(payload.get("updatedAt")) is None:
        hash_document.pop("updatedAt", None)
    return _stable_payload_hash(
        {
            "kind": "meal_template_upsert",
            "templateId": template_id,
            "document": hash_document,
        }
    )


async def upsert_saved_meal(user_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    client_mutation_id = _require_client_mutation_id(payload.get("clientMutationId"))
    normalized_id, normalized_document = _normalize_saved_meal_document(user_id, payload)
    payload_hash = _template_upsert_payload_hash(
        payload=payload,
        template_id=normalized_id,
        normalized_document=normalized_document,
    )
    client: firestore.Client = get_firestore()
    meal_ref = _my_meal_ref_for_client(client, user_id, normalized_id)
    mutation_ref = _meal_mutation_ref(client, user_id, client_mutation_id)

    try:
        result = _upsert_saved_meal_mutation_transaction(
            client.transaction(),
            mutation_ref=mutation_ref,
            meal_ref=meal_ref,
            user_id=user_id,
            meal_id=normalized_id,
            client_mutation_id=client_mutation_id,
            payload_hash=payload_hash,
            normalized_document=normalized_document,
        )
    except (FirebaseError, GoogleAPICallError, RetryError) as exc:
        logger.exception(
            "Failed to upsert saved meal.",
            extra={"user_id": user_id, "meal_id": normalized_id},
        )
        raise FirestoreServiceError("Failed to upsert saved meal.") from exc

    return result["meal"]


@firestore.transactional
def _upsert_saved_meal_mutation_transaction(
    transaction: firestore.Transaction,
    *,
    mutation_ref: firestore.DocumentReference,
    meal_ref: firestore.DocumentReference,
    user_id: str,
    meal_id: str,
    client_mutation_id: str,
    payload_hash: str,
    normalized_document: dict[str, Any],
) -> MealMutationResult:
    mutation_snapshot = mutation_ref.get(transaction=transaction)
    if mutation_snapshot.exists:
        return _result_from_existing_template_mutation(
            dict(mutation_snapshot.to_dict() or {}),
            client_mutation_id=client_mutation_id,
            kind="meal_template_upsert",
            template_id=meal_id,
            payload_hash=payload_hash,
        )

    snapshot = meal_ref.get(transaction=transaction)
    if snapshot.exists:
        existing = _normalize_saved_meal_snapshot(user_id, snapshot)
        if existing["updatedAt"] > normalized_document["updatedAt"]:
            transaction.set(
                mutation_ref,
                _meal_template_mutation_record(
                    user_id=user_id,
                    client_mutation_id=client_mutation_id,
                    kind="meal_template_upsert",
                    template_id=meal_id,
                    payload_hash=payload_hash,
                    result_template=existing,
                    applied=False,
                ),
                merge=False,
            )
            return {
                "meal": existing,
                "applied": False,
                "reference_day_key": None,
            }

    result_template = _meal_template_from_document(user_id, meal_id, normalized_document)
    transaction.set(meal_ref, normalized_document, merge=False)
    transaction.set(
        mutation_ref,
        _meal_template_mutation_record(
            user_id=user_id,
            client_mutation_id=client_mutation_id,
            kind="meal_template_upsert",
            template_id=meal_id,
            payload_hash=payload_hash,
            result_template=result_template,
            applied=True,
        ),
        merge=False,
    )
    return {
        "meal": result_template,
        "applied": True,
        "reference_day_key": None,
    }


async def mark_deleted(
    user_id: str,
    meal_id: str,
    *,
    updated_at: str,
    client_mutation_id: str,
) -> dict[str, Any]:
    normalized_client_mutation_id = _require_client_mutation_id(client_mutation_id)
    normalized_updated_at = coerce_iso8601(updated_at)
    payload_hash = _stable_payload_hash(
        {
            "kind": "meal_template_delete",
            "templateId": meal_id,
            "updatedAt": normalized_updated_at,
        }
    )
    client: firestore.Client = get_firestore()
    meal_ref = _my_meal_ref_for_client(client, user_id, meal_id)
    mutation_ref = _meal_mutation_ref(client, user_id, normalized_client_mutation_id)

    try:
        result = _delete_saved_meal_mutation_transaction(
            client.transaction(),
            mutation_ref=mutation_ref,
            meal_ref=meal_ref,
            user_id=user_id,
            meal_id=meal_id,
            client_mutation_id=normalized_client_mutation_id,
            payload_hash=payload_hash,
            normalized_updated_at=normalized_updated_at,
        )
    except (FirebaseError, GoogleAPICallError, RetryError) as exc:
        logger.exception(
            "Failed to delete saved meal.",
            extra={"user_id": user_id, "meal_id": meal_id},
        )
        raise FirestoreServiceError("Failed to delete saved meal.") from exc

    return result["meal"]


@firestore.transactional
def _delete_saved_meal_mutation_transaction(
    transaction: firestore.Transaction,
    *,
    mutation_ref: firestore.DocumentReference,
    meal_ref: firestore.DocumentReference,
    user_id: str,
    meal_id: str,
    client_mutation_id: str,
    payload_hash: str,
    normalized_updated_at: str,
) -> MealMutationResult:
    mutation_snapshot = mutation_ref.get(transaction=transaction)
    if mutation_snapshot.exists:
        return _result_from_existing_template_mutation(
            dict(mutation_snapshot.to_dict() or {}),
            client_mutation_id=client_mutation_id,
            kind="meal_template_delete",
            template_id=meal_id,
            payload_hash=payload_hash,
        )

    snapshot = meal_ref.get(transaction=transaction)
    existing: dict[str, Any] = dict(snapshot.to_dict() or {}) if snapshot.exists else {}
    normalized_id, normalized_document = _normalize_saved_meal_document(
        user_id,
        {
            **existing,
            "templateId": meal_id,
            "templateVersion": existing.get("templateVersion") or 1,
            "mealTypeHint": existing.get("mealTypeHint") or "other",
            "createdAt": existing.get("createdAt") or normalized_updated_at,
            "updatedAt": normalized_updated_at,
            "deleted": True,
        },
        fallback_cloud_id=meal_id,
        fallback_updated_at=normalized_updated_at,
    )
    if snapshot.exists:
        existing_normalized = _normalize_saved_meal_snapshot(user_id, snapshot)
        if existing_normalized["updatedAt"] > normalized_document["updatedAt"]:
            transaction.set(
                mutation_ref,
                _meal_template_mutation_record(
                    user_id=user_id,
                    client_mutation_id=client_mutation_id,
                    kind="meal_template_delete",
                    template_id=meal_id,
                    payload_hash=payload_hash,
                    result_template=existing_normalized,
                    applied=False,
                ),
                merge=False,
            )
            return {
                "meal": existing_normalized,
                "applied": False,
                "reference_day_key": None,
            }

    result_template = _meal_template_from_document(user_id, normalized_id, normalized_document)
    transaction.set(meal_ref, normalized_document, merge=False)
    transaction.set(
        mutation_ref,
        _meal_template_mutation_record(
            user_id=user_id,
            client_mutation_id=client_mutation_id,
            kind="meal_template_delete",
            template_id=meal_id,
            payload_hash=payload_hash,
            result_template=result_template,
            applied=True,
        ),
        merge=False,
    )
    return {
        "meal": result_template,
        "applied": True,
        "reference_day_key": None,
    }


async def upload_photo(
    user_id: str,
    meal_id: str,
    upload: UploadFile,
) -> dict[str, str]:
    extension = "jpg"
    if upload.filename and "." in upload.filename:
        maybe_extension = upload.filename.rsplit(".", 1)[-1].strip().lower()
        if maybe_extension:
            extension = maybe_extension

    payload = await meal_storage.upload_photo_to_storage(
        user_id,
        upload,
        object_path=f"mealTemplates/{user_id}/{meal_id}-{uuid4()}.{extension}",
        error_message="Failed to upload saved meal photo.",
    )
    return {
        "templateId": meal_id,
        "imageId": payload["imageId"],
        "storagePath": payload["storagePath"],
        "photoUrl": payload["photoUrl"],
    }
