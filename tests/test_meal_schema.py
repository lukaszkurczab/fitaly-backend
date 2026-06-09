from typing import Any, cast

import pytest
from pydantic import ValidationError

from app.schemas.meal import (
    MealAiMeta,
    MealItem,
    MealTotals,
    MealUpsertRequest,
    SavedMealDeleteRequest,
    SavedMealUpsertRequest,
)
from app.services.meal_service import normalize_meal_document_payload

_FORBIDDEN_PERSISTED_KEYS = {
    "rawPrompt",
    "rawResponse",
    "providerMessages",
    "fullPayload",
    "rawImage",
    "rawToolOutput",
    "profile",
    "history",
    "chat",
    "logs",
    "debug",
    "userId",
    "userUid",
}

_FORBIDDEN_PERSISTED_SENTINELS = (
    "secret-provider-prompt",
    "secret-provider-response",
    "secret-raw-image",
    "secret-full-payload",
    "secret-history",
    "secret-chat",
    "secret-debug-log",
    "secret-user-id",
)


def _assert_no_forbidden_persisted_payload(value: Any, *, path: str = "$") -> None:
    if isinstance(value, dict):
        payload = cast(dict[object, Any], value)
        for raw_key, item in payload.items():
            key = str(raw_key)
            assert key not in _FORBIDDEN_PERSISTED_KEYS, f"{path}.{key}"
            _assert_no_forbidden_persisted_payload(item, path=f"{path}.{key}")
        return

    if isinstance(value, list):
        items = cast(list[Any], value)
        for index, item in enumerate(items):
            _assert_no_forbidden_persisted_payload(item, path=f"{path}[{index}]")
        return

    if isinstance(value, str):
        for sentinel in _FORBIDDEN_PERSISTED_SENTINELS:
            assert sentinel not in value, f"{path} contains {sentinel}"


# ---------------------------------------------------------------------------
# inputMethod & aiMeta (existing)
# ---------------------------------------------------------------------------


def test_meal_upsert_request_accepts_input_method_and_ai_meta() -> None:
    payload = MealUpsertRequest.model_validate(
        {
            "mealId": "meal-1",
            "clientMutationId": "mutation-schema-ai-meta",
            "timestamp": "2026-03-18T12:00:00.000Z",
            "type": "lunch",
            "ingredients": [],
            "inputMethod": "photo",
            "loggedAtLocalMin": 720,
            "tzOffsetMin": 60,
            "aiMeta": {
                "model": "gpt-4o-mini",
                "runId": "run-1",
                "confidence": 0.92,
            },
        }
    )

    assert payload.inputMethod == "photo"
    assert payload.loggedAtLocalMin == 720
    assert payload.tzOffsetMin == 60
    assert payload.aiMeta is not None
    assert payload.aiMeta.model == "gpt-4o-mini"
    assert payload.aiMeta.runId == "run-1"
    assert payload.aiMeta.confidence == 0.92
    assert payload.aiMeta.warnings == []


def test_meal_upsert_request_is_backward_compatible_without_new_fields() -> None:
    payload = MealUpsertRequest.model_validate(
        {
            "mealId": "meal-1",
            "clientMutationId": "mutation-schema-basic",
            "timestamp": "2026-03-18T12:00:00.000Z",
            "type": "lunch",
            "ingredients": [],
        }
    )

    assert payload.inputMethod is None
    assert payload.aiMeta is None


def test_saved_meal_upsert_request_requires_non_empty_client_mutation_id() -> None:
    base: dict[str, Any] = {
        "mealId": "saved-1",
        "timestamp": "2026-03-18T12:00:00.000Z",
        "type": "lunch",
        "ingredients": [],
    }

    with pytest.raises(ValidationError):
        SavedMealUpsertRequest.model_validate(base)

    with pytest.raises(ValidationError):
        SavedMealUpsertRequest.model_validate({**base, "clientMutationId": "   "})

    payload = SavedMealUpsertRequest.model_validate(
        {**base, "clientMutationId": " mutation-saved-schema "}
    )
    assert payload.clientMutationId == "mutation-saved-schema"


