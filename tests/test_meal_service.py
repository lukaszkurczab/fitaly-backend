import asyncio
import logging
from typing import Any

from google.api_core.exceptions import FailedPrecondition
import pytest
from pytest_mock import MockerFixture

from app.core.exceptions import FirestoreServiceError
from app.services import meal_service


class FakeTransaction:
    def __init__(self) -> None:
        self._id = b"transaction-id"
        self._max_attempts = 1
        self._read_only = False
        self.set_calls: list[tuple[object, dict[str, object], bool | None]] = []

    def _begin(self, *args: Any, **kwargs: Any) -> None:
        return None

    def _commit(self) -> list[object]:
        return []

    def _rollback(self) -> None:
        return None

    def _clean_up(self) -> None:
        return None

    def set(
        self,
        document_ref: object,
        data: dict[str, object],
        merge: bool | None = None,
    ) -> None:
        self.set_calls.append((document_ref, data, merge))


def _build_snapshot(
    mocker: MockerFixture,
    *,
    exists: bool,
    data: dict[str, object] | None = None,
):
    snapshot = mocker.Mock()
    snapshot.exists = exists
    snapshot.id = str((data or {}).get("cloudId") or (data or {}).get("mealId") or "meal-1")
    snapshot.to_dict.return_value = data or {}
    return snapshot


def _wire_meal_firestore_refs(
    mocker: MockerFixture,
    *,
    meal_snapshot: object,
    mutation_snapshot: object | None = None,
):
    client = mocker.Mock()
    users_collection = mocker.Mock()
    user_ref = mocker.Mock()
    meals_collection = mocker.Mock()
    mutations_collection = mocker.Mock()
    meal_ref = mocker.Mock()
    mutation_ref = mocker.Mock()
    transaction = FakeTransaction()

    client.collection.return_value = users_collection
    client.transaction.return_value = transaction
    users_collection.document.return_value = user_ref
    def collection_for_name(name: str) -> object:
        if name == "meals":
            return meals_collection
        if name == "mealMutationDedupe":
            return mutations_collection
        return mocker.Mock()

    user_ref.collection.side_effect = collection_for_name
    meals_collection.document.return_value = meal_ref
    mutations_collection.document.return_value = mutation_ref
    meal_ref.get.return_value = meal_snapshot
    mutation_ref.get.return_value = mutation_snapshot or _build_snapshot(mocker, exists=False)
    return client, meal_ref, mutation_ref, transaction


def _base_meal_payload(overrides: dict[str, object] | None = None) -> dict[str, object]:
    return {
        "cloudId": "meal-1",
        "mealId": "meal-1",
        "timestamp": "2026-03-03T12:00:00.000Z",
        "dayKey": "2026-03-03",
        "type": "lunch",
        "ingredients": [],
        "createdAt": "2026-03-03T12:00:00.000Z",
        "updatedAt": "2026-03-03T12:30:00.000Z",
        "deleted": False,
        "totals": {"kcal": 200, "protein": 30, "carbs": 0, "fat": 5},
        **(overrides or {}),
    }


def test_normalize_meal_document_preserves_logged_upload_shaped_storage_path() -> None:
    _meal_id, document = meal_service.normalize_meal_document_payload(
        "user-1",
        _base_meal_payload(
            {
                "imageRef": {
                    "imageId": "image-1",
                    "storagePath": "meals/user-1/image-1.webp",
                    "downloadUrl": "https://cdn/meal.jpg",
                },
            }
        ),
    )

    assert document["imageRef"] == {
        "imageId": "image-1",
        "storagePath": "meals/user-1/image-1.webp",
        "downloadUrl": "https://cdn/meal.jpg",
    }


@pytest.mark.parametrize(
    "storage_path",
    [
        "meals/unknown/image-1.jpg",
        "meals/other-user/image-1.jpg",
        "meals/user-1/custom-image.jpg",
        "meals/user-1/meal-1-image-1.jpg",
        "meals/user-1/image-1",
        "meals/user-1/nested/image-1.jpg",
        "images/image-1.jpg",
        "myMeals/user-1/image-1.jpg",
    ],
)
def test_normalize_meal_document_derives_user_scoped_storage_path(
    storage_path: str,
) -> None:
    _meal_id, document = meal_service.normalize_meal_document_payload(
        "user-1",
        _base_meal_payload(
            {
                "imageRef": {
                    "imageId": "image-1",
                    "storagePath": storage_path,
                    "downloadUrl": "https://cdn/meal.jpg",
                },
            }
        ),
    )

    assert document["imageRef"] == {
        "imageId": "image-1",
        "storagePath": "meals/user-1/image-1.jpg",
        "downloadUrl": "https://cdn/meal.jpg",
    }


def test_normalize_meal_document_derives_missing_storage_path() -> None:
    _meal_id, document = meal_service.normalize_meal_document_payload(
        "user-1",
        _base_meal_payload({"imageRef": {"imageId": "image-1"}}),
    )

    assert document["imageRef"] == {
        "imageId": "image-1",
        "storagePath": "meals/user-1/image-1.jpg",
    }


def test_upsert_meal_keeps_newer_remote_document(mocker: MockerFixture) -> None:
    client, meal_ref, mutation_ref, transaction = _wire_meal_firestore_refs(
        mocker,
        meal_snapshot=_build_snapshot(
            mocker,
            exists=True,
            data={
                "cloudId": "meal-1",
                "mealId": "meal-1",
                "userUid": "user-1",
                "timestamp": "2026-03-03T12:00:00.000Z",
                "dayKey": "2026-03-03",
                "type": "lunch",
                "ingredients": [],
                "createdAt": "2026-03-03T12:00:00.000Z",
                "updatedAt": "2026-03-03T13:00:00.000Z",
                "deleted": False,
                "totals": {"kcal": 200, "protein": 30, "carbs": 0, "fat": 5},
            },
        ),
    )
    mocker.patch("app.services.meal_service.get_firestore", return_value=client)
    sync_streak = mocker.patch("app.services.meal_service.streak_service.sync_streak_from_meals")
    capture = mocker.patch(
        "app.services.meal_service.smart_memory_capture_service."
        "capture_typical_portion_candidate_from_meal_snapshots",
        new=mocker.AsyncMock(),
    )

    result = asyncio.run(
        meal_service.upsert_meal(
            "user-1",
            {
                "cloudId": "meal-1",
                "mealId": "meal-1",
                "timestamp": "2026-03-03T12:00:00.000Z",
                "dayKey": "2026-03-03",
                "type": "lunch",
                "ingredients": [],
                "createdAt": "2026-03-03T12:00:00.000Z",
                "updatedAt": "2026-03-03T12:30:00.000Z",
                "deleted": False,
                "clientMutationId": "mutation-upsert-stale",
            },
        )
    )

    assert result["updatedAt"] == "2026-03-03T13:00:00.000Z"
    meal_ref.set.assert_not_called()
    assert [call[0] for call in transaction.set_calls] == [mutation_ref]
    sync_streak.assert_not_called()
    capture.assert_not_awaited()


def test_upsert_meal_compares_non_canonical_updated_at_as_utc(
    mocker: MockerFixture,
) -> None:
    client, meal_ref, mutation_ref, transaction = _wire_meal_firestore_refs(
        mocker,
        meal_snapshot=_build_snapshot(
            mocker,
            exists=True,
            data={
                "cloudId": "meal-1",
                "mealId": "meal-1",
                "timestamp": "2026-03-03T12:00:00.000Z",
                "dayKey": "2026-03-03",
                "type": "lunch",
                "ingredients": [],
                "createdAt": "2026-03-03T12:00:00.000Z",
                "updatedAt": "2026-03-03T13:00:00+01:00",
                "deleted": False,
                "totals": {"kcal": 200, "protein": 30, "carbs": 0, "fat": 5},
            },
        ),
    )
    mocker.patch("app.services.meal_service.get_firestore", return_value=client)
    sync_streak = mocker.patch("app.services.meal_service.streak_service.sync_streak_from_meals")

    result = asyncio.run(
        meal_service.upsert_meal(
            "user-1",
            {
                "mealId": "meal-1",
                "timestamp": "2026-03-03T12:00:00.000Z",
                "dayKey": "2026-03-03",
                "type": "lunch",
                "ingredients": [],
                "createdAt": "2026-03-03T12:00:00.000Z",
                "updatedAt": "2026-03-03T12:30:00.000Z",
                "deleted": False,
                "clientMutationId": "mutation-upsert-newer",
            },
        )
    )

    assert result["updatedAt"] == "2026-03-03T12:30:00.000Z"
    meal_ref.set.assert_not_called()
    assert [call[0] for call in transaction.set_calls] == [meal_ref, mutation_ref]
    written_document = transaction.set_calls[0][1]
    assert written_document["updatedAt"] == "2026-03-03T12:30:00.000Z"
    sync_streak.assert_called_once_with("user-1", reference_day_key="2026-03-03")


