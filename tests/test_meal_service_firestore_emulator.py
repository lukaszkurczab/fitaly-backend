import os
from typing import Any, cast
from unittest.mock import call
from uuid import uuid4

import pytest
from google.cloud import firestore
from pytest_mock import MockerFixture

from app.services import meal_service


pytestmark = pytest.mark.skipif(
    not os.getenv("FIRESTORE_EMULATOR_HOST"),
    reason="Firestore emulator is not configured.",
)


def _emulator_client() -> firestore.Client:
    project_id = os.getenv("FIREBASE_PROJECT_ID") or "demo-fitaly-local"
    database_id = os.getenv("FIRESTORE_DATABASE_ID") or "(default)"
    client_class = cast(Any, firestore.Client)
    return cast(firestore.Client, client_class(project=project_id, database=database_id))


def _meal_payload(
    *,
    meal_id: str,
    logged_at: str,
    day_key: str,
    updated_at: str,
    image_id: str,
) -> dict[str, object]:
    return {
        "id": meal_id,
        "clientMutationId": f"mutation-{meal_id}",
        "mealId": meal_id,
        "cloudId": meal_id,
        "userUid": "legacy-user-field",
        "timestamp": logged_at,
        "photoUrl": "https://legacy.example.invalid/photo.jpg",
        "loggedAt": logged_at,
        "dayKey": day_key,
        "type": "lunch",
        "name": "Emulator lunch",
        "ingredients": [
            {
                "id": "ingredient-1",
                "name": "Rice bowl",
                "amount": 250,
                "unit": "g",
                "kcal": 420,
                "protein": 18,
                "carbs": 58,
                "fat": 12,
            }
        ],
        "createdAt": logged_at,
        "updatedAt": updated_at,
        "source": "ai",
        "inputMethod": "photo",
        "aiMeta": {
            "model": "gpt-4o-mini",
            "runId": f"run-{meal_id}",
            "confidence": 0.86,
            "warnings": ["estimated_portion"],
        },
        "imageRef": {
            "imageId": image_id,
            "storagePath": f"meals/test/{image_id}.jpg",
            "downloadUrl": "https://cdn.example.invalid/canonical-photo.jpg",
        },
        "deleted": False,
    }