def test_saved_meal_delete_request_requires_non_empty_client_mutation_id() -> None:
    base: dict[str, Any] = {"updatedAt": "2026-03-18T12:05:00.000Z"}

    with pytest.raises(ValidationError):
        SavedMealDeleteRequest.model_validate(base)

    with pytest.raises(ValidationError):
        SavedMealDeleteRequest.model_validate({**base, "clientMutationId": "   "})

    payload = SavedMealDeleteRequest.model_validate(
        {**base, "clientMutationId": " mutation-saved-delete-schema "}
    )
    assert payload.clientMutationId == "mutation-saved-delete-schema"


def test_meal_item_serializes_input_method_and_ai_meta() -> None:
    item = MealItem.model_validate(
        {
            "userUid": "user-1",
            "mealId": "meal-1",
            "timestamp": "2026-03-18T12:00:00.000Z",
            "type": "lunch",
            "ingredients": [],
            "createdAt": "2026-03-18T12:00:00.000Z",
            "updatedAt": "2026-03-18T12:05:00.000Z",
            "cloudId": "meal-1",
            "inputMethod": "text",
            "loggedAtLocalMin": 735,
            "tzOffsetMin": 60,
            "aiMeta": {
                "model": "gpt-4o-mini",
                "runId": None,
                "confidence": 0.71,
                "warnings": ["partial_totals"],
            },
        }
    )

    assert item.model_dump()["inputMethod"] == "text"
    assert item.model_dump()["loggedAtLocalMin"] == 735
    assert item.model_dump()["tzOffsetMin"] == 60
    assert item.model_dump()["aiMeta"] == {
        "model": "gpt-4o-mini",
        "runId": None,
        "confidence": 0.71,
        "warnings": ["partial_totals"],
    }


def test_meal_upsert_request_rejects_invalid_input_method() -> None:
    with pytest.raises(ValidationError):
        MealUpsertRequest.model_validate(
            {
                "mealId": "meal-1",
                "clientMutationId": "mutation-schema-invalid-input",
                "timestamp": "2026-03-18T12:00:00.000Z",
                "type": "lunch",
                "ingredients": [],
                "inputMethod": "voice",
            }
        )


# ---------------------------------------------------------------------------
# syncState parity — mobile defines "synced" | "pending" | "conflict" | "failed"
# ---------------------------------------------------------------------------


def test_meal_item_accepts_all_sync_states() -> None:
    """Backend schema must accept every syncState that mobile can produce."""
    base: dict[str, Any] = {
        "userUid": "user-1",
        "mealId": "meal-1",
        "timestamp": "2026-03-18T12:00:00.000Z",
        "type": "lunch",
        "ingredients": [],
        "createdAt": "2026-03-18T12:00:00.000Z",
        "updatedAt": "2026-03-18T12:00:00.000Z",
        "cloudId": "meal-1",
    }
    for state in ("synced", "pending", "conflict", "failed"):
        item = MealItem.model_validate({**base, "syncState": state})
        assert item.syncState == state


def test_meal_upsert_request_accepts_all_sync_states() -> None:
    """Request model must accept every syncState that mobile can send."""
    base: dict[str, Any] = {
        "mealId": "meal-1",
        "clientMutationId": "mutation-schema-sync-state",
        "timestamp": "2026-03-18T12:00:00.000Z",
        "type": "lunch",
        "ingredients": [],
    }
    for state in ("synced", "pending", "conflict", "failed"):
        req = MealUpsertRequest.model_validate({**base, "syncState": state})
        assert req.syncState == state


def test_meal_item_rejects_unknown_sync_state() -> None:
    with pytest.raises(ValidationError):
        MealItem.model_validate(
            {
                "userUid": "user-1",
                "mealId": "meal-1",
                "clientMutationId": "mutation-schema-invalid-sync",
                "timestamp": "2026-03-18T12:00:00.000Z",
                "type": "lunch",
                "ingredients": [],
                "createdAt": "2026-03-18T12:00:00.000Z",
                "updatedAt": "2026-03-18T12:00:00.000Z",
                "cloudId": "meal-1",
                "syncState": "broken",
            }
        )