def test_mark_deleted_creates_tombstone_when_meal_is_missing(
    mocker: MockerFixture,
) -> None:
    client, meal_ref, mutation_ref, transaction = _wire_meal_firestore_refs(
        mocker,
        meal_snapshot=_build_snapshot(mocker, exists=False),
    )
    mocker.patch("app.services.meal_service.get_firestore", return_value=client)
    sync_streak = mocker.patch("app.services.meal_service.streak_service.sync_streak_from_meals")

    result = asyncio.run(
        meal_service.mark_deleted(
            "user-1",
            "meal-1",
            updated_at="2026-03-03T12:30:00.000Z",
            client_mutation_id="mutation-delete-missing",
        )
    )

    assert result["id"] == "meal-1"
    assert result["mealId"] == "meal-1"
    assert result["cloudId"] == "meal-1"
    assert result["loggedAt"] == "2026-03-03T12:30:00.000Z"
    assert result["timestamp"] == "2026-03-03T12:30:00.000Z"
    assert result["dayKey"] == "2026-03-03"
    assert result["deleted"] is True
    meal_ref.set.assert_not_called()
    assert [call[0] for call in transaction.set_calls] == [meal_ref, mutation_ref]
    assert transaction.set_calls[0] == (
        meal_ref,
        {
            "loggedAt": "2026-03-03T12:30:00.000Z",
            "dayKey": "2026-03-03",
            "loggedAtLocalMin": None,
            "tzOffsetMin": None,
            "type": "other",
            "name": None,
            "ingredients": [],
            "createdAt": "2026-03-03T12:30:00.000Z",
            "updatedAt": "2026-03-03T12:30:00.000Z",
            "source": None,
            "inputMethod": None,
            "aiMeta": None,
            "imageRef": None,
            "notes": None,
            "tags": [],
            "deleted": True,
            "totals": {"protein": 0.0, "fat": 0.0, "carbs": 0.0, "kcal": 0.0},
        },
        True,
    )
    sync_streak.assert_called_once_with("user-1", reference_day_key="2026-03-03")


def test_mark_deleted_compares_non_canonical_updated_at_as_utc(
    mocker: MockerFixture,
) -> None:
    client, meal_ref, mutation_ref, transaction = _wire_meal_firestore_refs(
        mocker,
        meal_snapshot=_build_snapshot(
            mocker,
            exists=True,
            data={
                "cloudId": "meal-1",
                "mealId": "meal-1",
                "timestamp": "2026-03-03T12:00:00.000Z",
                "dayKey": "2026-03-03",
                "type": "lunch",
                "ingredients": [],
                "createdAt": "2026-03-03T12:00:00.000Z",
                "updatedAt": "2026-03-03T13:00:00+01:00",
                "deleted": False,
                "totals": {"kcal": 200, "protein": 30, "carbs": 0, "fat": 5},
            },
        ),
    )
    mocker.patch("app.services.meal_service.get_firestore", return_value=client)
    sync_streak = mocker.patch("app.services.meal_service.streak_service.sync_streak_from_meals")

    result = asyncio.run(
        meal_service.mark_deleted(
            "user-1",
            "meal-1",
            updated_at="2026-03-03T12:30:00.000Z",
            client_mutation_id="mutation-delete-newer",
        )
    )

    assert result["updatedAt"] == "2026-03-03T12:30:00.000Z"
    assert result["deleted"] is True
    meal_ref.set.assert_not_called()
    assert [call[0] for call in transaction.set_calls] == [meal_ref, mutation_ref]
    written_document = transaction.set_calls[0][1]
    assert written_document["updatedAt"] == "2026-03-03T12:30:00.000Z"
    assert written_document["deleted"] is True
    sync_streak.assert_called_once_with("user-1", reference_day_key="2026-03-03")


def test_upsert_meal_persists_input_method_and_ai_meta(mocker: MockerFixture) -> None:
    client, meal_ref, mutation_ref, transaction = _wire_meal_firestore_refs(
        mocker,
        meal_snapshot=_build_snapshot(mocker, exists=False),
    )
    mocker.patch("app.services.meal_service.get_firestore", return_value=client)
    sync_streak = mocker.patch("app.services.meal_service.streak_service.sync_streak_from_meals")

    result = asyncio.run(
        meal_service.upsert_meal(
            "user-1",
            {
                "cloudId": "meal-1",
                "mealId": "meal-1",
                "timestamp": "2026-03-03T12:00:00.000Z",
                "dayKey": "2026-03-03",
                "type": "lunch",
                "ingredients": [],
                "createdAt": "2026-03-03T12:00:00.000Z",
                "updatedAt": "2026-03-03T12:30:00.000Z",
                "deleted": False,
                "inputMethod": "photo",
                "aiMeta": {
                    "model": "gpt-4o-mini",
                    "runId": "run-1",
                    "confidence": 0.84,
                    "warnings": ["partial_totals"],
                },
                "clientMutationId": "mutation-upsert-ai-meta",
            },
        )
    )

    assert result["inputMethod"] == "photo"
    assert result["aiMeta"] == {
        "model": "gpt-4o-mini",
        "runId": "run-1",
        "confidence": 0.84,
        "warnings": ["partial_totals"],
    }
    meal_ref.set.assert_not_called()
    assert [call[0] for call in transaction.set_calls] == [meal_ref, mutation_ref]
    assert transaction.set_calls[0] == (
        meal_ref,
        {
            "loggedAt": "2026-03-03T12:00:00.000Z",
            "dayKey": "2026-03-03",
            "loggedAtLocalMin": None,
            "tzOffsetMin": None,
            "type": "lunch",
            "name": None,
            "ingredients": [],
            "createdAt": "2026-03-03T12:00:00.000Z",
            "updatedAt": "2026-03-03T12:30:00.000Z",
            "source": None,
            "inputMethod": "photo",
            "aiMeta": {
                "model": "gpt-4o-mini",
                "runId": "run-1",
                "confidence": 0.84,
                "warnings": ["partial_totals"],
            },
            "imageRef": None,
            "notes": None,
            "tags": [],
            "deleted": False,
            "totals": {"protein": 0.0, "fat": 0.0, "carbs": 0.0, "kcal": 0.0},
        },
        True,
    )
    sync_streak.assert_called_once_with("user-1", reference_day_key="2026-03-03")


def test_smart_memory_capture_recent_meal_read_is_bounded(
    mocker: MockerFixture,
) -> None:
    snapshots: list[dict[str, Any]] = [{"id": "meal-1", "deleted": False}]
    list_history = mocker.patch(
        "app.services.meal_service.list_history",
        new=mocker.AsyncMock(return_value=(snapshots, "ignored-cursor")),
    )

    result = asyncio.run(
        meal_service._list_recent_meal_snapshots_for_smart_memory_capture("user-1")
    )

    assert result == snapshots
    list_history.assert_awaited_once_with(
        "user-1",
        limit_count=meal_service.SMART_MEMORY_CAPTURE_RECENT_MEAL_LIMIT,
    )


