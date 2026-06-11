import os
from typing import Any, cast
from uuid import uuid4

import pytest
from google.cloud import firestore
from pytest_mock import MockerFixture

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
        "id": meal_id,
        "mealId": meal_id,
        "cloudId": meal_id,
        "userUid": "legacy-user-field",
        "timestamp": logged_at,
        "photoUrl": "https://legacy.example.invalid/saved.jpg",
        "loggedAt": logged_at,
        "dayKey": day_key,
        "type": "dinner",
        "name": "Saved emulator dinner",
        "ingredients": [
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
        "source": "ai",
        "inputMethod": "photo",
        "aiMeta": {
            "model": "gpt-4o-mini",
            "runId": f"run-{meal_id}",
            "confidence": 0.91,
            "warnings": ["estimated_portion"],
        },
        "imageRef": {
            "imageId": image_id,
            "storagePath": f"mealTemplates/{user_id}/{meal_id}-{image_id}.jpg",
            "downloadUrl": "https://cdn.example.invalid/saved-canonical.jpg",
        },
        "notes": "Reusable dinner",
        "tags": ["favorite", "high protein"],
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

        assert result["id"] == main_meal_id
        assert result["source"] == "saved"
        assert result["userUid"] is None

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
            "loggedAt": "2026-04-20T18:00:00.000Z",
            "dayKey": "2026-04-20",
            "loggedAtLocalMin": None,
            "tzOffsetMin": None,
            "type": "dinner",
            "name": "Saved emulator dinner",
            "ingredients": [
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
            "createdAt": "2026-04-20T18:00:00.000Z",
            "updatedAt": shared_updated_at,
            "source": "saved",
            "inputMethod": "photo",
            "aiMeta": {
                "model": "gpt-4o-mini",
                "runId": f"run-{main_meal_id}",
                "confidence": 0.91,
                "warnings": ["estimated_portion"],
            },
            "imageRef": {
                "imageId": f"image-main-{run_id}",
                "storagePath": (
                    f"mealTemplates/{user_a}/{main_meal_id}-image-main-{run_id}.jpg"
                ),
                "downloadUrl": "https://cdn.example.invalid/saved-canonical.jpg",
            },
            "notes": "Reusable dinner",
            "tags": ["favorite", "high protein"],
            "deleted": False,
            "totals": {
                "protein": 42.0,
                "fat": 18.0,
                "carbs": 36.0,
                "kcal": 510.0,
            },
        }
        for legacy_field in ("userUid", "cloudId", "timestamp", "photoUrl", "mealId"):
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
        assert [item["id"] for item in first_changes_page] == [expected_change_ids[0]]
        assert first_cursor == f"{shared_updated_at}|{expected_change_ids[0]}"
        assert [item["id"] for item in second_changes_page] == [expected_change_ids[1]]
        assert second_cursor == f"{shared_updated_at}|{expected_change_ids[1]}"
        assert user_b_meal_id not in {
            item["id"] for item in [*first_changes_page, *second_changes_page]
        }

        deleted = await my_meal_service.mark_deleted(
            user_a,
            main_meal_id,
            updated_at=deleted_updated_at,
            client_mutation_id=f"mutation-saved-delete-{run_id}",
        )
        assert deleted["deleted"] is True

        changes_after_delete, _ = await my_meal_service.list_changes(user_a, limit_count=10)
        changes_by_id = {item["id"]: item for item in changes_after_delete}
        assert set(changes_by_id) == {main_meal_id, side_meal_id}
        assert changes_by_id[main_meal_id]["deleted"] is True
        assert changes_by_id[main_meal_id]["updatedAt"] == deleted_updated_at
        assert changes_by_id[main_meal_id]["source"] == "saved"
        assert changes_by_id[side_meal_id]["deleted"] is False
        assert user_b_meal_id not in changes_by_id
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