def test_meal_upsert_request_rejects_unknown_sync_state() -> None:
    with pytest.raises(ValidationError):
        MealUpsertRequest.model_validate(
            {
                "mealId": "meal-1",
                "timestamp": "2026-03-18T12:00:00.000Z",
                "type": "lunch",
                "ingredients": [],
                "syncState": "broken",
            }
        )


# ---------------------------------------------------------------------------
# Full boundary contract — request parse with complete payload
# ---------------------------------------------------------------------------

_FULL_MEAL_PAYLOAD: dict[str, Any] = {
    "mealId": "meal-full-1",
    "timestamp": "2026-03-18T12:00:00.000Z",
    "dayKey": "2026-03-18",
    "loggedAtLocalMin": 720,
    "tzOffsetMin": 60,
    "type": "lunch",
    "name": "Grilled chicken salad",
    "ingredients": [
        {
            "id": "ing-1",
            "name": "Chicken breast",
            "amount": 200.0,
            "unit": "g",
            "kcal": 330.0,
            "protein": 62.0,
            "fat": 7.2,
            "carbs": 0.0,
        }
    ],
    "createdAt": "2026-03-18T12:00:00.000Z",
    "updatedAt": "2026-03-18T12:05:00.000Z",
    "syncState": "synced",
    "source": "ai",
    "inputMethod": "photo",
    "aiMeta": {
        "model": "gpt-4o",
        "runId": "run-abc",
        "confidence": 0.88,
        "warnings": ["partial_totals"],
    },
    "imageId": "img-001",
    "photoUrl": "https://storage.example.com/photo.jpg",
    "notes": "Post-workout meal",
    "tags": ["high-protein", "lunch"],
    "deleted": False,
    "cloudId": "meal-full-1",
    "totals": {"kcal": 330.0, "protein": 62.0, "fat": 7.2, "carbs": 0.0},
}


def test_full_meal_request_parses_all_boundary_fields() -> None:
    """Complete meal payload with all fields round-trips through request model."""
    req = MealUpsertRequest.model_validate(
        {**_FULL_MEAL_PAYLOAD, "clientMutationId": "mutation-schema-full"}
    )

    assert req.mealId == "meal-full-1"
    assert req.dayKey == "2026-03-18"
    assert req.loggedAtLocalMin == 720
    assert req.tzOffsetMin == 60
    assert req.type == "lunch"
    assert req.name == "Grilled chicken salad"
    assert len(req.ingredients) == 1
    assert req.ingredients[0].id == "ing-1"
    assert req.ingredients[0].protein == 62.0
    assert req.syncState == "synced"
    assert req.source == "ai"
    assert req.inputMethod == "photo"
    assert req.aiMeta is not None
    assert req.aiMeta.model == "gpt-4o"
    assert req.aiMeta.confidence == 0.88
    assert req.aiMeta.warnings == ["partial_totals"]
    assert req.imageId == "img-001"
    assert req.notes == "Post-workout meal"
    assert req.tags == ["high-protein", "lunch"]
    assert req.deleted is False
    assert req.totals is not None
    assert req.totals.kcal == 330.0


def test_full_meal_response_serializes_all_boundary_fields() -> None:
    """Complete MealItem round-trips through response model and serialization."""
    item = MealItem.model_validate(
        {**_FULL_MEAL_PAYLOAD, "userUid": "user-1"}
    )
    data = item.model_dump()

    assert data["userUid"] == "user-1"
    assert data["mealId"] == "meal-full-1"
    assert data["dayKey"] == "2026-03-18"
    assert data["loggedAtLocalMin"] == 720
    assert data["tzOffsetMin"] == 60
    assert data["type"] == "lunch"
    assert data["source"] == "ai"
    assert data["inputMethod"] == "photo"
    assert data["aiMeta"]["model"] == "gpt-4o"
    assert data["aiMeta"]["confidence"] == 0.88
    assert data["syncState"] == "synced"
    assert data["totals"]["kcal"] == 330.0
    assert data["totals"]["protein"] == 62.0
    assert data["notes"] == "Post-workout meal"
    assert data["tags"] == ["high-protein", "lunch"]
    assert data["deleted"] is False