def test_upsert_meal_triggers_typical_portion_capture_after_applied_non_deleted_upsert(
    mocker: MockerFixture,
) -> None:
    client, meal_ref, mutation_ref, transaction = _wire_meal_firestore_refs(
        mocker,
        meal_snapshot=_build_snapshot(mocker, exists=False),
    )
    mocker.patch("app.services.meal_service.get_firestore", return_value=client)
    call_order: list[str] = []

    async def fake_sync_streak(user_id: str, *, reference_day_key: str | None) -> None:
        assert user_id == "user-1"
        assert reference_day_key == "2026-03-03"
        call_order.append("streak")

    recent_snapshots: list[dict[str, Any]] = [
        {
            "id": "meal-1",
            "dayKey": "2026-03-03",
            "loggedAt": "2026-03-03T12:00:00.000Z",
            "updatedAt": "2026-03-03T12:30:00.000Z",
            "deleted": False,
            "ingredients": [
                {
                    "id": "ingredient-1",
                    "name": "Oats",
                    "amount": 60,
                    "unit": "g",
                }
            ],
        }
    ]

    async def fake_recent_snapshots(user_id: str) -> list[dict[str, Any]]:
        assert user_id == "user-1"
        return recent_snapshots

    get_settings = mocker.patch(
        "app.services.meal_service.smart_memory_service.get_settings",
        new=mocker.AsyncMock(return_value={"enabled": True}),
    )
    filter_tombstones = mocker.patch(
        "app.services.meal_service.smart_memory_service.filter_existing_tombstone_subject_keys",
        new=mocker.AsyncMock(return_value=["typical_portion:suppressed-hash"]),
    )

    async def fake_capture(
        *,
        owner_user_id: str,
        meal_snapshots: list[dict[str, Any]],
        memory_enabled: bool,
        suppressed_subject_keys: list[str],
    ) -> object:
        assert owner_user_id == "user-1"
        assert meal_snapshots == recent_snapshots
        assert memory_enabled is True
        assert suppressed_subject_keys == ["typical_portion:suppressed-hash"]
        assert [call[0] for call in transaction.set_calls] == [meal_ref, mutation_ref]
        call_order.append("capture")
        return object()

    mocker.patch(
        "app.services.meal_service.streak_service.sync_streak_from_meals",
        new=fake_sync_streak,
    )
    mocker.patch(
        "app.services.meal_service._list_recent_meal_snapshots_for_smart_memory_capture",
        new=fake_recent_snapshots,
    )
    mocker.patch(
        "app.services.meal_service.smart_memory_capture_service."
        "capture_typical_portion_candidate_from_meal_snapshots",
        new=fake_capture,
    )

    result = asyncio.run(
        meal_service.upsert_meal(
            "user-1",
            {
                "cloudId": "meal-1",
                "mealId": "meal-1",
                "timestamp": "2026-03-03T12:00:00.000Z",
                "dayKey": "2026-03-03",
                "type": "lunch",
                "ingredients": [],
                "createdAt": "2026-03-03T12:00:00.000Z",
                "updatedAt": "2026-03-03T12:30:00.000Z",
                "deleted": False,
                "clientMutationId": "mutation-upsert-capture",
            },
        )
    )

    assert result == {
        "id": "meal-1",
        "loggedAt": "2026-03-03T12:00:00.000Z",
        "dayKey": "2026-03-03",
        "loggedAtLocalMin": None,
        "tzOffsetMin": None,
        "type": "lunch",
        "name": None,
        "ingredients": [],
        "createdAt": "2026-03-03T12:00:00.000Z",
        "updatedAt": "2026-03-03T12:30:00.000Z",
        "syncState": "synced",
        "source": None,
        "inputMethod": None,
        "aiMeta": None,
        "imageRef": None,
        "notes": None,
        "tags": [],
        "deleted": False,
        "totals": {"protein": 0.0, "fat": 0.0, "carbs": 0.0, "kcal": 0.0},
        "mealId": "meal-1",
        "cloudId": "meal-1",
        "timestamp": "2026-03-03T12:00:00.000Z",
        "imageId": None,
        "photoUrl": None,
        "userUid": None,
    }
    assert call_order == ["streak", "capture"]
    get_settings.assert_awaited_once_with("user-1")
    filter_tombstones.assert_awaited_once()
    assert filter_tombstones.await_args is not None
    assert filter_tombstones.await_args.args[0] == "user-1"
    assert filter_tombstones.await_args.args[1]


def test_upsert_meal_skips_typical_portion_capture_for_deleted_upsert(
    mocker: MockerFixture,
) -> None:
    client, _meal_ref, _mutation_ref, _transaction = _wire_meal_firestore_refs(
        mocker,
        meal_snapshot=_build_snapshot(mocker, exists=False),
    )
    mocker.patch("app.services.meal_service.get_firestore", return_value=client)
    sync_streak = mocker.patch("app.services.meal_service.streak_service.sync_streak_from_meals")
    recent_snapshot_read = mocker.patch(
        "app.services.meal_service._list_recent_meal_snapshots_for_smart_memory_capture",
        new=mocker.AsyncMock(return_value=[]),
    )
    capture = mocker.patch(
        "app.services.meal_service.smart_memory_capture_service."
        "capture_typical_portion_candidate_from_meal_snapshots",
        new=mocker.AsyncMock(),
    )

    result = asyncio.run(
        meal_service.upsert_meal(
            "user-1",
            {
                "cloudId": "meal-1",
                "mealId": "meal-1",
                "timestamp": "2026-03-03T12:00:00.000Z",
                "dayKey": "2026-03-03",
                "type": "lunch",
                "ingredients": [],
                "createdAt": "2026-03-03T12:00:00.000Z",
                "updatedAt": "2026-03-03T12:30:00.000Z",
                "deleted": True,
                "clientMutationId": "mutation-upsert-deleted",
            },
        )
    )

    assert result["deleted"] is True
    sync_streak.assert_called_once_with("user-1", reference_day_key="2026-03-03")
    recent_snapshot_read.assert_not_awaited()
    capture.assert_not_awaited()


def test_upsert_meal_skips_typical_portion_capture_when_memory_is_disabled(
    mocker: MockerFixture,
) -> None:
    client, _meal_ref, _mutation_ref, _transaction = _wire_meal_firestore_refs(
        mocker,
        meal_snapshot=_build_snapshot(mocker, exists=False),
    )
    mocker.patch("app.services.meal_service.get_firestore", return_value=client)
    mocker.patch("app.services.meal_service.streak_service.sync_streak_from_meals")
    get_settings = mocker.patch(
        "app.services.meal_service.smart_memory_service.get_settings",
        new=mocker.AsyncMock(return_value={"enabled": False}),
    )
    filter_tombstones = mocker.patch(
        "app.services.meal_service.smart_memory_service.filter_existing_tombstone_subject_keys",
        new=mocker.AsyncMock(return_value=[]),
    )
    recent_snapshot_read = mocker.patch(
        "app.services.meal_service._list_recent_meal_snapshots_for_smart_memory_capture",
        new=mocker.AsyncMock(return_value=[]),
    )
    capture = mocker.patch(
        "app.services.meal_service.smart_memory_capture_service."
        "capture_typical_portion_candidate_from_meal_snapshots",
        new=mocker.AsyncMock(),
    )

    result = asyncio.run(
        meal_service.upsert_meal(
            "user-1",
            {
                "cloudId": "meal-1",
                "mealId": "meal-1",
                "timestamp": "2026-03-03T12:00:00.000Z",
                "dayKey": "2026-03-03",
                "type": "lunch",
                "ingredients": [],
                "createdAt": "2026-03-03T12:00:00.000Z",
                "updatedAt": "2026-03-03T12:30:00.000Z",
                "deleted": False,
                "clientMutationId": "mutation-upsert-memory-disabled",
            },
        )
    )

    assert result["id"] == "meal-1"
    get_settings.assert_awaited_once_with("user-1")
    filter_tombstones.assert_not_awaited()
    recent_snapshot_read.assert_not_awaited()
    capture.assert_not_awaited()


