import os
from typing import Any, cast
from uuid import uuid4

import pytest
from google.cloud import firestore
from pytest_mock import MockerFixture

from app.core.exceptions import FirestoreServiceError
from app.services import my_meal_service


pytestmark = pytest.mark.skipif(
    not os.getenv("FIRESTORE_EMULATOR_HOST"),
    reason="Firestore emulator is not configured.",
)


def _emulator_client() -> firestore.Client:
    project_id = os.getenv("FIREBASE_PROJECT_ID") or "demo-fitaly-local"
    database_id = os.getenv("FIRESTORE_DATABASE_ID") or "(default)"
    client_class = cast(Any, firestore.Client)
    return cast(firestore.Client, client_class(project=project_id, database=database_id))


def _saved_meal_payload(
    *,
    user_id: str,
    meal_id: str,
    logged_at: str,
    day_key: str,
    updated_at: str,
    image_id: str,
) -> dict[str, object]:
    return {
        "templateId": meal_id,
        "ownerUserId": "ignored-client-owner",
        "templateVersion": 1,
        "displayName": "Saved emulator dinner",
        "description": "Reusable dinner",
        "mealTypeHint": "dinner",
        "draftItems": [
            {
                "id": "ingredient-1",
                "name": "Salmon bowl",
                "amount": 300,
                "unit": "g",
                "kcal": 510,
                "protein": 42,
                "carbs": 36,
                "fat": 18,
            }
        ],
        "createdAt": logged_at,
        "updatedAt": updated_at,
        "draftTotals": {"kcal": 510, "protein": 42, "carbs": 36, "fat": 18},
        "nutritionSnapshot": {"kcal": 510, "protein": 42, "carbs": 36, "fat": 18},
        "imageRef": {
            "imageId": image_id,
            "storagePath": f"mealTemplates/{user_id}/{meal_id}-{image_id}.jpg",
            "downloadUrl": "https://cdn.example.invalid/saved-canonical.jpg",
        },
        "deleted": False,
    }


