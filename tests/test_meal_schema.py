from pydantic import ValidationError

from app.schemas.meal import MealItem, MealUpsertRequest


def test_meal_upsert_request_accepts_input_method_and_ai_meta() -> None:
    payload = MealUpsertRequest.model_validate(
        {
            "mealId": "meal-1",
            "timestamp": "2026-03-18T12:00:00.000Z",
            "type": "lunch",
            "ingredients": [],
            "inputMethod": "photo",
            "aiMeta": {
                "model": "gpt-4o-mini",
                "runId": "run-1",
                "confidence": 0.92,
            },
        }
    )

    assert payload.inputMethod == "photo"
    assert payload.aiMeta is not None
    assert payload.aiMeta.model == "gpt-4o-mini"
    assert payload.aiMeta.runId == "run-1"
    assert payload.aiMeta.confidence == 0.92
    assert payload.aiMeta.warnings == []


def test_meal_upsert_request_is_backward_compatible_without_new_fields() -> None:
    payload = MealUpsertRequest.model_validate(
        {
            "mealId": "meal-1",
            "timestamp": "2026-03-18T12:00:00.000Z",
            "type": "lunch",
            "ingredients": [],
        }
    )

    assert payload.inputMethod is None
    assert payload.aiMeta is None


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
            "aiMeta": {
                "model": "gpt-4o-mini",
                "runId": None,
                "confidence": 0.71,
                "warnings": ["partial_totals"],
            },
        }
    )

    assert item.model_dump()["inputMethod"] == "text"
    assert item.model_dump()["aiMeta"] == {
        "model": "gpt-4o-mini",
        "runId": None,
        "confidence": 0.71,
        "warnings": ["partial_totals"],
    }


def test_meal_upsert_request_rejects_invalid_input_method() -> None:
    try:
        MealUpsertRequest.model_validate(
            {
                "mealId": "meal-1",
                "timestamp": "2026-03-18T12:00:00.000Z",
                "type": "lunch",
                "ingredients": [],
                "inputMethod": "voice",
            }
        )
    except ValidationError:
        return

    raise AssertionError("Expected invalid inputMethod to fail validation")
