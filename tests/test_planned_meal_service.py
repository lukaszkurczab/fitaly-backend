from __future__ import annotations

import asyncio
import inspect
from typing import Any, cast

import pytest

from app.schemas.meal import MealIngredient, MealTotals
from app.schemas.planned_meals import (
    PlannedMealCreateRequest,
    PlannedMealDeleteRequest,
    PlannedMealDraftSnapshot,
    PlannedMealNutritionEstimate,
    PlannedMealUpdateRequest,
)
from app.services import planned_meal_service
from app.services.planned_meal_service import PlannedMealVersionConflictError


class FakeFirestoreDocumentReference:
    pass


class FakeSnapshot:
    def __init__(
        self,
        document_id: str,
        *,
        exists: bool,
        data: dict[str, Any] | None = None,
    ) -> None:
        self.id = document_id
        self.exists = exists
        self._data = data or {}

    def to_dict(self) -> dict[str, Any]:
        return dict(self._data)


class FakeDocumentRef:
    def __init__(self, document_id: str) -> None:
        self.id = document_id
        self.snapshot = FakeSnapshot(document_id, exists=False)

    def get(self, transaction: object | None = None) -> FakeSnapshot:
        return self.snapshot

    def set(self, data: dict[str, Any], merge: bool | None = None) -> None:
        self.snapshot = FakeSnapshot(self.id, exists=True, data=data)


class FakeCollectionRef:
    def __init__(self) -> None:
        self.documents: dict[str, FakeDocumentRef] = {}
        self.limit_calls: list[int] = []

    def document(self, document_id: str) -> FakeDocumentRef:
        if document_id not in self.documents:
            self.documents[document_id] = FakeDocumentRef(document_id)
        return self.documents[document_id]

    def limit(self, count: int) -> "FakeCollectionRef":
        self.limit_calls.append(count)
        return self

    def stream(self) -> list[FakeSnapshot]:
        return [document.snapshot for document in self.documents.values()]


class FakeUserRef(FakeFirestoreDocumentReference):
    def __init__(self) -> None:
        self.collections: dict[str, FakeCollectionRef] = {}
        self.collection_calls: list[str] = []

    def collection(self, name: str) -> FakeCollectionRef:
        self.collection_calls.append(name)
        self.collections.setdefault(name, FakeCollectionRef())
        return self.collections[name]


class FakeUsersCollection:
    def __init__(self, user_ref: FakeUserRef) -> None:
        self.user_ref = user_ref

    def document(self, document_id: str) -> FakeUserRef:
        return self.user_ref


class FakeClient:
    def __init__(self, user_ref: FakeUserRef) -> None:
        self.user_ref = user_ref

    def collection(self, name: str) -> FakeUsersCollection:
        assert name == "users"
        return FakeUsersCollection(self.user_ref)

    def transaction(self) -> "FakeTransaction":
        return FakeTransaction()


class FakeTransaction:
    def __init__(self) -> None:
        self._id = b"transaction-id"
        self._max_attempts = 1
        self._read_only = False

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
        document_ref: FakeDocumentRef,
        data: dict[str, Any],
        merge: bool | None = None,
    ) -> None:
        document_ref.set(data, merge=merge)


def _draft_snapshot(name: str = "Planned oats") -> PlannedMealDraftSnapshot:
    return PlannedMealDraftSnapshot(
        name=name,
        type="breakfast",
        ingredients=[
            MealIngredient(
                id="ingredient-1",
                name="Oats",
                amount=50,
                unit="g",
                kcal=180,
                protein=6,
                fat=3,
                carbs=32,
            )
        ],
        totals=MealTotals(kcal=180, protein=6, fat=3, carbs=32),
        notes=None,
        tags=[],
    )


def _known_estimate() -> PlannedMealNutritionEstimate:
    return PlannedMealNutritionEstimate(
        state="known",
        totals=MealTotals(kcal=180, protein=6, fat=3, carbs=32),
        missingFields=[],
        confidence="medium",
    )