def test_meal_document_normalization_drops_raw_ai_provider_payloads() -> None:
    """Persisted meal docs keep visible meal fields and structured AI metadata only."""
    payload: dict[str, Any] = {
        **_FULL_MEAL_PAYLOAD,
        "id": "meal-boundary-1",
        "mealId": "meal-boundary-1",
        "cloudId": "meal-boundary-1",
        "source": "saved",
        "inputMethod": "photo",
        "imageRef": {
            "imageId": "image-boundary-1",
            "storagePath": "meals/user-boundary/image-boundary-1.jpg",
            "downloadUrl": "https://cdn.example.invalid/visible-meal.jpg",
            "rawImage": "secret-raw-image",
            "debug": {"logs": ["secret-debug-log"]},
        },
        "aiMeta": {
            "model": "gpt-4o-mini",
            "runId": "run-boundary-1",
            "confidence": 0.86,
            "warnings": ["estimated_portion"],
            "rawPrompt": "secret-provider-prompt",
            "rawResponse": {"text": "secret-provider-response"},
            "providerMessages": [
                {"role": "developer", "content": "secret-provider-prompt"}
            ],
            "fullPayload": "secret-full-payload",
            "rawToolOutput": "secret-history",
            "debug": {"logs": ["secret-debug-log"]},
        },
        "ingredients": [
            {
                "id": "ing-boundary-1",
                "name": "Visible ingredient",
                "amount": 150,
                "unit": "g",
                "kcal": 240,
                "protein": 30,
                "fat": 8,
                "carbs": 12,
                "rawResponse": "secret-provider-response",
                "profile": {"userId": "secret-user-id"},
                "logs": ["secret-debug-log"],
            }
        ],
        "totals": {
            "kcal": 240,
            "protein": 30,
            "fat": 8,
            "carbs": 12,
            "fullPayload": "secret-full-payload",
        },
        "rawPrompt": "secret-provider-prompt",
        "rawResponse": "secret-provider-response",
        "providerMessages": [{"role": "assistant", "content": "secret-provider-response"}],
        "fullPayload": {"raw": "secret-full-payload"},
        "rawImage": "secret-raw-image",
        "rawToolOutput": "secret-history",
        "profile": {"userId": "secret-user-id"},
        "history": ["secret-history"],
        "chat": ["secret-chat"],
        "logs": ["secret-debug-log"],
        "debug": {"trace": "secret-debug-log"},
        "userUid": "secret-user-id",
    }

    meal_id, document = normalize_meal_document_payload("user-boundary", payload)

    _assert_no_forbidden_persisted_payload(document)
    assert meal_id == "meal-boundary-1"
    assert set(document) == {
        "loggedAt",
        "dayKey",
        "loggedAtLocalMin",
        "tzOffsetMin",
        "type",
        "name",
        "ingredients",
        "createdAt",
        "updatedAt",
        "source",
        "inputMethod",
        "aiMeta",
        "imageRef",
        "notes",
        "tags",
        "deleted",
        "totals",
    }
    assert document["loggedAt"] == "2026-03-18T12:00:00.000Z"
    assert document["dayKey"] == "2026-03-18"
    assert document["type"] == "lunch"
    assert document["name"] == "Grilled chicken salad"
    assert document["source"] == "saved"
    assert document["inputMethod"] == "photo"
    assert document["notes"] == "Post-workout meal"
    assert document["tags"] == ["high-protein", "lunch"]
    assert document["imageRef"] == {
        "imageId": "image-boundary-1",
        "storagePath": "meals/user-boundary/image-boundary-1.jpg",
        "downloadUrl": "https://cdn.example.invalid/visible-meal.jpg",
    }
    assert document["ingredients"] == [
        {
            "id": "ing-boundary-1",
            "name": "Visible ingredient",
            "amount": 150.0,
            "unit": "g",
            "kcal": 240.0,
            "protein": 30.0,
            "fat": 8.0,
            "carbs": 12.0,
        }
    ]
    assert document["totals"] == {
        "protein": 30.0,
        "fat": 8.0,
        "carbs": 12.0,
        "kcal": 240.0,
    }
    assert document["aiMeta"] == {
        "model": "gpt-4o-mini",
        "runId": "run-boundary-1",
        "confidence": 0.86,
        "warnings": ["estimated_portion"],
    }