def test_upsert_meal_logs_typical_portion_capture_failure_and_returns_meal(
    mocker: MockerFixture,
    caplog: pytest.LogCaptureFixture,
) -> None:
    client, _meal_ref, _mutation_ref, _transaction = _wire_meal_firestore_refs(
        mocker,
        meal_snapshot=_build_snapshot(mocker, exists=False),
    )
    mocker.patch("app.services.meal_service.get_firestore", return_value=client)
    mocker.patch("app.services.meal_service.streak_service.sync_streak_from_meals")
    mocker.patch(
        "app.services.meal_service.smart_memory_service.get_settings",
        new=mocker.AsyncMock(return_value={"enabled": True}),
    )
    mocker.patch(
        "app.services.meal_service.smart_memory_service.filter_existing_tombstone_subject_keys",
        new=mocker.AsyncMock(return_value=[]),
    )
    mocker.patch(
        "app.services.meal_service._list_recent_meal_snapshots_for_smart_memory_capture",
        new=mocker.AsyncMock(return_value=[{"id": "meal-1", "deleted": False}]),
    )
    mocker.patch(
        "app.services.meal_service.smart_memory_capture_service."
        "capture_typical_portion_candidate_from_meal_snapshots",
        new=mocker.AsyncMock(side_effect=RuntimeError("capture failed")),
    )

    with caplog.at_level(logging.ERROR, logger=meal_service.logger.name):
        result = asyncio.run(
            meal_service.upsert_meal(
                "user-1",
                {
                    "cloudId": "meal-1",
                    "mealId": "meal-1",
                    "timestamp": "2026-03-03T12:00:00.000Z",
                    "dayKey": "2026-03-03",
                    "type": "lunch",
                    "ingredients": [],
                    "createdAt": "2026-03-03T12:00:00.000Z",
                    "updatedAt": "2026-03-03T12:30:00.000Z",
                    "deleted": False,
                    "clientMutationId": "mutation-upsert-capture-failure",
                },
            )
        )

    assert result["id"] == "meal-1"
    log_records = [
        record
        for record in caplog.records
        if record.message
        == "Failed to capture Smart Memory typical portion after meal upsert."
    ]
    assert len(log_records) == 1
    assert getattr(log_records[0], "user_id") == "user-1"
    assert getattr(log_records[0], "meal_id") == "meal-1"


def test_upsert_meal_duplicate_replay_uses_dedupe_record_without_second_write(
    mocker: MockerFixture,
) -> None:
    first_client, meal_ref, mutation_ref, first_transaction = _wire_meal_firestore_refs(
        mocker,
        meal_snapshot=_build_snapshot(mocker, exists=False),
    )
    get_firestore = mocker.patch(
        "app.services.meal_service.get_firestore",
        return_value=first_client,
    )
    sync_streak = mocker.patch("app.services.meal_service.streak_service.sync_streak_from_meals")
    mocker.patch(
        "app.services.meal_service.smart_memory_service.get_settings",
        new=mocker.AsyncMock(return_value={"enabled": True}),
    )
    mocker.patch(
        "app.services.meal_service.smart_memory_service.filter_existing_tombstone_subject_keys",
        new=mocker.AsyncMock(return_value=[]),
    )
    recent_snapshot_read = mocker.patch(
        "app.services.meal_service._list_recent_meal_snapshots_for_smart_memory_capture",
        new=mocker.AsyncMock(return_value=[{"id": "meal-1", "deleted": False}]),
    )
    capture = mocker.patch(
        "app.services.meal_service.smart_memory_capture_service."
        "capture_typical_portion_candidate_from_meal_snapshots",
        new=mocker.AsyncMock(),
    )
    payload: dict[str, object] = {
        "cloudId": "meal-1",
        "mealId": "meal-1",
        "timestamp": "2026-03-03T12:00:00.000Z",
        "dayKey": "2026-03-03",
        "type": "lunch",
        "ingredients": [],
        "createdAt": "2026-03-03T12:00:00.000Z",
        "updatedAt": "2026-03-03T12:30:00.000Z",
        "deleted": False,
        "clientMutationId": "mutation-upsert-replay",
    }

    first_result = asyncio.run(meal_service.upsert_meal("user-1", payload))
    mutation_record = first_transaction.set_calls[1][1]

    second_client, _second_meal_ref, _second_mutation_ref, second_transaction = (
        _wire_meal_firestore_refs(
            mocker,
            meal_snapshot=_build_snapshot(mocker, exists=False),
            mutation_snapshot=_build_snapshot(
                mocker,
                exists=True,
                data=mutation_record,
            ),
        )
    )
    get_firestore.return_value = second_client

    second_result = asyncio.run(meal_service.upsert_meal("user-1", payload))

    assert first_result == second_result
    assert [call[0] for call in first_transaction.set_calls] == [meal_ref, mutation_ref]
    assert second_transaction.set_calls == []
    sync_streak.assert_called_once_with("user-1", reference_day_key="2026-03-03")
    assert recent_snapshot_read.await_count == 1
    assert capture.await_count == 1


def test_mark_deleted_marks_smart_memory_sources_deleted(
    mocker: MockerFixture,
) -> None:
    client, _meal_ref, _mutation_ref, _transaction = _wire_meal_firestore_refs(
        mocker,
        meal_snapshot=_build_snapshot(
            mocker,
            exists=True,
            data={
                "cloudId": "meal-1",
                "mealId": "meal-1",
                "timestamp": "2026-03-03T12:00:00.000Z",
                "dayKey": "2026-03-03",
                "type": "lunch",
                "ingredients": [
                    {
                        "id": "ingredient-1",
                        "name": "Oats",
                        "amount": 60,
                        "unit": "g",
                    }
                ],
                "createdAt": "2026-03-03T12:00:00.000Z",
                "updatedAt": "2026-03-03T12:30:00.000Z",
                "deleted": False,
            },
        ),
    )
    mocker.patch("app.services.meal_service.get_firestore", return_value=client)
    mocker.patch("app.services.meal_service.streak_service.sync_streak_from_meals")
    mark_sources_deleted = mocker.patch(
        "app.services.meal_service.smart_memory_service.mark_sources_deleted_by_source_hashes",
        new=mocker.AsyncMock(return_value=1),
    )

    result = asyncio.run(
        meal_service.mark_deleted(
            "user-1",
            "meal-1",
            updated_at="2026-03-03T13:00:00.000Z",
            client_mutation_id="mutation-delete-memory-source",
        )
    )

    assert result["deleted"] is True
    mark_sources_deleted.assert_awaited_once()
    await_args = mark_sources_deleted.await_args
    assert await_args is not None
    assert await_args.args[0] == "user-1"
    source_hashes = await_args.args[1]
    assert len(source_hashes) == 1
    assert isinstance(source_hashes[0], str)
    assert await_args.kwargs["memory_type"] == "typical_portion"
    subject_keys = await_args.kwargs["subject_keys"]
    assert len(subject_keys) == 1
    assert isinstance(subject_keys[0], str)


def test_mark_deleted_propagates_smart_memory_source_delete_failure(
    mocker: MockerFixture,
) -> None:
    client, _meal_ref, _mutation_ref, _transaction = _wire_meal_firestore_refs(
        mocker,
        meal_snapshot=_build_snapshot(
            mocker,
            exists=True,
            data={
                "cloudId": "meal-1",
                "mealId": "meal-1",
                "timestamp": "2026-03-03T12:00:00.000Z",
                "dayKey": "2026-03-03",
                "type": "lunch",
                "ingredients": [
                    {
                        "id": "ingredient-1",
                        "name": "Oats",
                        "amount": 60,
                        "unit": "g",
                    }
                ],
                "createdAt": "2026-03-03T12:00:00.000Z",
                "updatedAt": "2026-03-03T12:30:00.000Z",
                "deleted": False,
            },
        ),
    )
    mocker.patch("app.services.meal_service.get_firestore", return_value=client)
    mocker.patch("app.services.meal_service.streak_service.sync_streak_from_meals")
    mocker.patch(
        "app.services.meal_service.smart_memory_service.mark_sources_deleted_by_source_hashes",
        new=mocker.AsyncMock(side_effect=RuntimeError("source cascade failed")),
    )

    with pytest.raises(RuntimeError, match="source cascade failed"):
        asyncio.run(
            meal_service.mark_deleted(
                "user-1",
                "meal-1",
                updated_at="2026-03-03T13:00:00.000Z",
                client_mutation_id="mutation-delete-memory-source-failure",
            )
        )


def test_upsert_meal_rejects_reused_client_mutation_id_for_different_payload(
    mocker: MockerFixture,
) -> None:
    client, _meal_ref, _mutation_ref, transaction = _wire_meal_firestore_refs(
        mocker,
        meal_snapshot=_build_snapshot(mocker, exists=False),
        mutation_snapshot=_build_snapshot(
            mocker,
            exists=True,
            data={
                "clientMutationId": "mutation-reused",
                "kind": "upsert",
                "mealId": "meal-1",
                "payloadHash": "different-payload",
                "resultMeal": {"id": "meal-1"},
            },
        ),
    )
    mocker.patch("app.services.meal_service.get_firestore", return_value=client)
    sync_streak = mocker.patch("app.services.meal_service.streak_service.sync_streak_from_meals")

    with pytest.raises(meal_service.MealMutationDedupeConflictError):
        asyncio.run(
            meal_service.upsert_meal(
                "user-1",
                {
                    "cloudId": "meal-1",
                    "mealId": "meal-1",
                    "timestamp": "2026-03-03T12:00:00.000Z",
                    "dayKey": "2026-03-03",
                    "type": "lunch",
                    "ingredients": [],
                    "createdAt": "2026-03-03T12:00:00.000Z",
                    "updatedAt": "2026-03-03T12:30:00.000Z",
                    "deleted": False,
                    "clientMutationId": "mutation-reused",
                },
            )
        )

    assert transaction.set_calls == []
    sync_streak.assert_not_called()