def _create_request(
    *,
    client_mutation_id: str = "create-1",
    planned_meal_id: str = "planned-1",
    date_bucket: str = "2026-06-19",
) -> PlannedMealCreateRequest:
    return PlannedMealCreateRequest(
        clientMutationId=client_mutation_id,
        plannedMealId=planned_meal_id,
        dateBucket=date_bucket,
        timeBucket="breakfast",
        sourceType="manual",
        sourceRef=None,
        draftSnapshot=_draft_snapshot(),
        nutritionEstimate=_known_estimate(),
    )


def _patch_storage(monkeypatch: pytest.MonkeyPatch) -> FakeUserRef:
    user_ref = FakeUserRef()
    monkeypatch.setattr(
        planned_meal_service,
        "get_firestore",
        lambda: FakeClient(user_ref),
    )
    return user_ref


def test_create_list_update_delete_planned_meal_without_meal_history_write(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user_ref = _patch_storage(monkeypatch)

    created = asyncio.run(
        planned_meal_service.create_planned_meal_for_user(
            "user-1",
            _create_request(),
        )
    )

    assert created["applied"] is True
    assert created["item"].plannedMealId == "planned-1"
    assert created["item"].version == 1
    assert created["item"].status == "planned"
    assert "plannedMeals" in user_ref.collection_calls
    assert "meals" not in user_ref.collection_calls

    listed = asyncio.run(
        planned_meal_service.list_planned_meals_for_user(
            "user-1",
            start_date="2026-06-18",
            days=3,
        )
    )
    assert listed.queryEcho.returnedItems == 1
    assert listed.items[0].plannedMealId == "planned-1"

    updated = asyncio.run(
        planned_meal_service.update_planned_meal_for_user(
            "user-1",
            "planned-1",
            PlannedMealUpdateRequest(
                clientMutationId="update-1",
                expectedVersion=1,
                dateBucket="2026-06-20",
                timeBucket="lunch",
            ),
        )
    )
    assert updated["applied"] is True
    assert updated["item"].version == 2
    assert updated["item"].status == "rescheduled"
    assert updated["item"].dateBucket == "2026-06-20"

    deleted = asyncio.run(
        planned_meal_service.delete_planned_meal_for_user(
            "user-1",
            "planned-1",
            PlannedMealDeleteRequest(
                clientMutationId="delete-1",
                expectedVersion=2,
            ),
        )
    )
    assert deleted["applied"] is True
    assert deleted["item"].version == 3
    assert deleted["item"].status == "deleted"

    active = asyncio.run(
        planned_meal_service.list_planned_meals_for_user(
            "user-1",
            start_date="2026-06-18",
            days=3,
        )
    )
    assert active.items == []

    with_deleted = asyncio.run(
        planned_meal_service.list_planned_meals_for_user(
            "user-1",
            start_date="2026-06-18",
            days=3,
            include_deleted=True,
        )
    )
    assert with_deleted.items[0].status == "deleted"


def test_planned_meal_mutations_replay_by_client_mutation_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_storage(monkeypatch)
    request = _create_request()

    created = asyncio.run(
        planned_meal_service.create_planned_meal_for_user("user-1", request)
    )
    replay = asyncio.run(
        planned_meal_service.create_planned_meal_for_user("user-1", request)
    )

    assert replay["applied"] is False
    assert replay["item"] == created["item"]


def test_planned_meal_update_rejects_stale_version(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_storage(monkeypatch)
    asyncio.run(
        planned_meal_service.create_planned_meal_for_user(
            "user-1",
            _create_request(),
        )
    )

    with pytest.raises(PlannedMealVersionConflictError):
        asyncio.run(
            planned_meal_service.update_planned_meal_for_user(
                "user-1",
                "planned-1",
                PlannedMealUpdateRequest(
                    clientMutationId="update-1",
                    expectedVersion=2,
                    draftSnapshot=_draft_snapshot("Changed"),
                ),
            )
        )


def test_planned_meal_update_can_clear_optional_source_fields(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_storage(monkeypatch)
    asyncio.run(
        planned_meal_service.create_planned_meal_for_user(
            "user-1",
            _create_request(),
        )
    )

    updated = asyncio.run(
        planned_meal_service.update_planned_meal_for_user(
            "user-1",
            "planned-1",
            PlannedMealUpdateRequest(
                clientMutationId="update-1",
                expectedVersion=1,
                sourceRef=None,
                timeBucket=None,
            ),
        )
    )

    assert updated["item"].sourceRef is None
    assert updated["item"].timeBucket is None
    assert updated["item"].status == "rescheduled"


def test_planned_meal_update_rejects_null_required_fields() -> None:
    with pytest.raises(ValueError):
        PlannedMealUpdateRequest.model_validate(
            {
                "clientMutationId": "update-1",
                "expectedVersion": 1,
                "dateBucket": None,
            }
        )


def test_planned_meal_create_rejects_invalid_calendar_date(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_storage(monkeypatch)

    with pytest.raises(ValueError):
        asyncio.run(
            planned_meal_service.create_planned_meal_for_user(
                "user-1",
                _create_request(date_bucket="2026-99-99"),
            )
        )


def test_planned_meal_list_filters_after_reading_all_docs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user_ref = _patch_storage(monkeypatch)
    for index in range(120):
        asyncio.run(
            planned_meal_service.create_planned_meal_for_user(
                "user-1",
                _create_request(
                    client_mutation_id=f"create-old-{index}",
                    planned_meal_id=f"old-{index}",
                    date_bucket="2026-01-01",
                ),
            )
        )
    asyncio.run(
        planned_meal_service.create_planned_meal_for_user(
            "user-1",
            _create_request(
                client_mutation_id="create-current",
                planned_meal_id="current-1",
                date_bucket="2026-06-19",
            ),
        )
    )

    listed = asyncio.run(
        planned_meal_service.list_planned_meals_for_user(
            "user-1",
            start_date="2026-06-18",
            days=3,
        )
    )

    assert [item.plannedMealId for item in listed.items] == ["current-1"]
    planned_collection = user_ref.collections["plannedMeals"]
    assert planned_collection.limit_calls == []


def test_planned_meal_export_reads_all_items_and_dedupe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user_ref = _patch_storage(monkeypatch)
    for index in range(120):
        asyncio.run(
            planned_meal_service.create_planned_meal_for_user(
                "user-1",
                _create_request(
                    client_mutation_id=f"create-{index}",
                    planned_meal_id=f"planned-{index}",
                    date_bucket="2026-06-19",
                ),
            )
        )

    export = planned_meal_service.read_export(cast(Any, user_ref))

    assert len(export["items"]) == 120
    assert len(export["mutationDedupe"]) == 120
    assert user_ref.collections["plannedMeals"].limit_calls == []
    assert user_ref.collections["plannedMealMutationDedupe"].limit_calls == []


def test_planned_meal_unknown_and_partial_estimates_are_explicit() -> None:
    unknown = PlannedMealNutritionEstimate(
        state="unknown",
        totals=None,
        missingFields=["kcal", "protein", "fat", "carbs"],
        confidence=None,
    )
    partial = PlannedMealNutritionEstimate(
        state="partial",
        totals=MealTotals(kcal=200, protein=0, fat=8, carbs=20),
        missingFields=["protein"],
        confidence="low",
    )

    assert unknown.state == "unknown"
    assert unknown.totals is None
    assert partial.state == "partial"
    assert partial.missingFields == ["protein"]


def test_planned_meal_service_does_not_import_logged_meal_storage() -> None:
    service_source = inspect.getsource(planned_meal_service)
    source_names = set(planned_meal_service.__dict__)
    assert "MEALS_SUBCOLLECTION" not in source_names
    assert "meal_service" not in service_source
    assert ".delete(" not in service_source