# ---------------------------------------------------------------------------
# Backward compatibility — old payload without Foundation Sprint fields
# ---------------------------------------------------------------------------


def test_legacy_payload_without_foundation_fields_still_works() -> None:
    """Pre-Foundation-Sprint payload (no inputMethod, aiMeta, dayKey) must parse."""
    legacy = MealUpsertRequest.model_validate(
            {
                "mealId": "legacy-1",
                "clientMutationId": "mutation-schema-legacy",
                "timestamp": "2025-12-01T08:00:00.000Z",
            "type": "breakfast",
            "ingredients": [
                {"id": "i1", "name": "Oats", "amount": 100, "kcal": 389, "protein": 16.9, "fat": 6.9, "carbs": 66.3},
            ],
        }
    )

    assert legacy.inputMethod is None
    assert legacy.aiMeta is None
    assert legacy.dayKey is None
    assert legacy.source is None
    assert legacy.syncState is None
    assert legacy.totals is None
    assert legacy.cloudId is None


def test_legacy_meal_item_defaults_are_safe() -> None:
    """MealItem with only required fields has safe defaults for all optional fields."""
    item = MealItem.model_validate(
        {
            "userUid": "user-1",
            "mealId": "legacy-1",
            "timestamp": "2025-12-01T08:00:00.000Z",
            "type": "breakfast",
            "ingredients": [],
            "createdAt": "2025-12-01T08:00:00.000Z",
            "updatedAt": "2025-12-01T08:00:00.000Z",
            "cloudId": "legacy-1",
        }
    )

    assert item.inputMethod is None
    assert item.aiMeta is None
    assert item.dayKey is None
    assert item.source is None
    assert item.syncState == "synced"
    assert item.notes is None
    assert item.imageId is None
    assert item.photoUrl is None
    assert item.tags == []
    assert item.deleted is False
    assert item.totals.kcal == 0
    assert item.totals.protein == 0


# ---------------------------------------------------------------------------
# Individual field validation
# ---------------------------------------------------------------------------


def test_meal_type_rejects_unknown_value() -> None:
    with pytest.raises(ValidationError):
        MealUpsertRequest.model_validate(
            {
                "mealId": "m1",
                "clientMutationId": "mutation-schema-invalid-source",
                "timestamp": "2026-03-18T12:00:00.000Z",
                "type": "brunch",
                "ingredients": [],
            }
        )


def test_meal_source_accepts_valid_values() -> None:
    for source in ("ai", "manual", "saved", None):
        req = MealUpsertRequest.model_validate(
            {
                "mealId": "m1",
                "clientMutationId": f"mutation-schema-source-{source or 'none'}",
                "timestamp": "2026-03-18T12:00:00.000Z",
                "type": "lunch",
                "ingredients": [],
                "source": source,
            }
        )
        assert req.source == source


def test_all_input_methods_accepted() -> None:
    for method in ("manual", "photo", "barcode", "text", "saved"):
        req = MealUpsertRequest.model_validate(
            {
                "mealId": "m1",
                "clientMutationId": f"mutation-schema-input-{method}",
                "timestamp": "2026-03-18T12:00:00.000Z",
                "type": "lunch",
                "ingredients": [],
                "inputMethod": method,
            }
        )
        assert req.inputMethod == method


def test_ai_meta_all_fields_optional() -> None:
    meta = MealAiMeta.model_validate({})
    assert meta.model is None
    assert meta.runId is None
    assert meta.confidence is None
    assert meta.warnings == []


def test_totals_defaults_to_zero() -> None:
    totals = MealTotals.model_validate({})
    assert totals.kcal == 0
    assert totals.protein == 0
    assert totals.fat == 0
    assert totals.carbs == 0


def test_ingredient_unit_accepts_only_g_and_ml() -> None:
    from app.schemas.meal import MealIngredient

    for unit in ("g", "ml", None):
        ing = MealIngredient.model_validate(
            {"id": "i1", "name": "Test", "amount": 100, "unit": unit}
        )
        assert ing.unit == unit

    with pytest.raises(ValidationError):
        MealIngredient.model_validate(
            {"id": "i1", "name": "Test", "amount": 100, "unit": "oz"}
        )
