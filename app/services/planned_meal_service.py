from __future__ import annotations

from collections.abc import Iterable
from datetime import date, datetime, timedelta, timezone
import hashlib
import json
from typing import Any, TypedDict, cast

from firebase_admin.exceptions import FirebaseError
from google.api_core.exceptions import GoogleAPICallError, RetryError
from google.cloud import firestore
from google.cloud.firestore_v1.base_query import FieldFilter

from app.core.exceptions import FirestoreServiceError
from app.core.firestore_constants import (
    PLANNED_MEAL_MUTATION_DEDUPE_SUBCOLLECTION,
    PLANNED_MEALS_SUBCOLLECTION,
    USERS_COLLECTION,
)
from app.db.firebase import get_firestore
from app.schemas.planned_meals import (
    PlannedMealCreateRequest,
    PlannedMealDeleteRequest,
    PlannedMealItem,
    PlannedMealMutationResponse,
    PlannedMealsListQueryEcho,
    PlannedMealsListResponse,
    PlannedMealStatus,
    PlannedMealUpdateRequest,
)


class PlannedMealNotFoundError(ValueError):
    """Raised when a planned meal does not exist."""


class PlannedMealVersionConflictError(ValueError):
    """Raised when a planned meal mutation targets a stale version."""


class PlannedMealMutationDedupeConflictError(ValueError):
    """Raised when a clientMutationId is reused for a different mutation."""


class PlannedMealMutationResult(TypedDict):
    item: PlannedMealItem
    applied: bool


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace(
        "+00:00",
        "Z",
    )


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
    if "/" in normalized:
        raise ValueError("Invalid clientMutationId")
    if len(normalized) > 128:
        raise ValueError("clientMutationId is too long")
    return normalized


