import asyncio
import logging
from typing import Any

from google.api_core.exceptions import FailedPrecondition, GoogleAPICallError
import pytest
from pytest_mock import MockerFixture

from app.core.exceptions import FirestoreServiceError
from app.services import meal_effect_outbox_service, meal_service


@pytest.fixture(autouse=True)
def enable_smart_memory_meal_side_effect_flags(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(meal_service.settings, "SMART_MEMORY_CAPTURE_ENABLED", True)
    monkeypatch.setattr(meal_service.settings, "SMART_MEMORY_APPLY_ENABLED", True)
    monkeypatch.setattr(meal_service.settings, "PLANNED_MEALS_ENABLED", True)


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
        apply_transaction_set = getattr(document_ref, "__dict__", {}).get(
            "apply_transaction_set"
        )
        if callable(apply_transaction_set):
            apply_transaction_set(data, merge)


def _build_snapshot(
    mocker: MockerFixture,
    *,
    exists: bool,
    data: dict[str, object] | None = None,
) -> Any:
    snapshot = mocker.Mock()
    snapshot.exists = exists
    snapshot.id = str(
        (data or {}).get("eventId")
        or (data or {}).get("cloudId")
        or (data or {}).get("mealId")
        or "meal-1"
    )
    snapshot.to_dict.return_value = data or {}
    return snapshot


def _wire_meal_firestore_refs(
    mocker: MockerFixture,
    *,
    meal_snapshot: Any,
    mutation_snapshot: Any | None = None,
    planned_snapshot: Any | None = None,
    outbox_snapshots: dict[str, Any] | None = None,
) -> tuple[Any, Any, Any, FakeTransaction]:
    client = mocker.Mock()
    users_collection = mocker.Mock()
    user_ref = mocker.Mock()
    meals_collection = mocker.Mock()
    mutations_collection = mocker.Mock()
    planned_collection = mocker.Mock()
    outbox_collection = mocker.Mock()
    meal_ref = mocker.Mock()
    mutation_ref = mocker.Mock()
    planned_ref = mocker.Mock()
    transaction = FakeTransaction()
    outbox_refs: dict[str, object] = {}

    client.collection.return_value = users_collection
    client.transaction.return_value = transaction
    users_collection.document.return_value = user_ref

    def outbox_document_for_id(event_id: str) -> Any:
        if event_id not in outbox_refs:
            event_ref = mocker.Mock()
            event_ref.id = event_id
            initial_snapshot = (outbox_snapshots or {}).get(event_id)
            stored_data: dict[str, object] | None = None
            if initial_snapshot is not None and initial_snapshot.exists:
                stored_data = dict(initial_snapshot.to_dict() or {})

            def get_event_snapshot(*args: object, **kwargs: object) -> Any:
                if stored_data is None:
                    return _build_snapshot(mocker, exists=False)
                return _build_snapshot(
                    mocker,
                    exists=True,
                    data={**stored_data, "eventId": event_id},
                )

            def apply_transaction_set(
                data: dict[str, object],
                merge: bool | None = None,
            ) -> None:
                nonlocal stored_data
                if merge and stored_data is not None:
                    stored_data = {**stored_data, **data}
                else:
                    stored_data = dict(data)

            event_ref.get.side_effect = get_event_snapshot
            event_ref.apply_transaction_set = apply_transaction_set
            outbox_refs[event_id] = event_ref
        return outbox_refs[event_id]

    def collection_for_name(name: str) -> object:
        if name == "meals":
            return meals_collection
        if name == "mealMutationDedupe":
            return mutations_collection
        if name == "plannedMeals":
            return planned_collection
        if name == "mealEffectOutbox":
            return outbox_collection
        return mocker.Mock()

    user_ref.collection.side_effect = collection_for_name
    meals_collection.document.return_value = meal_ref
    mutations_collection.document.return_value = mutation_ref
    planned_collection.document.return_value = planned_ref
    outbox_collection.document.side_effect = outbox_document_for_id
    meal_ref.get.return_value = meal_snapshot
    mutation_ref.get.return_value = mutation_snapshot or _build_snapshot(mocker, exists=False)
    planned_ref.get.return_value = planned_snapshot or _build_snapshot(
        mocker,
        exists=False,
    )
    client.meal_effect_outbox_refs = outbox_refs
    client.planned_meal_ref = planned_ref
    return client, meal_ref, mutation_ref, transaction


def _primary_set_refs(transaction: FakeTransaction) -> list[object]:
    return [
        document_ref
        for document_ref, data, _merge in transaction.set_calls
        if not (
            isinstance(data, dict)
            and (
                "eventId" in data
                or "leaseToken" in data
                or "lastErrorCode" in data
            )
        )
    ]


def _outbox_set_events(transaction: FakeTransaction) -> list[dict[str, object]]:
    return [
        data
        for _document_ref, data, _merge in transaction.set_calls
        if isinstance(data, dict) and "eventId" in data
    ]


def _outbox_status_updates(
    transaction: FakeTransaction,
    event_ref: object,
) -> list[dict[str, object]]:
    return [
        data
        for document_ref, data, _merge in transaction.set_calls
        if document_ref is event_ref
        and isinstance(data, dict)
        and "eventId" not in data
    ]


def _last_outbox_status_update(
    transaction: FakeTransaction,
    event_ref: object,
) -> dict[str, object]:
    return _outbox_status_updates(transaction, event_ref)[-1]


def _wire_existing_outbox_event(
    mocker: MockerFixture,
    event: dict[str, object],
) -> tuple[Any, Any, FakeTransaction]:
    client, _meal_ref, _mutation_ref, transaction = _wire_meal_firestore_refs(
        mocker,
        meal_snapshot=_build_snapshot(mocker, exists=False),
        outbox_snapshots={
            str(event["eventId"]): _build_snapshot(
                mocker,
                exists=True,
                data=event,
            )
        },
    )
    event_ref = meal_effect_outbox_service.meal_effect_outbox_ref(
        client,
        str(event["ownerUserId"]),
        str(event["eventId"]),
    )
    return client, event_ref, transaction


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


def _base_planned_meal_document(
    overrides: dict[str, object] | None = None,
) -> dict[str, object]:
    return {
        "plannedMealId": "planned-1",
        "version": 2,
        "dateBucket": "2026-03-03",
        "timeBucket": "lunch",
        "sourceType": "manual",
        "sourceRef": None,
        "draftSnapshot": {
            "name": "Planned chicken",
            "type": "lunch",
            "ingredients": [
                {
                    "id": "ingredient-1",
                    "name": "Chicken",
                    "amount": 150,
                    "unit": "g",
                    "kcal": 200,
                    "protein": 30,
                    "fat": 5,
                    "carbs": 0,
                }
            ],
            "totals": {"kcal": 200, "protein": 30, "carbs": 0, "fat": 5},
            "notes": None,
            "tags": [],
        },
        "nutritionEstimate": {
            "state": "known",
            "totals": {"kcal": 200, "protein": 30, "carbs": 0, "fat": 5},
            "missingFields": [],
            "confidence": "medium",
        },
        "status": "planned",
        "createdAt": "2026-03-02T12:00:00.000Z",
        "updatedAt": "2026-03-02T12:00:00.000Z",
        "ownerUserId": "user-1",
        **(overrides or {}),
    }


def _planning_source_payload(
    overrides: dict[str, object] | None = None,
) -> dict[str, object]:
    return {
        "plannedMealId": "planned-1",
        "plannedMealVersion": 2,
        "sourceType": "manual",
        "sourceRef": None,
        "nutritionEstimateState": "known",
        "missingNutritionFields": [],
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


def test_upsert_meal_rejects_planning_source_when_planning_disabled_before_firestore(
    mocker: MockerFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(meal_service.settings, "PLANNED_MEALS_ENABLED", False)
    get_firestore = mocker.patch("app.services.meal_service.get_firestore")

    with pytest.raises(
        meal_service.MealPlanningSourceDisabledError,
        match="Planned Meals are temporarily disabled",
    ):
        asyncio.run(
            meal_service.upsert_meal(
                "user-1",
                _base_meal_payload(
                    {
                        "planningSource": _planning_source_payload(),
                        "clientMutationId": "mutation-upsert-planned-disabled",
                    }
                ),
            )
        )

    get_firestore.assert_not_called()


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


def test_normalize_meal_document_preserves_planning_source() -> None:
    _meal_id, document = meal_service.normalize_meal_document_payload(
        "user-1",
        _base_meal_payload(
            {
                "planningSource": {
                    "plannedMealId": "planned-1",
                    "plannedMealVersion": 2,
                    "sourceType": "manual",
                    "sourceRef": None,
                    "nutritionEstimateState": "unknown",
                    "missingNutritionFields": ["fat"],
                },
            }
        ),
    )

    assert document["planningSource"] == {
        "plannedMealId": "planned-1",
        "plannedMealVersion": 2,
        "sourceType": "manual",
        "sourceRef": None,
        "nutritionEstimateState": "unknown",
        "missingNutritionFields": ["fat"],
    }


def test_upsert_meal_rejects_unknown_planned_source_without_positive_nutrition(
    mocker: MockerFixture,
) -> None:
    get_firestore = mocker.patch("app.services.meal_service.get_firestore")

    with pytest.raises(
        ValueError,
        match="Planned meal source requires positive nutrition evidence",
    ):
        asyncio.run(
            meal_service.upsert_meal(
                "user-1",
                _base_meal_payload(
                    {
                        "cloudId": "meal-planned-empty-1",
                        "mealId": "meal-planned-empty-1",
                        "ingredients": [],
                        "totals": None,
                        "planningSource": {
                            "plannedMealId": "planned-1",
                            "plannedMealVersion": 2,
                            "sourceType": "manual",
                            "sourceRef": None,
                            "nutritionEstimateState": "unknown",
                            "missingNutritionFields": [
                                "kcal",
                                "protein",
                                "fat",
                                "carbs",
                            ],
                        },
                        "clientMutationId": "mutation-planned-empty",
                    }
                ),
            )
        )

    get_firestore.assert_not_called()


def test_upsert_meal_rejects_partial_planned_source_without_positive_nutrition(
    mocker: MockerFixture,
) -> None:
    get_firestore = mocker.patch("app.services.meal_service.get_firestore")

    with pytest.raises(
        ValueError,
        match="Planned meal source requires positive nutrition evidence",
    ):
        asyncio.run(
            meal_service.upsert_meal(
                "user-1",
                _base_meal_payload(
                    {
                        "cloudId": "meal-planned-partial-empty-1",
                        "mealId": "meal-planned-partial-empty-1",
                        "ingredients": [],
                        "totals": None,
                        "planningSource": {
                            "plannedMealId": "planned-1",
                            "plannedMealVersion": 2,
                            "sourceType": "manual",
                            "sourceRef": None,
                            "nutritionEstimateState": "partial",
                            "missingNutritionFields": [
                                "kcal",
                                "protein",
                                "fat",
                                "carbs",
                            ],
                        },
                        "clientMutationId": "mutation-planned-partial-empty",
                    }
                ),
            )
        )

    get_firestore.assert_not_called()


def test_upsert_meal_consumes_and_links_planned_meal_in_same_transaction(
    mocker: MockerFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(meal_service.settings, "SMART_MEMORY_CAPTURE_ENABLED", False)
    client, meal_ref, mutation_ref, transaction = _wire_meal_firestore_refs(
        mocker,
        meal_snapshot=_build_snapshot(mocker, exists=False),
        planned_snapshot=_build_snapshot(
            mocker,
            exists=True,
            data=_base_planned_meal_document(),
        ),
    )
    planned_ref = client.planned_meal_ref
    mocker.patch("app.services.meal_service.get_firestore", return_value=client)
    sync_streak = mocker.patch("app.services.meal_service.streak_service.sync_streak_from_meals")

    result = asyncio.run(
        meal_service.upsert_meal(
            "user-1",
            _base_meal_payload(
                {
                    "planningSource": _planning_source_payload(),
                    "clientMutationId": "mutation-upsert-planned-link",
                }
            ),
        )
    )

    assert result["id"] == "meal-1"
    assert result["planningSource"] == _planning_source_payload()
    assert _primary_set_refs(transaction) == [meal_ref, planned_ref, mutation_ref]
    planned_write = transaction.set_calls[1][1]
    assert planned_write["status"] == "converted_to_review"
    assert planned_write["version"] == 3
    assert planned_write["linkedMealId"] == "meal-1"
    assert planned_write["conversionClientMutationId"] == "mutation-upsert-planned-link"
    assert isinstance(planned_write["convertedAt"], str)
    assert planned_write["updatedAt"] == planned_write["convertedAt"]
    sync_streak.assert_called_once_with("user-1", reference_day_key="2026-03-03")


def test_upsert_meal_rejects_stale_planned_meal_version_without_writes(
    mocker: MockerFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(meal_service.settings, "SMART_MEMORY_CAPTURE_ENABLED", False)
    client, _meal_ref, _mutation_ref, transaction = _wire_meal_firestore_refs(
        mocker,
        meal_snapshot=_build_snapshot(mocker, exists=False),
        planned_snapshot=_build_snapshot(
            mocker,
            exists=True,
            data=_base_planned_meal_document({"version": 3}),
        ),
    )
    mocker.patch("app.services.meal_service.get_firestore", return_value=client)
    sync_streak = mocker.patch("app.services.meal_service.streak_service.sync_streak_from_meals")

    with pytest.raises(
        meal_service.MealPlanningSourceConflictError,
        match="Planned meal version conflict",
    ):
        asyncio.run(
            meal_service.upsert_meal(
                "user-1",
                _base_meal_payload(
                    {
                        "planningSource": _planning_source_payload(),
                        "clientMutationId": "mutation-upsert-planned-stale",
                    }
                ),
            )
        )

    assert transaction.set_calls == []
    sync_streak.assert_not_called()


@pytest.mark.parametrize(
    "planned_snapshot_data",
    [
        None,
        _base_planned_meal_document({"status": "deleted"}),
        _base_planned_meal_document({"status": "source_unavailable"}),
    ],
)
def test_upsert_meal_rejects_missing_or_unavailable_planned_meal_without_writes(
    mocker: MockerFixture,
    monkeypatch: pytest.MonkeyPatch,
    planned_snapshot_data: dict[str, object] | None,
) -> None:
    monkeypatch.setattr(meal_service.settings, "SMART_MEMORY_CAPTURE_ENABLED", False)
    client, _meal_ref, _mutation_ref, transaction = _wire_meal_firestore_refs(
        mocker,
        meal_snapshot=_build_snapshot(mocker, exists=False),
        planned_snapshot=_build_snapshot(
            mocker,
            exists=planned_snapshot_data is not None,
            data=planned_snapshot_data,
        ),
    )
    mocker.patch("app.services.meal_service.get_firestore", return_value=client)
    sync_streak = mocker.patch("app.services.meal_service.streak_service.sync_streak_from_meals")

    with pytest.raises(meal_service.MealPlanningSourceConflictError):
        asyncio.run(
            meal_service.upsert_meal(
                "user-1",
                _base_meal_payload(
                    {
                        "planningSource": _planning_source_payload(),
                        "clientMutationId": "mutation-upsert-planned-unavailable",
                    }
                ),
            )
        )

    assert transaction.set_calls == []
    sync_streak.assert_not_called()


def test_upsert_meal_blocks_duplicate_logged_meal_for_linked_plan(
    mocker: MockerFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(meal_service.settings, "SMART_MEMORY_CAPTURE_ENABLED", False)
    client, _meal_ref, _mutation_ref, transaction = _wire_meal_firestore_refs(
        mocker,
        meal_snapshot=_build_snapshot(mocker, exists=False),
        planned_snapshot=_build_snapshot(
            mocker,
            exists=True,
            data=_base_planned_meal_document(
                {
                    "status": "converted_to_review",
                    "version": 3,
                    "linkedMealId": "meal-1",
                    "convertedAt": "2026-03-03T12:31:00.000Z",
                    "conversionClientMutationId": "mutation-upsert-planned-first",
                }
            ),
        ),
    )
    mocker.patch("app.services.meal_service.get_firestore", return_value=client)

    with pytest.raises(
        meal_service.MealPlanningSourceConflictError,
        match="already linked",
    ):
        asyncio.run(
            meal_service.upsert_meal(
                "user-1",
                _base_meal_payload(
                    {
                        "cloudId": "meal-2",
                        "mealId": "meal-2",
                        "planningSource": _planning_source_payload(
                            {"plannedMealVersion": 3}
                        ),
                        "clientMutationId": "mutation-upsert-planned-second",
                    }
                ),
            )
        )

    assert transaction.set_calls == []


def test_upsert_meal_planned_retry_uses_meal_dedupe_without_second_plan_write(
    mocker: MockerFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(meal_service.settings, "SMART_MEMORY_CAPTURE_ENABLED", False)
    first_client, meal_ref, mutation_ref, first_transaction = _wire_meal_firestore_refs(
        mocker,
        meal_snapshot=_build_snapshot(mocker, exists=False),
        planned_snapshot=_build_snapshot(
            mocker,
            exists=True,
            data=_base_planned_meal_document(),
        ),
    )
    first_planned_ref = first_client.planned_meal_ref
    get_firestore = mocker.patch(
        "app.services.meal_service.get_firestore",
        return_value=first_client,
    )
    sync_streak = mocker.patch("app.services.meal_service.streak_service.sync_streak_from_meals")
    payload = _base_meal_payload(
        {
            "planningSource": _planning_source_payload(),
            "clientMutationId": "mutation-upsert-planned-replay",
        }
    )

    first_result = asyncio.run(meal_service.upsert_meal("user-1", payload))
    mutation_record = next(
        data
        for document_ref, data, _merge in first_transaction.set_calls
        if document_ref is mutation_ref
    )

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
    assert _primary_set_refs(first_transaction) == [
        meal_ref,
        first_planned_ref,
        mutation_ref,
    ]
    assert second_transaction.set_calls == []
    second_client.planned_meal_ref.get.assert_not_called()
    sync_streak.assert_called_once_with("user-1", reference_day_key="2026-03-03")


def test_mark_deleted_preserves_linked_planned_meal_without_reopening_it(
    mocker: MockerFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(meal_service.settings, "SMART_MEMORY_APPLY_ENABLED", False)
    client, meal_ref, mutation_ref, transaction = _wire_meal_firestore_refs(
        mocker,
        meal_snapshot=_build_snapshot(
            mocker,
            exists=True,
            data=_base_meal_payload(
                {
                    "planningSource": _planning_source_payload(),
                }
            ),
        ),
    )
    planned_ref = client.planned_meal_ref
    mocker.patch("app.services.meal_service.get_firestore", return_value=client)
    sync_streak = mocker.patch("app.services.meal_service.streak_service.sync_streak_from_meals")

    result = asyncio.run(
        meal_service.mark_deleted(
            "user-1",
            "meal-1",
            updated_at="2026-03-03T12:45:00.000Z",
            client_mutation_id="mutation-delete-linked-planned-meal",
        )
    )

    assert result["deleted"] is True
    assert result["planningSource"] == _planning_source_payload()
    assert _primary_set_refs(transaction) == [meal_ref, mutation_ref]
    planned_ref.get.assert_not_called()
    assert all(document_ref is not planned_ref for document_ref, _data, _merge in transaction.set_calls)
    sync_streak.assert_called_once_with("user-1", reference_day_key="2026-03-03")


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
    assert _primary_set_refs(transaction) == [mutation_ref]
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
    assert _primary_set_refs(transaction) == [meal_ref, mutation_ref]
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
    assert _primary_set_refs(transaction) == [meal_ref, mutation_ref]
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
    assert _primary_set_refs(transaction) == [meal_ref, mutation_ref]
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
    assert _primary_set_refs(transaction) == [meal_ref, mutation_ref]
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


def test_smart_memory_capture_read_uses_anchored_21_day_window_and_paginates(
    mocker: MockerFixture,
) -> None:
    page_one: list[dict[str, Any]] = [{"id": "meal-1", "dayKey": "2026-03-21"}]
    page_two: list[dict[str, Any]] = [{"id": "meal-2", "dayKey": "2026-03-01"}]
    list_history = mocker.patch(
        "app.services.meal_service.list_history",
        new=mocker.AsyncMock(
            side_effect=[
                (page_one, "cursor-page-2"),
                (page_two, None),
            ]
        ),
    )

    result = asyncio.run(
        meal_service._list_meal_snapshots_for_smart_memory_capture_window(
            "user-1",
            reference_day_key="2026-03-21",
        )
    )

    assert result == [*page_one, *page_two]
    assert list_history.await_args_list == [
        mocker.call(
            "user-1",
            limit_count=meal_service.SMART_MEMORY_TYPICAL_PORTION_CAPTURE_PAGE_SIZE,
            before_cursor=None,
            day_key_start="2026-03-01",
            day_key_end="2026-03-21",
        ),
        mocker.call(
            "user-1",
            limit_count=meal_service.SMART_MEMORY_TYPICAL_PORTION_CAPTURE_PAGE_SIZE,
            before_cursor="cursor-page-2",
            day_key_start="2026-03-01",
            day_key_end="2026-03-21",
        ),
    ]


def test_smart_memory_capture_window_excludes_22nd_and_31_day_old_meals(
    mocker: MockerFixture,
) -> None:
    records = [
        {"id": "day-0", "dayKey": "2026-03-21"},
        {"id": "day-20", "dayKey": "2026-03-01"},
        {"id": "day-21", "dayKey": "2026-02-28"},
        {"id": "day-30", "dayKey": "2026-02-19"},
    ]

    async def fake_list_history(
        user_id: str,
        *,
        limit_count: int,
        before_cursor: str | None = None,
        day_key_start: str | None = None,
        day_key_end: str | None = None,
        **kwargs: object,
    ) -> tuple[list[dict[str, Any]], str | None]:
        del limit_count, before_cursor, kwargs
        assert user_id == "user-1"
        assert day_key_start == "2026-03-01"
        assert day_key_end == "2026-03-21"
        return (
            [
                record
                for record in records
                if day_key_start <= str(record["dayKey"]) <= day_key_end
            ],
            None,
        )

    list_history = mocker.patch(
        "app.services.meal_service.list_history",
        new=mocker.AsyncMock(side_effect=fake_list_history),
    )

    result = asyncio.run(
        meal_service._list_meal_snapshots_for_smart_memory_capture_window(
            "user-1",
            reference_day_key="2026-03-21",
        )
    )

    assert [item["id"] for item in result] == ["day-0", "day-20"]
    assert list_history.await_count == 1
    list_history.assert_awaited_once_with(
        "user-1",
        limit_count=meal_service.SMART_MEMORY_TYPICAL_PORTION_CAPTURE_PAGE_SIZE,
        before_cursor=None,
        day_key_start="2026-03-01",
        day_key_end="2026-03-21",
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

    async def fake_capture_window_snapshots(
        user_id: str,
        *,
        reference_day_key: str,
    ) -> list[dict[str, Any]]:
        assert user_id == "user-1"
        assert reference_day_key == "2026-03-03"
        return recent_snapshots

    get_settings = mocker.patch(
        "app.services.meal_service.smart_memory_service.get_settings",
        new=mocker.AsyncMock(return_value={"enabled": True}),
    )
    filter_tombstones = mocker.patch(
        "app.services.meal_service.smart_memory_service."
        "filter_existing_tombstone_subject_keys",
        new=mocker.AsyncMock(return_value=["typical_portion:tombstoned-hash"]),
    )
    list_suppressed_subjects = mocker.patch(
        "app.services.meal_service.smart_memory_service.list_suppressed_subject_keys",
        new=mocker.AsyncMock(
            return_value=[
                "typical_portion:source-deleted-hash",
            ]
        ),
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
        assert suppressed_subject_keys == [
            "typical_portion:tombstoned-hash",
            "typical_portion:source-deleted-hash",
        ]
        assert _primary_set_refs(transaction) == [meal_ref, mutation_ref]
        call_order.append("capture")
        return object()

    mocker.patch(
        "app.services.meal_service.streak_service.sync_streak_from_meals",
        new=fake_sync_streak,
    )
    mocker.patch(
        "app.services.meal_service._list_meal_snapshots_for_smart_memory_capture_window",
        new=fake_capture_window_snapshots,
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
    filter_tombstones_args = filter_tombstones.await_args
    assert filter_tombstones_args is not None
    assert filter_tombstones_args.args[0] == "user-1"
    assert filter_tombstones_args.kwargs["limit_count"] == len(
        filter_tombstones_args.args[1]
    )
    list_suppressed_subjects.assert_awaited_once_with(
        "user-1",
        memory_type="typical_portion",
    )


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
        "app.services.meal_service._list_meal_snapshots_for_smart_memory_capture_window",
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
    list_suppressed_subjects = mocker.patch(
        "app.services.meal_service.smart_memory_service.list_suppressed_subject_keys",
        new=mocker.AsyncMock(return_value=[]),
    )
    recent_snapshot_read = mocker.patch(
        "app.services.meal_service._list_meal_snapshots_for_smart_memory_capture_window",
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
    list_suppressed_subjects.assert_not_awaited()
    recent_snapshot_read.assert_not_awaited()
    capture.assert_not_awaited()


def test_upsert_meal_skips_smart_memory_reads_and_writes_when_capture_flag_disabled(
    mocker: MockerFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(meal_service.settings, "SMART_MEMORY_CAPTURE_ENABLED", False)
    client, _meal_ref, _mutation_ref, _transaction = _wire_meal_firestore_refs(
        mocker,
        meal_snapshot=_build_snapshot(mocker, exists=False),
    )
    mocker.patch("app.services.meal_service.get_firestore", return_value=client)
    mocker.patch("app.services.meal_service.streak_service.sync_streak_from_meals")
    get_settings = mocker.patch(
        "app.services.meal_service.smart_memory_service.get_settings",
        new=mocker.AsyncMock(return_value={"enabled": True}),
    )
    list_suppressed_subjects = mocker.patch(
        "app.services.meal_service.smart_memory_service.list_suppressed_subject_keys",
        new=mocker.AsyncMock(return_value=[]),
    )
    recent_snapshot_read = mocker.patch(
        "app.services.meal_service._list_meal_snapshots_for_smart_memory_capture_window",
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
                "clientMutationId": "mutation-upsert-capture-flag-disabled",
            },
        )
    )

    assert result["id"] == "meal-1"
    get_settings.assert_not_awaited()
    list_suppressed_subjects.assert_not_awaited()
    recent_snapshot_read.assert_not_awaited()
    capture.assert_not_awaited()


def test_upsert_meal_leaves_streak_event_pending_after_failure(
    mocker: MockerFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(meal_service.settings, "SMART_MEMORY_CAPTURE_ENABLED", False)
    client, _meal_ref, _mutation_ref, transaction = _wire_meal_firestore_refs(
        mocker,
        meal_snapshot=_build_snapshot(mocker, exists=False),
    )
    mocker.patch("app.services.meal_service.get_firestore", return_value=client)
    sync_streak = mocker.patch(
        "app.services.meal_service.streak_service.sync_streak_from_meals",
        new=mocker.AsyncMock(side_effect=RuntimeError("streak failed")),
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
                "clientMutationId": "mutation-upsert-streak-failure",
            },
        )
    )

    assert result["id"] == "meal-1"
    sync_streak.assert_awaited_once_with("user-1", reference_day_key="2026-03-03")
    outbox_events = _outbox_set_events(transaction)
    assert [event["kind"] for event in outbox_events] == [
        meal_effect_outbox_service.KIND_MEAL_SAVED_STREAK_SYNC
    ]
    pending_event = outbox_events[0]
    event_ref = client.meal_effect_outbox_refs[pending_event["eventId"]]
    failure_update = _last_outbox_status_update(transaction, event_ref)
    assert failure_update["status"] == meal_effect_outbox_service.STATUS_PENDING
    assert failure_update["attemptCount"] == 1
    assert failure_update["lastErrorCode"] == "RuntimeError"
    assert failure_update["lastErrorMessage"] == "streak failed"
    assert failure_update["leaseToken"] is None


def test_upsert_meal_duplicate_retry_processes_pending_streak_event(
    mocker: MockerFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(meal_service.settings, "SMART_MEMORY_CAPTURE_ENABLED", False)
    first_client, _meal_ref, _mutation_ref, first_transaction = _wire_meal_firestore_refs(
        mocker,
        meal_snapshot=_build_snapshot(mocker, exists=False),
    )
    get_firestore = mocker.patch(
        "app.services.meal_service.get_firestore",
        return_value=first_client,
    )
    sync_streak = mocker.patch(
        "app.services.meal_service.streak_service.sync_streak_from_meals",
        new=mocker.AsyncMock(side_effect=[RuntimeError("streak failed"), None]),
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
        "clientMutationId": "mutation-upsert-streak-retry",
    }

    first_result = asyncio.run(meal_service.upsert_meal("user-1", payload))
    mutation_record = first_transaction.set_calls[1][1]
    streak_event = _outbox_set_events(first_transaction)[0]
    first_event_ref = first_client.meal_effect_outbox_refs[streak_event["eventId"]]
    failed_update = _last_outbox_status_update(first_transaction, first_event_ref)
    pending_streak_event: dict[str, object] = {
        **streak_event,
        **dict(failed_update),
        "nextAttemptAt": "2000-01-01T00:00:00.000Z",
    }

    second_client, second_meal_ref, second_mutation_ref, second_transaction = (
        _wire_meal_firestore_refs(
            mocker,
            meal_snapshot=_build_snapshot(mocker, exists=False),
            mutation_snapshot=_build_snapshot(
                mocker,
                exists=True,
                data=mutation_record,
            ),
            outbox_snapshots={
                str(streak_event["eventId"]): _build_snapshot(
                    mocker,
                    exists=True,
                    data=pending_streak_event,
                )
            },
        )
    )
    get_firestore.return_value = second_client

    second_result = asyncio.run(meal_service.upsert_meal("user-1", payload))

    assert first_result == second_result
    assert all(
        document_ref not in {second_meal_ref, second_mutation_ref}
        for document_ref, _data, _merge in second_transaction.set_calls
    )
    assert sync_streak.await_count == 2
    retry_event_ref = second_client.meal_effect_outbox_refs[streak_event["eventId"]]
    success_update = _last_outbox_status_update(second_transaction, retry_event_ref)
    assert success_update["status"] == meal_effect_outbox_service.STATUS_SUCCEEDED
    assert success_update["attemptCount"] == 2
    assert success_update["lastErrorCode"] is None


def test_upsert_meal_leaves_smart_memory_capture_event_pending_after_failure(
    mocker: MockerFixture,
    caplog: pytest.LogCaptureFixture,
) -> None:
    client, _meal_ref, _mutation_ref, transaction = _wire_meal_firestore_refs(
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
        "app.services.meal_service.smart_memory_service.list_suppressed_subject_keys",
        new=mocker.AsyncMock(return_value=[]),
    )
    mocker.patch(
        "app.services.meal_service._list_meal_snapshots_for_smart_memory_capture_window",
        new=mocker.AsyncMock(return_value=[{"id": "meal-1", "deleted": False}]),
    )
    mocker.patch(
        "app.services.meal_service.smart_memory_capture_service."
        "capture_typical_portion_candidate_from_meal_snapshots",
        new=mocker.AsyncMock(side_effect=RuntimeError("capture failed")),
    )

    with caplog.at_level(logging.WARNING, logger=meal_service.logger.name):
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
    outbox_events = _outbox_set_events(transaction)
    smart_memory_events = [
        event
        for event in outbox_events
        if event["kind"]
        == meal_effect_outbox_service.KIND_MEAL_SAVED_SMART_MEMORY_CAPTURE
    ]
    assert len(smart_memory_events) == 1
    pending_event = smart_memory_events[0]
    assert pending_event["status"] == meal_effect_outbox_service.STATUS_PENDING
    event_ref = client.meal_effect_outbox_refs[pending_event["eventId"]]
    failure_update = _last_outbox_status_update(transaction, event_ref)
    assert failure_update["status"] == meal_effect_outbox_service.STATUS_PENDING
    assert failure_update["attemptCount"] == 1
    assert failure_update["lastErrorCode"] == "RuntimeError"
    assert failure_update["lastErrorMessage"] == "capture failed"
    log_records = [
        record
        for record in caplog.records
        if record.message == "meal_effect_outbox.processing.failed"
    ]
    assert len(log_records) == 1
    assert getattr(log_records[0], "user_id") == "user-1"
    assert getattr(log_records[0], "event_id") == pending_event["eventId"]
    assert (
        getattr(log_records[0], "kind")
        == meal_effect_outbox_service.KIND_MEAL_SAVED_SMART_MEMORY_CAPTURE
    )
    assert log_records[0].exc_info is not None


def test_upsert_meal_duplicate_retry_processes_pending_smart_memory_capture(
    mocker: MockerFixture,
) -> None:
    first_client, _meal_ref, _mutation_ref, first_transaction = _wire_meal_firestore_refs(
        mocker,
        meal_snapshot=_build_snapshot(mocker, exists=False),
    )
    get_firestore = mocker.patch(
        "app.services.meal_service.get_firestore",
        return_value=first_client,
    )
    mocker.patch("app.services.meal_service.streak_service.sync_streak_from_meals")
    mocker.patch(
        "app.services.meal_service.smart_memory_service.get_settings",
        new=mocker.AsyncMock(return_value={"enabled": True}),
    )
    mocker.patch(
        "app.services.meal_service.smart_memory_service.list_suppressed_subject_keys",
        new=mocker.AsyncMock(return_value=[]),
    )
    capture_window_read = mocker.patch(
        "app.services.meal_service._list_meal_snapshots_for_smart_memory_capture_window",
        new=mocker.AsyncMock(return_value=[{"id": "meal-1", "deleted": False}]),
    )
    capture = mocker.patch(
        "app.services.meal_service.smart_memory_capture_service."
        "capture_typical_portion_candidate_from_meal_snapshots",
        new=mocker.AsyncMock(side_effect=[RuntimeError("capture failed"), None]),
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
        "clientMutationId": "mutation-upsert-memory-retry",
    }

    first_result = asyncio.run(meal_service.upsert_meal("user-1", payload))
    mutation_record = first_transaction.set_calls[1][1]
    memory_event = next(
        event
        for event in _outbox_set_events(first_transaction)
        if event["kind"]
        == meal_effect_outbox_service.KIND_MEAL_SAVED_SMART_MEMORY_CAPTURE
    )
    failed_update = first_client.meal_effect_outbox_refs[
        memory_event["eventId"]
    ]
    pending_memory_event: dict[str, object] = {
        **memory_event,
        **dict(_last_outbox_status_update(first_transaction, failed_update)),
        "nextAttemptAt": "2000-01-01T00:00:00.000Z",
    }

    second_client, second_meal_ref, second_mutation_ref, second_transaction = (
        _wire_meal_firestore_refs(
            mocker,
            meal_snapshot=_build_snapshot(mocker, exists=False),
            mutation_snapshot=_build_snapshot(
                mocker,
                exists=True,
                data=mutation_record,
            ),
            outbox_snapshots={
                str(memory_event["eventId"]): _build_snapshot(
                    mocker,
                    exists=True,
                    data=pending_memory_event,
                )
            },
        )
    )
    get_firestore.return_value = second_client

    second_result = asyncio.run(meal_service.upsert_meal("user-1", payload))

    assert first_result == second_result
    assert all(
        document_ref not in {second_meal_ref, second_mutation_ref}
        for document_ref, _data, _merge in second_transaction.set_calls
    )
    assert capture_window_read.await_args_list == [
        mocker.call("user-1", reference_day_key="2026-03-03"),
        mocker.call("user-1", reference_day_key="2026-03-03"),
    ]
    assert capture.await_count == 2
    retry_event_ref = second_client.meal_effect_outbox_refs[memory_event["eventId"]]
    success_update = _last_outbox_status_update(second_transaction, retry_event_ref)
    assert success_update["status"] == meal_effect_outbox_service.STATUS_SUCCEEDED
    assert success_update["attemptCount"] == 2
    assert success_update["lastErrorCode"] is None


def test_smart_memory_capture_replay_blocks_source_deleted_subject_reactivation(
    mocker: MockerFixture,
) -> None:
    event: dict[str, object] = {
        "eventId": "meal-effect-capture-source-deleted-subject",
        "ownerUserId": "user-1",
        "sourceEntityId": "meal-3",
        "sourceMutationId": "mutation-1",
        "kind": meal_effect_outbox_service.KIND_MEAL_SAVED_SMART_MEMORY_CAPTURE,
        "status": meal_effect_outbox_service.STATUS_PENDING,
        "attemptCount": 0,
        "nextAttemptAt": "2000-01-01T00:00:00.000Z",
        "referenceDayKey": "2026-03-03",
        "resultMeal": {"id": "meal-3", "dayKey": "2026-03-03", "deleted": False},
    }
    client, event_ref, transaction = _wire_existing_outbox_event(mocker, event)
    subject_key = (
        meal_service.smart_memory_capture_service.subject_suppression_key("oats")
    )
    meal_snapshots: list[dict[str, object]] = [
        {
            "id": f"meal-{index}",
            "dayKey": f"2026-03-0{index}",
            "updatedAt": f"2026-03-0{index}T12:30:00.000Z",
            "deleted": False,
            "ingredients": [
                {
                    "id": f"ingredient-{index}",
                    "name": "Oats",
                    "amount": 60,
                    "unit": "g",
                }
            ],
        }
        for index in range(1, 4)
    ]
    mocker.patch(
        "app.services.meal_service.smart_memory_service.get_settings",
        new=mocker.AsyncMock(return_value={"enabled": True}),
    )
    list_suppressed_subjects = mocker.patch(
        "app.services.meal_service.smart_memory_service.list_suppressed_subject_keys",
        new=mocker.AsyncMock(return_value=[subject_key]),
    )
    filter_tombstones = mocker.patch(
        "app.services.meal_service.smart_memory_service."
        "filter_existing_tombstone_subject_keys",
        new=mocker.AsyncMock(return_value=[]),
    )
    capture_window_read = mocker.patch(
        "app.services.meal_service._list_meal_snapshots_for_smart_memory_capture_window",
        new=mocker.AsyncMock(return_value=meal_snapshots),
    )
    upsert_candidate = mocker.patch(
        "app.services.smart_memory_capture_service.smart_memory_service.upsert_candidate",
        new=mocker.AsyncMock(),
    )

    result = asyncio.run(
        meal_service._process_meal_effect_outbox_event(
            {
                "event_ref": event_ref,
                "event": event,
                "client": client,
            }
        )
    )

    assert result == "succeeded"
    capture_window_read.assert_awaited_once_with(
        "user-1",
        reference_day_key="2026-03-03",
    )
    list_suppressed_subjects.assert_awaited_once_with(
        "user-1",
        memory_type="typical_portion",
    )
    filter_tombstones.assert_awaited_once()
    filter_tombstones_args = filter_tombstones.await_args
    assert filter_tombstones_args is not None
    assert filter_tombstones_args.args[0] == "user-1"
    assert filter_tombstones_args.kwargs["limit_count"] == len(
        filter_tombstones_args.args[1]
    )
    upsert_candidate.assert_not_awaited()
    success_update = _last_outbox_status_update(transaction, event_ref)
    assert success_update["status"] == meal_effect_outbox_service.STATUS_SUCCEEDED
    assert success_update["attemptCount"] == 1


def test_smart_memory_capture_event_disabled_flag_records_retryable_failure(
    mocker: MockerFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(meal_service.settings, "SMART_MEMORY_CAPTURE_ENABLED", False)
    event: dict[str, object] = {
        "eventId": "meal-effect-capture-disabled",
        "ownerUserId": "user-1",
        "sourceEntityId": "meal-1",
        "sourceMutationId": "mutation-1",
        "kind": meal_effect_outbox_service.KIND_MEAL_SAVED_SMART_MEMORY_CAPTURE,
        "status": meal_effect_outbox_service.STATUS_PENDING,
        "attemptCount": 0,
        "nextAttemptAt": "2000-01-01T00:00:00.000Z",
        "referenceDayKey": "2026-03-03",
        "resultMeal": {"id": "meal-1", "deleted": False},
    }
    client, event_ref, transaction = _wire_existing_outbox_event(mocker, event)
    get_settings = mocker.patch(
        "app.services.meal_service.smart_memory_service.get_settings",
        new=mocker.AsyncMock(return_value={"enabled": True}),
    )
    capture = mocker.patch(
        "app.services.meal_service.smart_memory_capture_service."
        "capture_typical_portion_candidate_from_meal_snapshots",
        new=mocker.AsyncMock(),
    )

    result = asyncio.run(
        meal_service._process_meal_effect_outbox_event(
            {
                "event_ref": event_ref,
                "event": event,
                "client": client,
            }
        )
    )

    assert result == "failed"
    get_settings.assert_not_awaited()
    capture.assert_not_awaited()
    update = _last_outbox_status_update(transaction, event_ref)
    assert update["status"] == meal_effect_outbox_service.STATUS_PENDING
    assert update["attemptCount"] == 1
    assert update["lastErrorCode"] == "MealEffectFeatureDisabledError"
    assert update["lastErrorMessage"] == "SMART_MEMORY_CAPTURE_ENABLED is disabled"


def test_smart_memory_source_delete_event_disabled_flag_records_retryable_failure(
    mocker: MockerFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(meal_service.settings, "SMART_MEMORY_APPLY_ENABLED", False)
    event: dict[str, object] = {
        "eventId": "meal-effect-source-delete-disabled",
        "ownerUserId": "user-1",
        "sourceEntityId": "meal-1",
        "sourceMutationId": "mutation-1",
        "kind": meal_effect_outbox_service.KIND_MEAL_DELETED_SMART_MEMORY_SOURCE_DELETE,
        "status": meal_effect_outbox_service.STATUS_PENDING,
        "attemptCount": 0,
        "nextAttemptAt": "2000-01-01T00:00:00.000Z",
        "referenceDayKey": "2026-03-03",
        "resultMeal": {"id": "meal-1", "deleted": True},
    }
    client, event_ref, transaction = _wire_existing_outbox_event(mocker, event)
    mark_sources_deleted = mocker.patch(
        "app.services.meal_service.smart_memory_service.mark_sources_deleted_by_source_hashes",
        new=mocker.AsyncMock(),
    )

    result = asyncio.run(
        meal_service._process_meal_effect_outbox_event(
            {
                "event_ref": event_ref,
                "event": event,
                "client": client,
            }
        )
    )

    assert result == "failed"
    mark_sources_deleted.assert_not_awaited()
    update = _last_outbox_status_update(transaction, event_ref)
    assert update["status"] == meal_effect_outbox_service.STATUS_PENDING
    assert update["attemptCount"] == 1
    assert update["lastErrorCode"] == "MealEffectFeatureDisabledError"
    assert update["lastErrorMessage"] == "SMART_MEMORY_APPLY_ENABLED is disabled"


def test_meal_effect_outbox_claim_prevents_second_worker_from_rerunning_effect(
    mocker: MockerFixture,
) -> None:
    event: dict[str, object] = {
        "eventId": "meal-effect-streak-concurrent",
        "ownerUserId": "user-1",
        "sourceEntityId": "meal-1",
        "sourceMutationId": "mutation-1",
        "kind": meal_effect_outbox_service.KIND_MEAL_SAVED_STREAK_SYNC,
        "status": meal_effect_outbox_service.STATUS_PENDING,
        "attemptCount": 0,
        "nextAttemptAt": "2000-01-01T00:00:00.000Z",
        "referenceDayKey": "2026-03-03",
        "resultMeal": {"id": "meal-1", "deleted": False},
    }
    client, event_ref, transaction = _wire_existing_outbox_event(mocker, event)
    sync_streak = mocker.patch(
        "app.services.meal_service.streak_service.sync_streak_from_meals",
        new=mocker.AsyncMock(),
    )
    entry: meal_service.MealEffectOutboxEvent = {
        "event_ref": event_ref,
        "event": event,
        "client": client,
    }

    first_result = asyncio.run(meal_service._process_meal_effect_outbox_event(entry))
    second_result = asyncio.run(meal_service._process_meal_effect_outbox_event(entry))

    assert first_result == "succeeded"
    assert second_result == "skipped"
    sync_streak.assert_awaited_once_with("user-1", reference_day_key="2026-03-03")
    success_update = _last_outbox_status_update(transaction, event_ref)
    assert success_update["status"] == meal_effect_outbox_service.STATUS_SUCCEEDED
    assert success_update["attemptCount"] == 1


def test_meal_effect_outbox_returns_status_update_failed_when_success_mark_fails(
    mocker: MockerFixture,
) -> None:
    event: dict[str, object] = {
        "eventId": "meal-effect-streak-status-update-fails",
        "ownerUserId": "user-1",
        "sourceEntityId": "meal-1",
        "sourceMutationId": "mutation-1",
        "kind": meal_effect_outbox_service.KIND_MEAL_SAVED_STREAK_SYNC,
        "status": meal_effect_outbox_service.STATUS_PENDING,
        "attemptCount": 0,
        "nextAttemptAt": "2000-01-01T00:00:00.000Z",
        "referenceDayKey": "2026-03-03",
        "resultMeal": {"id": "meal-1", "deleted": False},
    }
    client, event_ref, _transaction = _wire_existing_outbox_event(mocker, event)
    sync_streak = mocker.patch(
        "app.services.meal_service.streak_service.sync_streak_from_meals",
        new=mocker.AsyncMock(),
    )
    mark_succeeded = mocker.patch(
        "app.services.meal_service.meal_effect_outbox_service.mark_succeeded",
        side_effect=GoogleAPICallError("status write failed"),
    )

    result = asyncio.run(
        meal_service._process_meal_effect_outbox_event(
            {
                "event_ref": event_ref,
                "event": event,
                "client": client,
            }
        )
    )

    assert result == "status_update_failed"
    sync_streak.assert_awaited_once_with("user-1", reference_day_key="2026-03-03")
    mark_succeeded.assert_called_once()


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
        "app.services.meal_service.smart_memory_service.list_suppressed_subject_keys",
        new=mocker.AsyncMock(return_value=[]),
    )
    recent_snapshot_read = mocker.patch(
        "app.services.meal_service._list_meal_snapshots_for_smart_memory_capture_window",
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
    assert _primary_set_refs(first_transaction) == [meal_ref, mutation_ref]
    assert second_transaction.set_calls == []
    sync_streak.assert_called_once_with("user-1", reference_day_key="2026-03-03")
    recent_snapshot_read.assert_awaited_once_with(
        "user-1",
        reference_day_key="2026-03-03",
    )
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


def test_mark_deleted_skips_smart_memory_writes_when_apply_flag_disabled(
    mocker: MockerFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(meal_service.settings, "SMART_MEMORY_APPLY_ENABLED", False)
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
    sync_streak = mocker.patch(
        "app.services.meal_service.streak_service.sync_streak_from_meals"
    )
    mark_sources_deleted = mocker.patch(
        "app.services.meal_service.smart_memory_service.mark_sources_deleted_by_source_hashes",
        new=mocker.AsyncMock(return_value=1),
    )

    result = asyncio.run(
        meal_service.mark_deleted(
            "user-1",
            "meal-1",
            updated_at="2026-03-03T13:00:00.000Z",
            client_mutation_id="mutation-delete-memory-apply-disabled",
        )
    )

    assert result["deleted"] is True
    sync_streak.assert_called_once_with("user-1", reference_day_key="2026-03-03")
    mark_sources_deleted.assert_not_awaited()


def test_mark_deleted_leaves_smart_memory_source_delete_event_pending_after_failure(
    mocker: MockerFixture,
) -> None:
    client, _meal_ref, _mutation_ref, transaction = _wire_meal_firestore_refs(
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

    result = asyncio.run(
        meal_service.mark_deleted(
            "user-1",
            "meal-1",
            updated_at="2026-03-03T13:00:00.000Z",
            client_mutation_id="mutation-delete-memory-source-failure",
        )
    )

    assert result["deleted"] is True
    outbox_events = _outbox_set_events(transaction)
    memory_events = [
        event
        for event in outbox_events
        if event["kind"]
        == meal_effect_outbox_service.KIND_MEAL_DELETED_SMART_MEMORY_SOURCE_DELETE
    ]
    assert len(memory_events) == 1
    pending_event = memory_events[0]
    event_ref = client.meal_effect_outbox_refs[pending_event["eventId"]]
    failure_update = _last_outbox_status_update(transaction, event_ref)
    assert failure_update["status"] == meal_effect_outbox_service.STATUS_PENDING
    assert failure_update["attemptCount"] == 1
    assert failure_update["lastErrorCode"] == "RuntimeError"
    assert failure_update["lastErrorMessage"] == "source cascade failed"


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
    assert _primary_set_refs(transaction) == [mutation_ref]
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
    assert _primary_set_refs(first_transaction) == [meal_ref, mutation_ref]
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