async def test_pr3_core_meal_loop_uses_canonical_user_meal_documents(
    mocker: MockerFixture,
) -> None:
    client = _emulator_client()
    run_id = uuid4().hex
    user_a = f"l3-pr3-user-a-{run_id}"
    user_b = f"l3-pr3-user-b-{run_id}"
    main_meal_id = f"meal-a-main-{run_id}"
    side_meal_id = f"meal-a-side-{run_id}"
    user_b_meal_id = f"meal-b-{run_id}"
    shared_updated_at = "2026-04-18T12:10:00.000Z"
    deleted_updated_at = "2026-04-18T12:30:00.000Z"

    mocker.patch("app.services.meal_service.get_firestore", return_value=client)
    sync_streak = mocker.patch(
        "app.services.meal_service.streak_service.sync_streak_from_meals"
    )
    capture_calls: list[tuple[str, list[dict[str, object]]]] = []

    async def fake_capture(
        *,
        owner_user_id: str,
        meal_snapshots: list[dict[str, object]],
        memory_enabled: bool,
        suppressed_subject_keys: list[str],
    ) -> object:
        assert memory_enabled is True
        assert suppressed_subject_keys == []
        capture_calls.append((owner_user_id, meal_snapshots))
        return object()

    mocker.patch(
        "app.services.meal_service.smart_memory_capture_service."
        "capture_typical_portion_candidate_from_meal_snapshots",
        new=fake_capture,
    )

    try:
        result = await meal_service.upsert_meal(
            user_a,
            _meal_payload(
                meal_id=main_meal_id,
                logged_at="2026-04-18T12:00:00.000Z",
                day_key="2026-04-18",
                updated_at=shared_updated_at,
                image_id=f"image-main-{run_id}",
            ),
        )
        await meal_service.upsert_meal(
            user_a,
            _meal_payload(
                meal_id=side_meal_id,
                logged_at="2026-04-19T08:00:00.000Z",
                day_key="2026-04-19",
                updated_at=shared_updated_at,
                image_id=f"image-side-{run_id}",
            ),
        )
        await meal_service.upsert_meal(
            user_b,
            _meal_payload(
                meal_id=user_b_meal_id,
                logged_at="2026-04-18T12:00:00.000Z",
                day_key="2026-04-18",
                updated_at=shared_updated_at,
                image_id=f"image-b-{run_id}",
            ),
        )

        assert result["id"] == main_meal_id
        assert result["userUid"] is None
        assert capture_calls[0][0] == user_a
        assert [snapshot["id"] for snapshot in capture_calls[0][1]] == [main_meal_id]

        stored_snapshot = (
            client.collection("users")
            .document(user_a)
            .collection("meals")
            .document(main_meal_id)
            .get()
        )
        assert stored_snapshot.exists is True
        stored_doc = stored_snapshot.to_dict() or {}
        assert stored_doc == {
            "loggedAt": "2026-04-18T12:00:00.000Z",
            "dayKey": "2026-04-18",
            "loggedAtLocalMin": None,
            "tzOffsetMin": None,
            "type": "lunch",
            "name": "Emulator lunch",
            "ingredients": [
                {
                    "id": "ingredient-1",
                    "name": "Rice bowl",
                    "amount": 250.0,
                    "unit": "g",
                    "kcal": 420.0,
                    "protein": 18.0,
                    "fat": 12.0,
                    "carbs": 58.0,
                }
            ],
            "createdAt": "2026-04-18T12:00:00.000Z",
            "updatedAt": shared_updated_at,
            "source": "ai",
            "inputMethod": "photo",
            "aiMeta": {
                "model": "gpt-4o-mini",
                "runId": f"run-{main_meal_id}",
                "confidence": 0.86,
                "warnings": ["estimated_portion"],
            },
            "imageRef": {
                "imageId": f"image-main-{run_id}",
                "storagePath": f"meals/{user_a}/image-main-{run_id}.jpg",
                "downloadUrl": "https://cdn.example.invalid/canonical-photo.jpg",
            },
            "notes": None,
            "tags": [],
            "deleted": False,
            "totals": {
                "protein": 18.0,
                "fat": 12.0,
                "carbs": 58.0,
                "kcal": 420.0,
            },
        }
        for legacy_field in ("userUid", "cloudId", "timestamp", "photoUrl", "mealId"):
            assert legacy_field not in stored_doc

        history, history_cursor = await meal_service.list_history(
            user_a,
            limit_count=10,
            day_key_start="2026-04-18",
            day_key_end="2026-04-18",
        )
        assert history_cursor is None
        assert [item["id"] for item in history] == [main_meal_id]
        assert history[0]["deleted"] is False
        assert user_b_meal_id not in {item["id"] for item in history}

        first_changes_page, first_cursor = await meal_service.list_changes(
            user_a,
            limit_count=1,
        )
        second_changes_page, second_cursor = await meal_service.list_changes(
            user_a,
            limit_count=1,
            after_cursor=first_cursor,
        )
        expected_change_ids = sorted([main_meal_id, side_meal_id])
        assert [item["id"] for item in first_changes_page] == [expected_change_ids[0]]
        assert first_cursor == f"{shared_updated_at}|{expected_change_ids[0]}"
        assert [item["id"] for item in second_changes_page] == [expected_change_ids[1]]
        assert second_cursor == f"{shared_updated_at}|{expected_change_ids[1]}"

        deleted = await meal_service.mark_deleted(
            user_a,
            main_meal_id,
            updated_at=deleted_updated_at,
            client_mutation_id=f"mutation-delete-{main_meal_id}",
        )
        assert deleted["deleted"] is True

        history_after_delete, _ = await meal_service.list_history(
            user_a,
            limit_count=10,
            day_key_start="2026-04-18",
            day_key_end="2026-04-18",
        )
        assert history_after_delete == []

        changes_after_delete, _ = await meal_service.list_changes(user_a, limit_count=10)
        changes_by_id = {item["id"]: item for item in changes_after_delete}
        assert set(changes_by_id) == {main_meal_id, side_meal_id}
        assert changes_by_id[main_meal_id]["deleted"] is True
        assert changes_by_id[main_meal_id]["updatedAt"] == deleted_updated_at
        assert changes_by_id[side_meal_id]["deleted"] is False

        assert sync_streak.call_args_list == [
            call(user_a, reference_day_key="2026-04-18"),
            call(user_a, reference_day_key="2026-04-19"),
            call(user_b, reference_day_key="2026-04-18"),
            call(user_a, reference_day_key="2026-04-18"),
        ]
        assert [owner_user_id for owner_user_id, _snapshots in capture_calls] == [
            user_a,
            user_a,
            user_b,
        ]
        assert {snapshot["id"] for snapshot in capture_calls[1][1]} == {
            main_meal_id,
            side_meal_id,
        }
        assert [snapshot["id"] for snapshot in capture_calls[2][1]] == [user_b_meal_id]
    finally:
        for uid, meal_ids in (
            (user_a, (main_meal_id, side_meal_id)),
            (user_b, (user_b_meal_id,)),
        ):
            user_ref = client.collection("users").document(uid)
            for meal_id in meal_ids:
                user_ref.collection("meals").document(meal_id).delete()
            user_ref.delete()