def test_mark_deleted_keeps_newer_remote_document(mocker: MockerFixture) -> None:
    client, meal_ref, mutation_ref, transaction = _wire_meal_firestore_refs(
        mocker,
        meal_snapshot=_build_snapshot(
            mocker,
            exists=True,
            data={
                "cloudId": "meal-1",
                "mealId": "meal-1",
                "timestamp": "2026-03-03T12:00:00.000Z",
                "dayKey": "2026-03-03",
                "type": "lunch",
                "ingredients": [],
                "createdAt": "2026-03-03T12:00:00.000Z",
                "updatedAt": "2026-03-03T13:00:00.000Z",
                "deleted": False,
                "totals": {"kcal": 200, "protein": 30, "carbs": 0, "fat": 5},
            },
        ),
    )
    mocker.patch("app.services.meal_service.get_firestore", return_value=client)
    sync_streak = mocker.patch("app.services.meal_service.streak_service.sync_streak_from_meals")

    result = asyncio.run(
        meal_service.mark_deleted(
            "user-1",
            "meal-1",
            updated_at="2026-03-03T12:30:00.000Z",
            client_mutation_id="mutation-delete-stale",
        )
    )

    assert result["updatedAt"] == "2026-03-03T13:00:00.000Z"
    assert result["deleted"] is False
    meal_ref.set.assert_not_called()
    assert [call[0] for call in transaction.set_calls] == [mutation_ref]
    sync_streak.assert_not_called()


def test_mark_deleted_duplicate_replay_uses_dedupe_record_without_second_write(
    mocker: MockerFixture,
) -> None:
    first_client, meal_ref, mutation_ref, first_transaction = _wire_meal_firestore_refs(
        mocker,
        meal_snapshot=_build_snapshot(mocker, exists=False),
    )
    get_firestore = mocker.patch(
        "app.services.meal_service.get_firestore",
        return_value=first_client,
    )
    sync_streak = mocker.patch("app.services.meal_service.streak_service.sync_streak_from_meals")

    first_result = asyncio.run(
        meal_service.mark_deleted(
            "user-1",
            "meal-1",
            updated_at="2026-03-03T12:30:00.000Z",
            client_mutation_id="mutation-delete-replay",
        )
    )
    mutation_record = first_transaction.set_calls[1][1]

    second_client, _second_meal_ref, _second_mutation_ref, second_transaction = (
        _wire_meal_firestore_refs(
            mocker,
            meal_snapshot=_build_snapshot(mocker, exists=False),
            mutation_snapshot=_build_snapshot(
                mocker,
                exists=True,
                data=mutation_record,
            ),
        )
    )
    get_firestore.return_value = second_client

    second_result = asyncio.run(
        meal_service.mark_deleted(
            "user-1",
            "meal-1",
            updated_at="2026-03-03T12:30:00.000Z",
            client_mutation_id="mutation-delete-replay",
        )
    )

    assert first_result == second_result
    assert [call[0] for call in first_transaction.set_calls] == [meal_ref, mutation_ref]
    assert second_transaction.set_calls == []
    sync_streak.assert_called_once_with("user-1", reference_day_key="2026-03-03")


def test_upload_photo_returns_storage_download_url(mocker: MockerFixture) -> None:
    bucket = mocker.Mock()
    bucket.name = "demo.appspot.com"
    blob = mocker.Mock()
    bucket.blob.return_value = blob
    upload = mocker.Mock()
    upload.filename = "meal.jpg"
    upload.content_type = "image/jpeg"
    upload.file = mocker.Mock()
    mocker.patch("app.services.meal_storage.get_storage_bucket", return_value=bucket)

    payload = asyncio.run(meal_service.upload_photo("user-1", upload))

    bucket.blob.assert_called_once()
    blob.upload_from_file.assert_called_once_with(
        upload.file,
        content_type="image/jpeg",
    )
    blob.patch.assert_called_once_with()
    upload.file.seek.assert_called_once_with(0)
    upload.file.close.assert_called_once_with()
    assert payload["imageId"]
    assert payload["storagePath"] == bucket.blob.call_args.args[0]
    assert payload["storagePath"].startswith("meals/user-1/")
    assert payload["storagePath"].endswith(".jpg")
    assert payload["photoUrl"].startswith(
        "https://firebasestorage.googleapis.com/v0/b/demo.appspot.com/o/meals%2Fuser-1%2F"
    )