async def test_pr3_my_meals_saved_meal_sync_uses_canonical_firestore_documents(
    mocker: MockerFixture,
) -> None:
    client = _emulator_client()
    run_id = uuid4().hex
    user_a = f"l3-pr3-my-meals-user-a-{run_id}"
    user_b = f"l3-pr3-my-meals-user-b-{run_id}"
    main_meal_id = f"saved-a-main-{run_id}"
    side_meal_id = f"saved-a-side-{run_id}"
    user_b_meal_id = f"saved-b-{run_id}"
    shared_updated_at = "2026-04-20T10:00:00.000Z"
    deleted_updated_at = "2026-04-20T10:30:00.000Z"

    mocker.patch("app.services.my_meal_service.get_firestore", return_value=client)

    try:
        result = await my_meal_service.upsert_saved_meal(
            user_a,
            _saved_meal_payload(
                user_id=user_a,
                meal_id=main_meal_id,
                logged_at="2026-04-20T18:00:00.000Z",
                day_key="2026-04-20",
                updated_at=shared_updated_at,
                image_id=f"image-main-{run_id}",
            )
            | {"clientMutationId": f"mutation-saved-main-{run_id}"},
        )
        await my_meal_service.upsert_saved_meal(
            user_a,
            _saved_meal_payload(
                user_id=user_a,
                meal_id=side_meal_id,
                logged_at="2026-04-21T18:00:00.000Z",
                day_key="2026-04-21",
                updated_at=shared_updated_at,
                image_id=f"image-side-{run_id}",
            )
            | {"clientMutationId": f"mutation-saved-side-{run_id}"},
        )
        await my_meal_service.upsert_saved_meal(
            user_b,
            _saved_meal_payload(
                user_id=user_b,
                meal_id=user_b_meal_id,
                logged_at="2026-04-20T18:00:00.000Z",
                day_key="2026-04-20",
                updated_at=shared_updated_at,
                image_id=f"image-b-{run_id}",
            )
            | {"clientMutationId": f"mutation-saved-user-b-{run_id}"},
        )

        assert result["templateId"] == main_meal_id
        assert result["ownerUserId"] == user_a
        for legacy_field in (
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
        ):
            assert legacy_field not in result

        stored_snapshot = (
            client.collection("users")
            .document(user_a)
            .collection("mealTemplates")
            .document(main_meal_id)
            .get()
        )
        assert stored_snapshot.exists is True
        stored_doc = stored_snapshot.to_dict() or {}
        assert stored_doc == {
            "templateId": main_meal_id,
            "ownerUserId": user_a,
            "templateVersion": 1,
            "displayName": "Saved emulator dinner",
            "description": "Reusable dinner",
            "mealTypeHint": "dinner",
            "draftItems": [
                {
                    "id": "ingredient-1",
                    "name": "Salmon bowl",
                    "amount": 300.0,
                    "unit": "g",
                    "kcal": 510.0,
                    "protein": 42.0,
                    "fat": 18.0,
                    "carbs": 36.0,
                }
            ],
            "draftTotals": {
                "protein": 42.0,
                "fat": 18.0,
                "carbs": 36.0,
                "kcal": 510.0,
            },
            "nutritionSnapshot": {
                "protein": 42.0,
                "fat": 18.0,
                "carbs": 36.0,
                "kcal": 510.0,
            },
            "createdAt": "2026-04-20T18:00:00.000Z",
            "updatedAt": shared_updated_at,
            "imageRef": {
                "imageId": f"image-main-{run_id}",
                "storagePath": (
                    f"mealTemplates/{user_a}/{main_meal_id}-image-main-{run_id}.jpg"
                ),
                "downloadUrl": "https://cdn.example.invalid/saved-canonical.jpg",
            },
            "deleted": False,
        }
        for legacy_field in (
            "userUid",
            "cloudId",
            "timestamp",
            "photoUrl",
            "mealId",
            "loggedAt",
            "dayKey",
            "loggedAtLocalMin",
            "tzOffsetMin",
            "syncState",
            "source",
            "inputMethod",
            "savedMealRefId",
        ):
            assert legacy_field not in stored_doc

        first_changes_page, first_cursor = await my_meal_service.list_changes(
            user_a,
            limit_count=1,
        )
        second_changes_page, second_cursor = await my_meal_service.list_changes(
            user_a,
            limit_count=1,
            after_cursor=first_cursor,
        )
        expected_change_ids = sorted([main_meal_id, side_meal_id])
        assert [item["templateId"] for item in first_changes_page] == [expected_change_ids[0]]
        assert first_cursor == f"{shared_updated_at}|{expected_change_ids[0]}"
        assert [item["templateId"] for item in second_changes_page] == [expected_change_ids[1]]
        assert second_cursor == f"{shared_updated_at}|{expected_change_ids[1]}"
        assert user_b_meal_id not in {
            item["templateId"] for item in [*first_changes_page, *second_changes_page]
        }

        deleted = await my_meal_service.mark_deleted(
            user_a,
            main_meal_id,
            updated_at=deleted_updated_at,
            client_mutation_id=f"mutation-saved-delete-{run_id}",
        )
        assert deleted["deleted"] is True

        changes_after_delete, _ = await my_meal_service.list_changes(user_a, limit_count=10)
        changes_by_id = {item["templateId"]: item for item in changes_after_delete}
        assert set(changes_by_id) == {main_meal_id, side_meal_id}
        assert changes_by_id[main_meal_id]["deleted"] is True
        assert changes_by_id[main_meal_id]["updatedAt"] == deleted_updated_at
        assert changes_by_id[side_meal_id]["deleted"] is False
        assert user_b_meal_id not in changes_by_id

        deleted_snapshot = (
            client.collection("users")
            .document(user_a)
            .collection("mealTemplates")
            .document(main_meal_id)
            .get()
        )
        deleted_doc = deleted_snapshot.to_dict() or {}
        assert deleted_doc["templateId"] == main_meal_id
        assert deleted_doc["ownerUserId"] == user_a
        assert deleted_doc["deleted"] is True
        for legacy_field in (
            "loggedAt",
            "dayKey",
            "loggedAtLocalMin",
            "tzOffsetMin",
            "syncState",
            "source",
            "inputMethod",
            "savedMealRefId",
        ):
            assert legacy_field not in deleted_doc
    finally:
        for uid, meal_ids in (
            (user_a, (main_meal_id, side_meal_id)),
            (user_b, (user_b_meal_id,)),
        ):
            user_ref = client.collection("users").document(uid)
            for meal_id in meal_ids:
                user_ref.collection("mealTemplates").document(meal_id).delete()
            for mutation in user_ref.collection("mealMutationDedupe").stream():
                mutation.reference.delete()
            user_ref.delete()


async def test_meal_template_list_rejects_legacy_logged_meal_shaped_doc(
    mocker: MockerFixture,
) -> None:
    client = _emulator_client()
    run_id = uuid4().hex
    user_id = f"template-legacy-user-{run_id}"
    template_id = f"legacy-template-{run_id}"
    user_ref = client.collection("users").document(user_id)
    template_ref = user_ref.collection("mealTemplates").document(template_id)

    mocker.patch("app.services.my_meal_service.get_firestore", return_value=client)

    try:
        template_ref.set(
            {
                "templateId": template_id,
                "ownerUserId": user_id,
                "templateVersion": 1,
                "displayName": "Legacy saved meal",
                "description": None,
                "mealTypeHint": "lunch",
                "draftItems": [],
                "draftTotals": {"kcal": 0, "protein": 0, "carbs": 0, "fat": 0},
                "nutritionSnapshot": {"kcal": 0, "protein": 0, "carbs": 0, "fat": 0},
                "imageRef": None,
                "createdAt": "2026-04-20T09:00:00.000Z",
                "updatedAt": "2026-04-20T10:00:00.000Z",
                "deleted": False,
                "loggedAt": "2026-04-20T09:00:00.000Z",
                "timestamp": "2026-04-20T09:00:00.000Z",
                "dayKey": "2026-04-20",
                "type": "lunch",
                "ingredients": [],
                "source": "saved",
                "inputMethod": "manual",
                "savedMealRefId": template_id,
            }
        )

        with pytest.raises(FirestoreServiceError, match="logged-meal-only fields"):
            await my_meal_service.list_changes(user_id, limit_count=10)
    finally:
        template_ref.delete()
        user_ref.delete()