async def test_meal_delete_marks_real_smart_memory_candidate_source_deleted(
    mocker: MockerFixture,
) -> None:
    client = _emulator_client()
    run_id = uuid4().hex
    user_id = f"l3-smart-memory-source-delete-{run_id}"
    meal_ids = [f"meal-{index}-{run_id}" for index in range(3)]
    logged_days = ["2026-05-01", "2026-05-02", "2026-05-03"]
    deleted_updated_at = "2026-05-04T12:30:00.000Z"

    mocker.patch("app.services.meal_service.get_firestore", return_value=client)
    mocker.patch("app.services.smart_memory_service.get_firestore", return_value=client)
    mocker.patch("app.services.meal_service.streak_service.sync_streak_from_meals")

    user_ref = client.collection("users").document(user_id)
    try:
        for meal_id, day_key in zip(meal_ids, logged_days, strict=True):
            await meal_service.upsert_meal(
                user_id,
                _meal_payload(
                    meal_id=meal_id,
                    logged_at=f"{day_key}T08:00:00.000Z",
                    day_key=day_key,
                    updated_at=f"{day_key}T08:10:00.000Z",
                    image_id=f"image-{meal_id}",
                ),
            )

        candidate_snapshots = list(
            user_ref.collection("smartMemoryCandidates").stream()
        )
        assert len(candidate_snapshots) == 1
        candidate_snapshot = candidate_snapshots[0]
        candidate = candidate_snapshot.to_dict() or {}
        assert candidate["state"] == "activated"
        assert candidate["ownerUserId"] == user_id
        assert candidate["memoryType"] == "typical_portion"
        assert candidate["subject"]["kind"] == "ingredient_alias"
        assert set(candidate["subject"]) == {"kind", "aliasHash"}
        assert candidate["confidenceReasonCodes"] == ["distinct_days_met"]
        assert candidate["evidenceSummary"]["thresholdVersion"] == "typical_portion_v1"
        assert candidate["evidenceSummary"]["eligibleObservationCount"] == 3
        assert candidate["evidenceSummary"]["distinctDayCount"] == 3
        assert candidate["evidenceSummary"]["proposedValue"] == {
            "amount": 250.0,
            "unit": "g",
        }
        assert len(candidate["sourceRefs"]) == 3
        assert all(
            set(source_ref) == {"kind", "sourceHash"}
            for source_ref in candidate["sourceRefs"]
        )
        candidate_text = str(candidate)
        assert "Rice bowl" not in candidate_text
        assert "gpt-4o-mini" not in candidate_text
        assert "estimated_portion" not in candidate_text
        active_items = [
            snapshot.to_dict() or {}
            for snapshot in user_ref.collection("smartMemory").stream()
        ]
        assert len(active_items) == 1
        active_item = active_items[0]
        assert active_item["state"] == "active"
        assert active_item["stateReason"] == "threshold_met"
        assert active_item["memoryType"] == "typical_portion"
        assert active_item["subject"] == candidate["subject"]
        assert active_item["sourceRefs"] == candidate["sourceRefs"]
        assert active_item["userValue"] == {"amount": 250.0, "unit": "g"}
        assert active_item["control"]["sourceCandidateId"] == candidate["candidateId"]

        deleted = await meal_service.mark_deleted(
            user_id,
            meal_ids[0],
            updated_at=deleted_updated_at,
            client_mutation_id=f"delete-{meal_ids[0]}",
        )

        assert deleted["deleted"] is True
        source_deleted_candidate = candidate_snapshot.reference.get().to_dict() or {}
        assert source_deleted_candidate["state"] == "source_deleted"
        assert source_deleted_candidate["suppressionChecks"]["sourceDeleted"] is True
        source_deleted_items = [
            snapshot.to_dict() or {}
            for snapshot in user_ref.collection("smartMemory").stream()
        ]
        assert len(source_deleted_items) == 1
        assert source_deleted_items[0]["state"] == "source_deleted"
        assert source_deleted_items[0]["sourceDeletedAt"] is not None
        assert source_deleted_items[0]["control"]["suggestionsSuppressed"] is True
        tombstones = [
            snapshot.to_dict() or {}
            for snapshot in user_ref.collection("smartMemoryTombstones").stream()
        ]
        assert any(tombstone.get("reasonCode") == "source_deleted" for tombstone in tombstones)
    finally:
        for subcollection_name in (
            "meals",
            "smartMemory",
            "smartMemoryCandidates",
            "smartMemoryTombstones",
            "smartMemoryMutationDedupe",
            "smartMemorySettings",
        ):
            for snapshot in user_ref.collection(subcollection_name).stream():
                snapshot.reference.delete()
        user_ref.delete()