def _parse_date_bucket(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError("Invalid dateBucket") from exc


def _user_ref(client: firestore.Client, user_id: str) -> firestore.DocumentReference:
    return client.collection(USERS_COLLECTION).document(user_id)


def _planned_meal_ref(
    client: firestore.Client,
    user_id: str,
    planned_meal_id: str,
) -> firestore.DocumentReference:
    return _user_ref(client, user_id).collection(
        PLANNED_MEALS_SUBCOLLECTION
    ).document(planned_meal_id)


def _mutation_ref(
    client: firestore.Client,
    user_id: str,
    client_mutation_id: str,
) -> firestore.DocumentReference:
    return _user_ref(client, user_id).collection(
        PLANNED_MEAL_MUTATION_DEDUPE_SUBCOLLECTION
    ).document(client_mutation_id)


def _snapshot_document(snapshot: Any, *, document_id_field: str) -> dict[str, Any]:
    payload = dict(snapshot.to_dict() or {})
    payload.setdefault(document_id_field, snapshot.id)
    return payload


def _response_item_from_document(data: dict[str, Any]) -> PlannedMealItem:
    payload = dict(data)
    payload.pop("ownerUserId", None)
    return PlannedMealItem.model_validate(payload)


def _mutation_record(
    *,
    user_id: str,
    client_mutation_id: str,
    kind: str,
    target_id: str,
    payload_hash: str,
    result_document: dict[str, Any],
) -> dict[str, Any]:
    return {
        "ownerUserId": user_id,
        "clientMutationId": client_mutation_id,
        "kind": kind,
        "targetId": target_id,
        "payloadHash": payload_hash,
        "resultDocument": result_document,
        "createdAt": _now_iso(),
    }


def _result_from_existing_mutation(
    data: dict[str, Any],
    *,
    client_mutation_id: str,
    kind: str,
    target_id: str,
    payload_hash: str,
) -> PlannedMealMutationResult:
    if (
        data.get("clientMutationId") != client_mutation_id
        or data.get("kind") != kind
        or data.get("targetId") != target_id
        or data.get("payloadHash") != payload_hash
    ):
        raise PlannedMealMutationDedupeConflictError(
            "clientMutationId was already used for a different planned meal mutation"
        )

    result_document = data.get("resultDocument")
    if not isinstance(result_document, dict):
        raise PlannedMealMutationDedupeConflictError(
            "clientMutationId record is incomplete"
        )
    return {
        "item": _response_item_from_document(cast(dict[str, Any], result_document)),
        "applied": False,
    }


def _existing_mutation_result(
    client: firestore.Client,
    *,
    user_id: str,
    client_mutation_id: str,
    kind: str,
    target_id: str,
    payload_hash: str,
) -> PlannedMealMutationResult | None:
    snapshot = _mutation_ref(client, user_id, client_mutation_id).get()
    if not snapshot.exists:
        return None
    return _result_from_existing_mutation(
        _snapshot_document(snapshot, document_id_field="id"),
        client_mutation_id=client_mutation_id,
        kind=kind,
        target_id=target_id,
        payload_hash=payload_hash,
    )


def _create_document(
    user_id: str,
    request: PlannedMealCreateRequest,
    *,
    now_iso: str,
) -> dict[str, Any]:
    _parse_date_bucket(request.dateBucket)
    item = PlannedMealItem(
        plannedMealId=request.plannedMealId,
        version=1,
        dateBucket=request.dateBucket,
        timeBucket=request.timeBucket,
        sourceType=request.sourceType,
        sourceRef=request.sourceRef,
        draftSnapshot=request.draftSnapshot,
        nutritionEstimate=request.nutritionEstimate,
        status="planned",
        createdAt=now_iso,
        updatedAt=now_iso,
    )
    document = item.model_dump(mode="json")
    document["ownerUserId"] = user_id
    return document


def _update_status(
    existing: dict[str, Any],
    request: PlannedMealUpdateRequest,
) -> PlannedMealStatus:
    if request.status is not None:
        return request.status
    if {"dateBucket", "timeBucket"} & request.model_fields_set:
        return "rescheduled"
    if (
        {"sourceType", "sourceRef", "draftSnapshot", "nutritionEstimate"}
        & request.model_fields_set
    ):
        return "edited"
    return cast(PlannedMealStatus, existing.get("status") or "planned")


def _updated_document(
    existing: dict[str, Any],
    request: PlannedMealUpdateRequest,
    *,
    now_iso: str,
) -> dict[str, Any]:
    current = _response_item_from_document(existing)
    if current.status == "deleted":
        raise PlannedMealNotFoundError("Planned meal was deleted")
    if current.version != request.expectedVersion:
        raise PlannedMealVersionConflictError("Planned meal version conflict")
    if request.dateBucket is not None:
        _parse_date_bucket(request.dateBucket)

    next_payload = current.model_dump(mode="json")
    for field in (
        "dateBucket",
        "timeBucket",
        "sourceType",
        "sourceRef",
        "draftSnapshot",
        "nutritionEstimate",
    ):
        if field not in request.model_fields_set:
            continue
        value = getattr(request, field)
        if hasattr(value, "model_dump"):
            next_payload[field] = value.model_dump(mode="json")
        else:
            next_payload[field] = value
    next_payload["status"] = _update_status(existing, request)
    next_payload["version"] = current.version + 1
    next_payload["updatedAt"] = now_iso
    next_payload["createdAt"] = current.createdAt
    next_payload["ownerUserId"] = existing.get("ownerUserId")
    return next_payload


def _deleted_document(
    existing: dict[str, Any],
    request: PlannedMealDeleteRequest,
    *,
    now_iso: str,
) -> dict[str, Any]:
    current = _response_item_from_document(existing)
    if current.version != request.expectedVersion:
        raise PlannedMealVersionConflictError("Planned meal version conflict")
    next_payload = current.model_dump(mode="json")
    next_payload["status"] = "deleted"
    next_payload["version"] = current.version + 1
    next_payload["updatedAt"] = now_iso
    next_payload["ownerUserId"] = existing.get("ownerUserId")
    return next_payload


@firestore.transactional
def _create_planned_meal_transaction(
    transaction: firestore.Transaction,
    *,
    client: firestore.Client,
    user_id: str,
    request: PlannedMealCreateRequest,
    client_mutation_id: str,
    payload_hash: str,
) -> PlannedMealMutationResult:
    planned_meal_id = request.plannedMealId
    planned_ref = _planned_meal_ref(client, user_id, planned_meal_id)
    mutation_ref = _mutation_ref(client, user_id, client_mutation_id)
    mutation_snapshot = mutation_ref.get(transaction=transaction)
    if mutation_snapshot.exists:
        return _result_from_existing_mutation(
            _snapshot_document(mutation_snapshot, document_id_field="id"),
            client_mutation_id=client_mutation_id,
            kind="planned_meal_create",
            target_id=planned_meal_id,
            payload_hash=payload_hash,
        )

    planned_snapshot = planned_ref.get(transaction=transaction)
    if planned_snapshot.exists:
        raise PlannedMealVersionConflictError("Planned meal already exists")

    document = _create_document(user_id, request, now_iso=_now_iso())
    transaction.set(planned_ref, document, merge=False)
    transaction.set(
        mutation_ref,
        _mutation_record(
            user_id=user_id,
            client_mutation_id=client_mutation_id,
            kind="planned_meal_create",
            target_id=planned_meal_id,
            payload_hash=payload_hash,
            result_document=document,
        ),
        merge=False,
    )
    return {"item": _response_item_from_document(document), "applied": True}


@firestore.transactional
def _update_planned_meal_transaction(
    transaction: firestore.Transaction,
    *,
    client: firestore.Client,
    user_id: str,
    planned_meal_id: str,
    request: PlannedMealUpdateRequest,
    client_mutation_id: str,
    payload_hash: str,
) -> PlannedMealMutationResult:
    planned_ref = _planned_meal_ref(client, user_id, planned_meal_id)
    mutation_ref = _mutation_ref(client, user_id, client_mutation_id)
    mutation_snapshot = mutation_ref.get(transaction=transaction)
    if mutation_snapshot.exists:
        return _result_from_existing_mutation(
            _snapshot_document(mutation_snapshot, document_id_field="id"),
            client_mutation_id=client_mutation_id,
            kind="planned_meal_update",
            target_id=planned_meal_id,
            payload_hash=payload_hash,
        )

    planned_snapshot = planned_ref.get(transaction=transaction)
    if not planned_snapshot.exists:
        raise PlannedMealNotFoundError("Planned meal was not found")

    document = _updated_document(
        _snapshot_document(planned_snapshot, document_id_field="plannedMealId"),
        request,
        now_iso=_now_iso(),
    )
    transaction.set(planned_ref, document, merge=False)
    transaction.set(
        mutation_ref,
        _mutation_record(
            user_id=user_id,
            client_mutation_id=client_mutation_id,
            kind="planned_meal_update",
            target_id=planned_meal_id,
            payload_hash=payload_hash,
            result_document=document,
        ),
        merge=False,
    )
    return {"item": _response_item_from_document(document), "applied": True}


@firestore.transactional
def _delete_planned_meal_transaction(
    transaction: firestore.Transaction,
    *,
    client: firestore.Client,
    user_id: str,
    planned_meal_id: str,
    request: PlannedMealDeleteRequest,
    client_mutation_id: str,
    payload_hash: str,
) -> PlannedMealMutationResult:
    planned_ref = _planned_meal_ref(client, user_id, planned_meal_id)
    mutation_ref = _mutation_ref(client, user_id, client_mutation_id)
    mutation_snapshot = mutation_ref.get(transaction=transaction)
    if mutation_snapshot.exists:
        return _result_from_existing_mutation(
            _snapshot_document(mutation_snapshot, document_id_field="id"),
            client_mutation_id=client_mutation_id,
            kind="planned_meal_delete",
            target_id=planned_meal_id,
            payload_hash=payload_hash,
        )

    planned_snapshot = planned_ref.get(transaction=transaction)
    if not planned_snapshot.exists:
        raise PlannedMealNotFoundError("Planned meal was not found")

    document = _deleted_document(
        _snapshot_document(planned_snapshot, document_id_field="plannedMealId"),
        request,
        now_iso=_now_iso(),
    )
    transaction.set(planned_ref, document, merge=False)
    transaction.set(
        mutation_ref,
        _mutation_record(
            user_id=user_id,
            client_mutation_id=client_mutation_id,
            kind="planned_meal_delete",
            target_id=planned_meal_id,
            payload_hash=payload_hash,
            result_document=document,
        ),
        merge=False,
    )
    return {"item": _response_item_from_document(document), "applied": True}


def _payload_hash(kind: str, target_id: str, request: Any) -> str:
    return _stable_payload_hash(
        {
            "kind": kind,
            "targetId": target_id,
            "request": request.model_dump(mode="json"),
        }
    )


async def create_planned_meal_for_user(
    user_id: str,
    request: PlannedMealCreateRequest,
) -> PlannedMealMutationResult:
    planned_meal_id = _require_document_id(
        request.plannedMealId,
        field_name="plannedMealId",
    )
    client_mutation_id = _require_client_mutation_id(request.clientMutationId)
    payload_hash = _payload_hash("planned_meal_create", planned_meal_id, request)

    try:
        client: firestore.Client = get_firestore()
        existing_result = _existing_mutation_result(
            client,
            user_id=user_id,
            client_mutation_id=client_mutation_id,
            kind="planned_meal_create",
            target_id=planned_meal_id,
            payload_hash=payload_hash,
        )
        if existing_result is not None:
            return existing_result
        return _create_planned_meal_transaction(
            client.transaction(),
            client=client,
            user_id=user_id,
            request=request,
            client_mutation_id=client_mutation_id,
            payload_hash=payload_hash,
        )
    except (
        PlannedMealMutationDedupeConflictError,
        PlannedMealVersionConflictError,
        ValueError,
    ):
        raise
    except (FirebaseError, GoogleAPICallError, RetryError) as exc:
        raise FirestoreServiceError("Failed to create planned meal.") from exc


def _active_window_items(
    documents: Iterable[dict[str, Any]],
    *,
    start_date: date,
    days: int,
    include_deleted: bool,
) -> list[PlannedMealItem]:
    end_date = start_date + timedelta(days=days)
    items: list[PlannedMealItem] = []
    for document in documents:
        item = _response_item_from_document(document)
        item_date = _parse_date_bucket(item.dateBucket)
        if item_date < start_date or item_date >= end_date:
            continue
        if item.status == "deleted" and not include_deleted:
            continue
        items.append(item)

    bucket_order = {"breakfast": 0, "lunch": 1, "dinner": 2, "snack": 3, "any": 4}
    items.sort(
        key=lambda item: (
            item.dateBucket,
            bucket_order.get(item.timeBucket or "any", 4),
            item.updatedAt,
            item.plannedMealId,
        )
    )
    return items


async def list_planned_meals_for_user(
    user_id: str,
    *,
    start_date: str | None = None,
    days: int = 3,
    include_deleted: bool = False,
) -> PlannedMealsListResponse:
    if days < 1 or days > 3:
        raise ValueError("Planned meal query days must be between 1 and 3")
    start = _parse_date_bucket(start_date) if start_date else date.today()

    try:
        client: firestore.Client = get_firestore()
        collection = _user_ref(client, user_id).collection(PLANNED_MEALS_SUBCOLLECTION)
        end = start + timedelta(days=days)
        query = (
            collection.where(filter=FieldFilter("dateBucket", ">=", start.isoformat()))
            .where(filter=FieldFilter("dateBucket", "<", end.isoformat()))
            .order_by("dateBucket")
        )
        snapshots = query.stream()
        documents = [
            _snapshot_document(snapshot, document_id_field="plannedMealId")
            for snapshot in snapshots
        ]
        items = _active_window_items(
            documents,
            start_date=start,
            days=days,
            include_deleted=include_deleted,
        )
        return PlannedMealsListResponse(
            items=items,
            queryEcho=PlannedMealsListQueryEcho(
                startDate=start.isoformat(),
                days=days,
                includeDeleted=include_deleted,
                returnedItems=len(items),
            ),
        )
    except ValueError:
        raise
    except (FirebaseError, GoogleAPICallError, RetryError) as exc:
        raise FirestoreServiceError("Failed to list planned meals.") from exc


async def update_planned_meal_for_user(
    user_id: str,
    planned_meal_id: str,
    request: PlannedMealUpdateRequest,
) -> PlannedMealMutationResult:
    normalized_id = _require_document_id(planned_meal_id, field_name="plannedMealId")
    client_mutation_id = _require_client_mutation_id(request.clientMutationId)
    payload_hash = _payload_hash("planned_meal_update", normalized_id, request)

    try:
        client: firestore.Client = get_firestore()
        existing_result = _existing_mutation_result(
            client,
            user_id=user_id,
            client_mutation_id=client_mutation_id,
            kind="planned_meal_update",
            target_id=normalized_id,
            payload_hash=payload_hash,
        )
        if existing_result is not None:
            return existing_result
        return _update_planned_meal_transaction(
            client.transaction(),
            client=client,
            user_id=user_id,
            planned_meal_id=normalized_id,
            request=request,
            client_mutation_id=client_mutation_id,
            payload_hash=payload_hash,
        )
    except (
        PlannedMealMutationDedupeConflictError,
        PlannedMealNotFoundError,
        PlannedMealVersionConflictError,
        ValueError,
    ):
        raise
    except (FirebaseError, GoogleAPICallError, RetryError) as exc:
        raise FirestoreServiceError("Failed to update planned meal.") from exc


async def delete_planned_meal_for_user(
    user_id: str,
    planned_meal_id: str,
    request: PlannedMealDeleteRequest,
) -> PlannedMealMutationResult:
    normalized_id = _require_document_id(planned_meal_id, field_name="plannedMealId")
    client_mutation_id = _require_client_mutation_id(request.clientMutationId)
    payload_hash = _payload_hash("planned_meal_delete", normalized_id, request)

    try:
        client: firestore.Client = get_firestore()
        existing_result = _existing_mutation_result(
            client,
            user_id=user_id,
            client_mutation_id=client_mutation_id,
            kind="planned_meal_delete",
            target_id=normalized_id,
            payload_hash=payload_hash,
        )
        if existing_result is not None:
            return existing_result
        return _delete_planned_meal_transaction(
            client.transaction(),
            client=client,
            user_id=user_id,
            planned_meal_id=normalized_id,
            request=request,
            client_mutation_id=client_mutation_id,
            payload_hash=payload_hash,
        )
    except (
        PlannedMealMutationDedupeConflictError,
        PlannedMealNotFoundError,
        PlannedMealVersionConflictError,
        ValueError,
    ):
        raise
    except (FirebaseError, GoogleAPICallError, RetryError) as exc:
        raise FirestoreServiceError("Failed to delete planned meal.") from exc


def response_from_result(result: PlannedMealMutationResult) -> PlannedMealMutationResponse:
    return PlannedMealMutationResponse(item=result["item"], updated=result["applied"])


def read_export(user_ref: firestore.DocumentReference) -> dict[str, list[dict[str, Any]]]:
    return {
        "items": [
            _snapshot_document(snapshot, document_id_field="plannedMealId")
            for snapshot in user_ref.collection(PLANNED_MEALS_SUBCOLLECTION).stream()
        ],
        "mutationDedupe": [
            _snapshot_document(snapshot, document_id_field="id")
            for snapshot in user_ref.collection(
                PLANNED_MEAL_MUTATION_DEDUPE_SUBCOLLECTION
            ).stream()
        ],
    }