def test_upload_photo_skips_metadata_patch_in_storage_emulator(
    mocker: MockerFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("FIREBASE_STORAGE_EMULATOR_HOST", "127.0.0.1:9199")
    bucket = mocker.Mock()
    bucket.name = "demo.appspot.com"
    blob = mocker.Mock()
    bucket.blob.return_value = blob
    upload = mocker.Mock()
    upload.filename = "meal.jpg"
    upload.content_type = "image/jpeg"
    upload.file = mocker.Mock()
    mocker.patch("app.services.meal_storage.get_storage_bucket", return_value=bucket)

    payload = asyncio.run(meal_service.upload_photo("user-1", upload))

    blob.upload_from_file.assert_called_once_with(
        upload.file,
        content_type="image/jpeg",
    )
    blob.patch.assert_not_called()
    assert blob.metadata["firebaseStorageDownloadTokens"]
    assert payload["storagePath"] == bucket.blob.call_args.args[0]
    assert payload["storagePath"].startswith("meals/user-1/")
    assert payload["photoUrl"].startswith(
        "https://firebasestorage.googleapis.com/v0/b/demo.appspot.com/o/meals%2Fuser-1%2F"
    )


def test_resolve_photo_uses_meal_document_photo_url(mocker: MockerFixture) -> None:
    client = mocker.Mock()
    users_collection = mocker.Mock()
    user_ref = mocker.Mock()
    meals_collection = mocker.Mock()
    meal_ref = mocker.Mock()

    client.collection.return_value = users_collection
    users_collection.document.return_value = user_ref
    user_ref.collection.return_value = meals_collection
    meals_collection.document.return_value = meal_ref
    meal_ref.get.return_value = _build_snapshot(
        mocker,
        exists=True,
        data={
            "cloudId": "meal-1",
            "mealId": "meal-1",
            "userUid": "user-1",
            "timestamp": "2026-03-03T12:00:00.000Z",
            "type": "lunch",
            "ingredients": [],
            "createdAt": "2026-03-03T12:00:00.000Z",
            "updatedAt": "2026-03-03T12:00:00.000Z",
            "imageId": "image-1",
            "photoUrl": "https://cdn/meal.jpg",
            "deleted": False,
            "totals": {"kcal": 200, "protein": 30, "carbs": 0, "fat": 5},
        },
    )
    mocker.patch("app.services.meal_service.get_firestore", return_value=client)

    payload = asyncio.run(
        meal_service.resolve_photo("user-1", meal_id="meal-1", image_id="image-1")
    )

    assert payload == {
        "mealId": "meal-1",
        "imageId": "image-1",
        "storagePath": "meals/user-1/image-1.jpg",
        "photoUrl": "https://cdn/meal.jpg",
    }


def test_resolve_photo_ignores_foreign_stored_storage_path(
    mocker: MockerFixture,
) -> None:
    client = mocker.Mock()
    users_collection = mocker.Mock()
    user_ref = mocker.Mock()
    meals_collection = mocker.Mock()
    meal_ref = mocker.Mock()

    client.collection.return_value = users_collection
    users_collection.document.return_value = user_ref
    user_ref.collection.return_value = meals_collection
    meals_collection.document.return_value = meal_ref
    meal_ref.get.return_value = _build_snapshot(
        mocker,
        exists=True,
        data=_base_meal_payload(
            {
                "imageRef": {
                    "imageId": "image-1",
                    "storagePath": "meals/other-user/image-1.jpg",
                },
            }
        ),
    )
    mocker.patch("app.services.meal_service.get_firestore", return_value=client)
    bucket = mocker.Mock()
    bucket.name = "demo.appspot.com"
    blob = mocker.Mock()
    blob.exists.return_value = True
    blob.metadata = {"firebaseStorageDownloadTokens": "token-1"}
    bucket.blob.return_value = blob
    mocker.patch("app.services.meal_service.get_storage_bucket", return_value=bucket)

    payload = asyncio.run(
        meal_service.resolve_photo("user-1", meal_id="meal-1", image_id="image-1")
    )

    bucket.blob.assert_called_once_with("meals/user-1/image-1.jpg")
    assert payload == {
        "mealId": "meal-1",
        "imageId": "image-1",
        "storagePath": "meals/user-1/image-1.jpg",
        "photoUrl": (
            "https://firebasestorage.googleapis.com/v0/b/demo.appspot.com/o/"
            "meals%2Fuser-1%2Fimage-1.jpg?alt=media&token=token-1"
        ),
    }


def test_resolve_photo_ignores_user_scoped_inconsistent_stored_storage_path(
    mocker: MockerFixture,
) -> None:
    client = mocker.Mock()
    users_collection = mocker.Mock()
    user_ref = mocker.Mock()
    meals_collection = mocker.Mock()
    meal_ref = mocker.Mock()

    client.collection.return_value = users_collection
    users_collection.document.return_value = user_ref
    user_ref.collection.return_value = meals_collection
    meals_collection.document.return_value = meal_ref
    meal_ref.get.return_value = _build_snapshot(mocker, exists=True)
    mocker.patch("app.services.meal_service.get_firestore", return_value=client)
    mocker.patch(
        "app.services.meal_service._normalize_meal_snapshot",
        return_value={
            "imageRef": {
                "imageId": "image-1",
                "storagePath": "meals/user-1/custom-image.jpg",
            },
        },
    )
    bucket = mocker.Mock()
    bucket.name = "demo.appspot.com"
    blob = mocker.Mock()
    blob.exists.return_value = True
    blob.metadata = {"firebaseStorageDownloadTokens": "token-1"}
    bucket.blob.return_value = blob
    mocker.patch("app.services.meal_service.get_storage_bucket", return_value=bucket)

    payload = asyncio.run(
        meal_service.resolve_photo("user-1", meal_id="meal-1", image_id="image-1")
    )

    bucket.blob.assert_called_once_with("meals/user-1/image-1.jpg")
    assert payload == {
        "mealId": "meal-1",
        "imageId": "image-1",
        "storagePath": "meals/user-1/image-1.jpg",
        "photoUrl": (
            "https://firebasestorage.googleapis.com/v0/b/demo.appspot.com/o/"
            "meals%2Fuser-1%2Fimage-1.jpg?alt=media&token=token-1"
        ),
    }


def test_resolve_photo_returns_derived_path_with_photo_url_for_inconsistent_stored_path(
    mocker: MockerFixture,
) -> None:
    client = mocker.Mock()
    users_collection = mocker.Mock()
    user_ref = mocker.Mock()
    meals_collection = mocker.Mock()
    meal_ref = mocker.Mock()

    client.collection.return_value = users_collection
    users_collection.document.return_value = user_ref
    user_ref.collection.return_value = meals_collection
    meals_collection.document.return_value = meal_ref
    meal_ref.get.return_value = _build_snapshot(mocker, exists=True)
    mocker.patch("app.services.meal_service.get_firestore", return_value=client)
    get_storage_bucket = mocker.patch("app.services.meal_service.get_storage_bucket")
    mocker.patch(
        "app.services.meal_service._normalize_meal_snapshot",
        return_value={
            "imageRef": {
                "imageId": "image-1",
                "storagePath": "meals/user-1/custom-image.jpg",
                "downloadUrl": "https://cdn/meal.jpg",
            },
        },
    )

    payload = asyncio.run(
        meal_service.resolve_photo("user-1", meal_id="meal-1", image_id="image-1")
    )

    get_storage_bucket.assert_not_called()
    assert payload == {
        "mealId": "meal-1",
        "imageId": "image-1",
        "storagePath": "meals/user-1/image-1.jpg",
        "photoUrl": "https://cdn/meal.jpg",
    }


def test_resolve_photo_uses_user_scoped_storage_object_path(
    mocker: MockerFixture,
) -> None:
    bucket = mocker.Mock()
    bucket.name = "demo.appspot.com"
    blob = mocker.Mock()
    blob.exists.return_value = True
    blob.metadata = {"firebaseStorageDownloadTokens": "token-1"}
    bucket.blob.return_value = blob
    mocker.patch("app.services.meal_service.get_storage_bucket", return_value=bucket)

    payload = asyncio.run(meal_service.resolve_photo("user-1", image_id="image-1"))

    bucket.blob.assert_called_once_with("meals/user-1/image-1.jpg")
    blob.reload.assert_called_once_with()
    blob.patch.assert_not_called()
    assert payload == {
        "mealId": None,
        "imageId": "image-1",
        "storagePath": "meals/user-1/image-1.jpg",
        "photoUrl": (
            "https://firebasestorage.googleapis.com/v0/b/demo.appspot.com/o/"
            "meals%2Fuser-1%2Fimage-1.jpg?alt=media&token=token-1"
        ),
    }


class _FakeHistorySnapshot:
    def __init__(self, doc_id: str, data: dict[str, Any]) -> None:
        self.id = doc_id
        self._data = data

    def to_dict(self) -> dict[str, Any]:
        return self._data


class _FakeHistoryQuery:
    def __init__(
        self,
        *,
        snapshots: list[_FakeHistorySnapshot],
        failure: BaseException | None = None,
    ) -> None:
        self._snapshots = snapshots
        self._failure = failure
        self._limit: int | None = None
        self._start_after: list[str] | None = None
        self.order_by_fields: list[str] = []
        self.stream_called = False

    def where(self, *, filter: object):  # noqa: A002
        del filter
        return self

    def order_by(self, field: str, direction: object | None = None):
        del direction
        self.order_by_fields.append(field)
        return self

    def start_after(self, cursor: list[str]):
        self._start_after = cursor
        return self

    def limit(self, value: int):
        self._limit = value
        return self

    def stream(self):
        self.stream_called = True
        if self._failure is not None:
            raise self._failure
        items = self._snapshots
        if self._start_after and len(self._start_after) == 3:
            cursor_key = tuple(self._start_after)

            def sort_key(snapshot: _FakeHistorySnapshot) -> tuple[str, str, str]:
                data = snapshot.to_dict()
                logged_at = str(data.get("loggedAt") or data.get("timestamp") or "")
                return (
                    str(data.get("dayKey") or logged_at[:10]),
                    logged_at,
                    snapshot.id,
                )

            items = [snapshot for snapshot in items if sort_key(snapshot) < cursor_key]
        if self._limit is not None:
            items = items[: self._limit]
        return iter(items)


class _FakeHistoryMealsCollection:
    def __init__(
        self,
        *,
        indexed_query: _FakeHistoryQuery,
        root_order_query: _FakeHistoryQuery,
    ) -> None:
        self._indexed_query = indexed_query
        self._root_order_query = root_order_query

    def where(self, *, filter: object):  # noqa: A002
        del filter
        return self._indexed_query

    def order_by(self, field: str, direction: object | None = None):
        del field, direction
        return self._root_order_query


def _history_doc(
    *,
    meal_id: str,
    timestamp: str,
    deleted: bool,
    day_key: str | None = None,
) -> dict[str, Any]:
    return {
        "loggedAt": timestamp,
        "timestamp": timestamp,
        "dayKey": day_key or timestamp[:10],
        "type": "lunch",
        "ingredients": [],
        "createdAt": timestamp,
        "updatedAt": timestamp,
        "deleted": deleted,
        "totals": {"kcal": 300, "protein": 20, "carbs": 30, "fat": 10},
    }


class _FakeChangesSnapshot:
    def __init__(self, doc_id: str, data: dict[str, Any]) -> None:
        self.id = doc_id
        self._data = data

    def to_dict(self) -> dict[str, Any]:
        return self._data


class _FakeChangesQuery:
    def __init__(self, snapshots: list[_FakeChangesSnapshot]) -> None:
        self._snapshots = snapshots
        self._limit: int | None = None
        self._start_after: list[str] | None = None
        self.stream_called = False

    def order_by(self, field: str, direction: object | None = None):
        del field, direction
        return self

    def start_after(self, cursor: list[str]):
        self._start_after = cursor
        return self

    def where(self, *, filter: object):  # noqa: A002
        del filter
        return self

    def limit(self, value: int):
        self._limit = value
        return self

    def stream(self):
        self.stream_called = True
        items = sorted(
            self._snapshots,
            key=lambda snapshot: (str(snapshot.to_dict().get("updatedAt") or ""), snapshot.id),
        )
        if self._start_after:
            cursor_key = tuple(self._start_after)
            items = [
                snapshot
                for snapshot in items
                if (str(snapshot.to_dict().get("updatedAt") or ""), snapshot.id) > cursor_key
            ]
        if self._limit is not None:
            items = items[: self._limit]
        return iter(items)


def _changes_doc(
    *,
    timestamp: str,
    updated_at: str,
    deleted: bool = False,
) -> dict[str, Any]:
    return {
        "loggedAt": timestamp,
        "timestamp": timestamp,
        "dayKey": timestamp[:10],
        "type": "lunch",
        "ingredients": [],
        "createdAt": timestamp,
        "updatedAt": updated_at,
        "deleted": deleted,
        "totals": {"kcal": 300, "protein": 20, "carbs": 30, "fat": 10},
    }


def _wire_changes_collection(
    mocker: MockerFixture,
    query: _FakeChangesQuery,
) -> None:
    client = mocker.Mock()
    users_collection = mocker.Mock()
    user_ref = mocker.Mock()
    client.collection.return_value = users_collection
    users_collection.document.return_value = user_ref
    user_ref.collection.return_value = query
    mocker.patch("app.services.meal_service.get_firestore", return_value=client)


def test_list_changes_paginates_same_updated_at_without_duplicates_or_gaps(
    mocker: MockerFixture,
) -> None:
    same_updated_at = "2026-04-20T10:00:00.000Z"
    query = _FakeChangesQuery(
        [
            _FakeChangesSnapshot(
                "meal-c",
                _changes_doc(
                    timestamp="2026-04-20T09:00:00.000Z",
                    updated_at=same_updated_at,
                ),
            ),
            _FakeChangesSnapshot(
                "meal-a",
                _changes_doc(
                    timestamp="2026-04-20T07:00:00.000Z",
                    updated_at=same_updated_at,
                ),
            ),
            _FakeChangesSnapshot(
                "meal-b",
                _changes_doc(
                    timestamp="2026-04-20T08:00:00.000Z",
                    updated_at=same_updated_at,
                ),
            ),
            _FakeChangesSnapshot(
                "meal-d",
                _changes_doc(
                    timestamp="2026-04-20T11:00:00.000Z",
                    updated_at="2026-04-20T11:00:00.000Z",
                ),
            ),
        ]
    )
    _wire_changes_collection(mocker, query)

    page1, cursor1 = asyncio.run(meal_service.list_changes("user-1", limit_count=2))
    page2, cursor2 = asyncio.run(
        meal_service.list_changes("user-1", limit_count=2, after_cursor=cursor1)
    )

    ids = [item["cloudId"] for item in [*page1, *page2]]
    assert ids == ["meal-a", "meal-b", "meal-c", "meal-d"]
    assert len(ids) == len(set(ids))
    assert cursor1 == "2026-04-20T10:00:00.000Z|meal-b"
    assert cursor2 == "2026-04-20T11:00:00.000Z|meal-d"


def test_list_changes_returns_deleted_tombstones(
    mocker: MockerFixture,
) -> None:
    query = _FakeChangesQuery(
        [
            _FakeChangesSnapshot(
                "meal-deleted",
                _changes_doc(
                    timestamp="2026-04-20T09:00:00.000Z",
                    updated_at="2026-04-20T10:00:00.000Z",
                    deleted=True,
                ),
            ),
        ]
    )
    _wire_changes_collection(mocker, query)

    items, next_cursor = asyncio.run(meal_service.list_changes("user-1", limit_count=10))

    assert [item["cloudId"] for item in items] == ["meal-deleted"]
    assert items[0]["deleted"] is True
    assert next_cursor is None


def test_list_changes_derives_day_key_when_stored_day_key_is_invalid(
    mocker: MockerFixture,
) -> None:
    query = _FakeChangesQuery(
        [
            _FakeChangesSnapshot(
                "meal-legacy",
                {
                    **_changes_doc(
                        timestamp="2026-04-20T09:15:00.000Z",
                        updated_at="2026-04-20T10:00:00.000Z",
                    ),
                    "dayKey": "2026-04-20T09:15:00.000Z",
                },
            ),
        ]
    )
    _wire_changes_collection(mocker, query)

    items, next_cursor = asyncio.run(meal_service.list_changes("user-1", limit_count=10))

    assert [item["cloudId"] for item in items] == ["meal-legacy"]
    assert items[0]["dayKey"] == "2026-04-20"
    assert items[0]["_hasCanonicalDayKey"] is False
    assert next_cursor is None


def test_list_changes_rejects_invalid_cursor_without_fallback(
    mocker: MockerFixture,
) -> None:
    query = _FakeChangesQuery([])
    _wire_changes_collection(mocker, query)

    with pytest.raises(ValueError, match="Invalid cursor"):
        asyncio.run(
            meal_service.list_changes(
                "user-1",
                limit_count=2,
                after_cursor="2026-04-20T10:00:00.000Z",
            )
        )

    assert query.stream_called is False


def test_list_history_raises_firestore_error_on_missing_index_without_root_order_stream(
    mocker: MockerFixture,
) -> None:
    indexed_query = _FakeHistoryQuery(
        snapshots=[],
        failure=FailedPrecondition("The query requires an index."),
    )
    root_order_query = _FakeHistoryQuery(
        snapshots=[
            _FakeHistorySnapshot(
                "meal-2",
                _history_doc(
                    meal_id="meal-2",
                    timestamp="2026-04-18T08:00:00.000Z",
                    deleted=False,
                ),
            ),
            _FakeHistorySnapshot(
                "meal-1",
                _history_doc(
                    meal_id="meal-1",
                    timestamp="2026-04-18T07:00:00.000Z",
                    deleted=False,
                ),
            ),
        ]
    )
    meals_collection = _FakeHistoryMealsCollection(
        indexed_query=indexed_query,
        root_order_query=root_order_query,
    )

    client = mocker.Mock()
    users_collection = mocker.Mock()
    user_ref = mocker.Mock()
    client.collection.return_value = users_collection
    users_collection.document.return_value = user_ref
    user_ref.collection.return_value = meals_collection
    mocker.patch("app.services.meal_service.get_firestore", return_value=client)

    with pytest.raises(FirestoreServiceError):
        asyncio.run(meal_service.list_history("user-1", limit_count=2))

    assert indexed_query.stream_called is True
    assert root_order_query.stream_called is False


def test_list_history_logged_at_range_raises_firestore_error_on_missing_index_without_root_order_stream(
    mocker: MockerFixture,
) -> None:
    indexed_query = _FakeHistoryQuery(
        snapshots=[],
        failure=FailedPrecondition("The query requires an index."),
    )
    root_order_query = _FakeHistoryQuery(
        snapshots=[
            _FakeHistorySnapshot(
                "meal-1",
                _history_doc(
                    meal_id="meal-1",
                    timestamp="2026-04-18T07:00:00.000Z",
                    deleted=False,
                ),
            ),
        ],
    )
    meals_collection = _FakeHistoryMealsCollection(
        indexed_query=indexed_query,
        root_order_query=root_order_query,
    )

    client = mocker.Mock()
    users_collection = mocker.Mock()
    user_ref = mocker.Mock()
    client.collection.return_value = users_collection
    users_collection.document.return_value = user_ref
    user_ref.collection.return_value = meals_collection
    mocker.patch("app.services.meal_service.get_firestore", return_value=client)

    with pytest.raises(FirestoreServiceError):
        asyncio.run(
            meal_service.list_history(
                "user-1",
                limit_count=2,
                logged_at_start="2026-04-18T00:00:00.000Z",
                logged_at_end="2026-04-18T23:59:59.999Z",
            )
        )

    assert indexed_query.order_by_fields == ["loggedAt", "__name__"]
    assert indexed_query.stream_called is True
    assert root_order_query.stream_called is False


def test_list_history_filters_by_day_key_without_derived_logged_at_day(
    mocker: MockerFixture,
) -> None:
    indexed_query = _FakeHistoryQuery(
        snapshots=[
            _FakeHistorySnapshot(
                "meal-in-range",
                _history_doc(
                    meal_id="meal-in-range",
                    timestamp="2026-04-17T22:30:00.000Z",
                    day_key="2026-04-18",
                    deleted=False,
                ),
            ),
            _FakeHistorySnapshot(
                "meal-outside-range",
                _history_doc(
                    meal_id="meal-outside-range",
                    timestamp="2026-04-18T12:00:00.000Z",
                    day_key="2026-04-17",
                    deleted=False,
                ),
            ),
            _FakeHistorySnapshot(
                "meal-missing-day-key",
                {
                    "loggedAt": "2026-04-18T14:00:00.000Z",
                    "timestamp": "2026-04-18T14:00:00.000Z",
                    "type": "lunch",
                    "ingredients": [],
                    "createdAt": "2026-04-18T14:00:00.000Z",
                    "updatedAt": "2026-04-18T14:00:00.000Z",
                    "deleted": False,
                    "totals": {"kcal": 300, "protein": 20, "carbs": 30, "fat": 10},
                },
            ),
            _FakeHistorySnapshot(
                "meal-deleted",
                _history_doc(
                    meal_id="meal-deleted",
                    timestamp="2026-04-18T13:00:00.000Z",
                    day_key="2026-04-18",
                    deleted=True,
                ),
            ),
        ],
    )
    root_order_query = _FakeHistoryQuery(snapshots=[])
    meals_collection = _FakeHistoryMealsCollection(
        indexed_query=indexed_query,
        root_order_query=root_order_query,
    )

    client = mocker.Mock()
    users_collection = mocker.Mock()
    user_ref = mocker.Mock()
    client.collection.return_value = users_collection
    users_collection.document.return_value = user_ref
    user_ref.collection.return_value = meals_collection
    mocker.patch("app.services.meal_service.get_firestore", return_value=client)

    items, next_cursor = asyncio.run(
        meal_service.list_history(
            "user-1",
            limit_count=10,
            day_key_start="2026-04-18",
            day_key_end="2026-04-18",
        )
    )

    assert [item["cloudId"] for item in items] == ["meal-in-range"]
    assert items[0]["loggedAt"] == "2026-04-17T22:30:00.000Z"
    assert next_cursor is None


def test_upsert_meal_requires_explicit_day_key(mocker: MockerFixture) -> None:
    client = mocker.Mock()
    users_collection = mocker.Mock()
    user_ref = mocker.Mock()
    meals_collection = mocker.Mock()
    meal_ref = mocker.Mock()

    client.collection.return_value = users_collection
    users_collection.document.return_value = user_ref
    user_ref.collection.return_value = meals_collection
    meals_collection.document.return_value = meal_ref
    meal_ref.get.return_value = _build_snapshot(mocker, exists=False)
    mocker.patch("app.services.meal_service.get_firestore", return_value=client)
    mocker.patch("app.services.meal_service.streak_service.sync_streak_from_meals")

    with pytest.raises(ValueError, match="Missing dayKey"):
        asyncio.run(
            meal_service.upsert_meal(
                "user-1",
                {
                    "mealId": "meal-1",
                    "loggedAt": "2026-04-18T01:30:00.000Z",
                    "type": "snack",
                    "ingredients": [],
                    "clientMutationId": "mutation-missing-day-key",
                },
            )
        )

    meal_ref.set.assert_not_called()


def test_upsert_meal_rejects_invalid_day_key(mocker: MockerFixture) -> None:
    client = mocker.Mock()
    users_collection = mocker.Mock()
    user_ref = mocker.Mock()
    meals_collection = mocker.Mock()
    meal_ref = mocker.Mock()

    client.collection.return_value = users_collection
    users_collection.document.return_value = user_ref
    user_ref.collection.return_value = meals_collection
    meals_collection.document.return_value = meal_ref
    meal_ref.get.return_value = _build_snapshot(mocker, exists=False)
    mocker.patch("app.services.meal_service.get_firestore", return_value=client)

    with pytest.raises(ValueError, match="dayKey must use YYYY-MM-DD format"):
        asyncio.run(
            meal_service.upsert_meal(
                "user-1",
                {
                    "mealId": "meal-1",
                    "loggedAt": "2026-04-18T01:30:00.000Z",
                    "dayKey": "2026/04/18",
                    "type": "snack",
                    "ingredients": [],
                    "clientMutationId": "mutation-invalid-day-key",
                },
            )
        )

    meal_ref.set.assert_not_called()


def test_list_history_paginates_composite_cursor_without_duplicates_or_gaps(
    mocker: MockerFixture,
) -> None:
    snapshots = [
        _FakeHistorySnapshot(
            "meal-e",
            _history_doc(
                meal_id="meal-e",
                timestamp="2026-04-19T12:00:00.000Z",
                day_key="2026-04-19",
                deleted=False,
            ),
        ),
        _FakeHistorySnapshot(
            "meal-d",
            _history_doc(
                meal_id="meal-d",
                timestamp="2026-04-19T12:00:00.000Z",
                day_key="2026-04-19",
                deleted=False,
            ),
        ),
        _FakeHistorySnapshot(
            "meal-c",
            _history_doc(
                meal_id="meal-c",
                timestamp="2026-04-19T12:00:00.000Z",
                day_key="2026-04-19",
                deleted=False,
            ),
        ),
        _FakeHistorySnapshot(
            "meal-b",
            _history_doc(
                meal_id="meal-b",
                timestamp="2026-04-18T09:00:00.000Z",
                day_key="2026-04-18",
                deleted=False,
            ),
        ),
        _FakeHistorySnapshot(
            "meal-a",
            _history_doc(
                meal_id="meal-a",
                timestamp="2026-04-17T09:00:00.000Z",
                day_key="2026-04-17",
                deleted=False,
            ),
        ),
    ]
    indexed_query = _FakeHistoryQuery(snapshots=snapshots)
    root_order_query = _FakeHistoryQuery(snapshots=[])
    meals_collection = _FakeHistoryMealsCollection(
        indexed_query=indexed_query,
        root_order_query=root_order_query,
    )

    client = mocker.Mock()
    users_collection = mocker.Mock()
    user_ref = mocker.Mock()
    client.collection.return_value = users_collection
    users_collection.document.return_value = user_ref
    user_ref.collection.return_value = meals_collection
    mocker.patch("app.services.meal_service.get_firestore", return_value=client)

    page1, cursor1 = asyncio.run(meal_service.list_history("user-1", limit_count=2))
    page2, cursor2 = asyncio.run(
        meal_service.list_history("user-1", limit_count=2, before_cursor=cursor1)
    )
    page3, cursor3 = asyncio.run(
        meal_service.list_history("user-1", limit_count=2, before_cursor=cursor2)
    )

    ids = [item["cloudId"] for item in [*page1, *page2, *page3]]
    assert ids == ["meal-e", "meal-d", "meal-c", "meal-b", "meal-a"]
    assert len(ids) == len(set(ids))
    assert cursor1 == "2026-04-19|2026-04-19T12:00:00.000Z|meal-d"
    assert cursor2 == "2026-04-18|2026-04-18T09:00:00.000Z|meal-b"
    assert cursor3 is None


def test_list_history_raises_firestore_error_on_non_index_failed_precondition(
    mocker: MockerFixture,
) -> None:
    indexed_query = _FakeHistoryQuery(
        snapshots=[],
        failure=FailedPrecondition("Some other precondition failure."),
    )
    root_order_query = _FakeHistoryQuery(snapshots=[])
    meals_collection = _FakeHistoryMealsCollection(
        indexed_query=indexed_query,
        root_order_query=root_order_query,
    )

    client = mocker.Mock()
    users_collection = mocker.Mock()
    user_ref = mocker.Mock()
    client.collection.return_value = users_collection
    users_collection.document.return_value = user_ref
    user_ref.collection.return_value = meals_collection
    mocker.patch("app.services.meal_service.get_firestore", return_value=client)

    with pytest.raises(FirestoreServiceError):
        asyncio.run(meal_service.list_history("user-1", limit_count=5))

    assert indexed_query.stream_called is True
    assert root_order_query.stream_called is False
