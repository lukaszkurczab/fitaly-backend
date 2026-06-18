import asyncio
from typing import Any, cast

import pytest
from google.api_core.exceptions import GoogleAPICallError
from pytest_mock import MockerFixture

from app.core.exceptions import FirestoreServiceError
from pydantic import ValidationError

from app.schemas.food_library import (
    IngredientProductCreateRequest,
    IngredientProductUpdateRequest,
)
from app.services import food_library_service


class FakeSnapshot:
    def __init__(self, document_id: str, data: dict[str, Any]) -> None:
        self.id = document_id
        self._data = data

    def to_dict(self) -> dict[str, Any]:
        return dict(self._data)


class FakeTransaction:
    def __init__(self, on_set: Any | None = None) -> None:
        self._id = b"transaction-id"
        self._max_attempts = 1
        self._read_only = False
        self.on_set = on_set
        self.set_calls: list[tuple[object, dict[str, Any], bool | None]] = []

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
        data: dict[str, Any],
        merge: bool | None = None,
    ) -> None:
        self.set_calls.append((document_ref, data, merge))
        if self.on_set is not None:
            self.on_set(document_ref, data, merge)


class FakeQuery:
    def __init__(self, snapshots: list[FakeSnapshot], collection_ref: "FakeCollectionRef") -> None:
        self.snapshots = snapshots
        self.collection_ref = collection_ref
        self.limit_count: int | None = None

    def limit(self, count: int) -> "FakeQuery":
        self.limit_count = count
        self.collection_ref.limit_calls.append(count)
        return self

    def stream(self) -> list[FakeSnapshot]:
        if self.collection_ref.stream_error is not None:
            raise self.collection_ref.stream_error
        if self.limit_count is None:
            return list(self.snapshots)
        return list(self.snapshots[: self.limit_count])


class FakeCollectionRef:
    def __init__(
        self,
        documents: list[dict[str, Any]],
        *,
        stream_error: Exception | None = None,
    ) -> None:
        self.snapshots = [
            FakeSnapshot(str(document["ingredientProductId"]), document)
            for document in documents
        ]
        self.stream_error = stream_error
        self.where_calls: list[tuple[str, str, str]] = []
        self.limit_calls: list[int] = []

    def where(self, *, filter: Any) -> FakeQuery:
        field_path = str(filter.field_path)
        op_string = str(filter.op_string)
        value = str(filter.value)
        self.where_calls.append((field_path, op_string, value))
        matching = [
            snapshot
            for snapshot in self.snapshots
            if value in cast(list[str], snapshot.to_dict().get("searchPrefixes") or [])
        ]
        return FakeQuery(matching, self)


class FakeUserRef:
    def __init__(self, ingredient_products: FakeCollectionRef) -> None:
        self.ingredient_products = ingredient_products

    def collection(self, name: str) -> FakeCollectionRef:
        assert name == "ingredientProducts"
        return self.ingredient_products


class FakeUsersCollection:
    def __init__(self, user_ref: FakeUserRef) -> None:
        self.user_ref = user_ref

    def document(self, document_id: str) -> FakeUserRef:
        assert document_id
        return self.user_ref


class FakeClient:
    def __init__(
        self,
        *,
        user_collection: FakeCollectionRef,
        global_collection: FakeCollectionRef,
    ) -> None:
        self.user_collection = user_collection
        self.global_collection = global_collection

    def collection(self, name: str) -> FakeUsersCollection | FakeCollectionRef:
        if name == "users":
            return FakeUsersCollection(FakeUserRef(self.user_collection))
        if name == "ingredientProducts":
            return self.global_collection
        raise AssertionError(f"Unexpected collection: {name}")


def _record(overrides: dict[str, Any] | None = None) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "ingredientProductId": "global-oats",
        "recordScope": "global_seed",
        "lifecycleState": "verified",
        "kind": "generic_ingredient",
        "displayName": "Owies gorski",
        "ingredientName": "Owies",
        "sourceAttribution": {
            "sourceType": "internal_seed",
            "sourceId": "seed-oats",
            "sourceName": "Fitaly seed",
        },
        "confidence": {
            "identity": "high",
            "nutrition": "high",
            "profile": "high",
        },
        "nutritionPer100": {
            "basis": "per_100g",
            "unit": "g",
            "kcal": 389,
            "protein": 16.9,
            "fat": 6.9,
            "carbs": 66.3,
        },
        "defaultServing": {"quantity": 50, "unit": "g"},
        "servingSizes": [
            {
                "servingSizeId": "default",
                "label": "50 g",
                "quantity": 50,
                "unit": "g",
            }
        ],
        "profileFlags": {
            "compatibilityStatus": "compatible",
            "dietaryFlags": [],
            "allergenFlags": [],
        },
        "dietaryFlags": [],
        "allergenFlags": [],
        "searchPrefixes": ["owies", "owsianka"],
        "createdAt": "2026-06-15T10:00:00.000Z",
        "updatedAt": "2026-06-15T10:00:00.000Z",
    }
    if overrides:
        payload.update(overrides)
    return payload


def _create_request(
    overrides: dict[str, Any] | None = None,
) -> IngredientProductCreateRequest:
    payload: dict[str, Any] = {
        "clientMutationId": "mutation-1",
        "ingredientProductId": "user-oats-1",
        "displayName": "Owsianka domowa",
        "kind": "generic_ingredient",
        "defaultServing": {"quantity": 50, "unit": "g"},
        "nutritionPer100": {
            "basis": "per_100g",
            "unit": "g",
            "kcal": 370,
            "protein": 13,
            "fat": 7,
            "carbs": 60,
        },
        "dietaryFlags": ["vegetarian"],
        "allergenFlags": ["wheat"],
    }
    if overrides:
        payload.update(overrides)
    return IngredientProductCreateRequest.model_validate(payload)


def _update_request(
    overrides: dict[str, Any] | None = None,
) -> IngredientProductUpdateRequest:
    payload: dict[str, Any] = {
        "clientMutationId": "update-mutation-1",
        "displayName": "Owsianka po edycji",
        "defaultServing": {"quantity": 60, "unit": "g"},
        "nutritionPer100": {
            "basis": "per_100g",
            "unit": "g",
            "kcal": 371,
            "protein": 14,
            "fat": 7,
            "carbs": 61,
        },
        "dietaryFlags": ["vegetarian"],
        "allergenFlags": [],
    }
    if overrides:
        payload.update(overrides)
    return IngredientProductUpdateRequest.model_validate(payload)


def test_search_caps_limit_and_orders_exact_user_before_verified_global(
    mocker: MockerFixture,
) -> None:
    user_collection = FakeCollectionRef(
        [
            _record(
                {
                    "ingredientProductId": "user-oats",
                    "recordScope": "user_scoped",
                    "ownerUserId": "user-1",
                    "displayName": "Owies",
                    "sourceAttribution": {
                        "sourceType": "user_created",
                        "sourceId": "user-oats",
                        "sourceName": "User created",
                    },
                }
            )
        ]
    )
    global_records = [
        _record({"ingredientProductId": f"global-oats-{index}", "displayName": f"Owies {index}"})
        for index in range(13)
    ]
    global_records.append(
        _record(
            {
                "ingredientProductId": "low-confidence-oats",
                "displayName": "Owies niski",
                "confidence": {
                    "identity": "high",
                    "nutrition": "low",
                    "profile": "high",
                },
            }
        )
    )
    global_collection = FakeCollectionRef(global_records)
    mocker.patch(
        "app.services.food_library_service.get_firestore",
        return_value=FakeClient(
            user_collection=user_collection,
            global_collection=global_collection,
        ),
    )

    response = asyncio.run(
        food_library_service.search_ingredient_products(
            "user-1",
            query=" Owies ",
            limit_count=99,
        )
    )

    assert response.queryEcho.limit == 12
    assert len(response.items) == 12
    assert response.items[0].ingredientProductId == "user-oats"
    assert response.items[0].rankingSignals == ["user_scoped", "exact_user"]
    assert "low-confidence-oats" not in [
        item.ingredientProductId for item in response.items[:3]
    ]
    assert user_collection.where_calls == [("searchPrefixes", "array_contains", "owies")]
    assert global_collection.where_calls == [("searchPrefixes", "array_contains", "owies")]
    assert user_collection.limit_calls == [12]
    assert global_collection.limit_calls == [12]


def test_create_user_ingredient_product_enforces_user_scope_and_candidate(
    mocker: MockerFixture,
) -> None:
    client = mocker.Mock()
    users_collection_ref = mocker.Mock()
    user_ref = mocker.Mock()
    ingredient_products_collection_ref = mocker.Mock()
    document_ref = mocker.Mock()
    document_ref.get.return_value.exists = False

    def collection_side_effect(name: str) -> Any:
        if name == "users":
            return users_collection_ref
        pytest.fail(f"Unexpected {name}")

    client.collection.side_effect = collection_side_effect
    users_collection_ref.document.return_value = user_ref
    user_ref.collection.return_value = ingredient_products_collection_ref
    ingredient_products_collection_ref.document.return_value = document_ref
    mocker.patch(
        "app.services.food_library_service.get_firestore",
        return_value=client,
    )

    row, updated = asyncio.run(
        food_library_service.create_user_ingredient_product(
            "user-1",
            _create_request(),
        )
    )

    assert updated is True
    users_collection_ref.document.assert_called_once_with("user-1")
    user_ref.collection.assert_called_once_with("ingredientProducts")
    ingredient_products_collection_ref.document.assert_called_once_with("user-oats-1")
    document_ref.set.assert_called_once()
    payload = document_ref.set.call_args.args[0]
    assert payload["recordScope"] == "user_scoped"
    assert payload["lifecycleState"] == "candidate"
    assert payload["ownerUserId"] == "user-1"
    assert payload["sourceAttribution"]["sourceType"] == "user_created"
    assert payload["ingredientName"] == "Owsianka domowa"
    assert payload["confidence"] == {
        "identity": "low",
        "nutrition": "low",
        "profile": "unknown",
    }
    assert "ow" in payload["searchPrefixes"]
    assert "owsianka" in payload["searchPrefixes"]
    assert row.ingredientProductId == "user-oats-1"
    assert row.recordScope == "user_scoped"
    assert row.lifecycleState == "candidate"
    assert row.ownerUserId == "user-1"
    assert "pending_user_record" in row.warningReasonCodes


def test_create_user_ingredient_product_is_idempotent_for_same_mutation(
    mocker: MockerFixture,
) -> None:
    client = mocker.Mock()
    users_collection_ref = mocker.Mock()
    user_ref = mocker.Mock()
    ingredient_products_collection_ref = mocker.Mock()
    document_ref = mocker.Mock()
    existing_snapshot = FakeSnapshot(
        "user-oats-1",
        _record(
            {
                "ingredientProductId": "user-oats-1",
                "recordScope": "user_scoped",
                "ownerUserId": "user-1",
                "lifecycleState": "candidate",
                "displayName": "Owsianka domowa",
                "sourceAttribution": {
                    "sourceType": "user_created",
                    "sourceId": "mutation-1",
                    "sourceName": "manual_entry",
                },
                "confidence": {
                    "identity": "low",
                    "nutrition": "low",
                    "profile": "unknown",
                },
                "creationClientMutationId": "mutation-1",
            }
        ),
    )
    existing_snapshot.exists = True  # type: ignore[attr-defined]
    document_ref.get.return_value = existing_snapshot

    client.collection.return_value = users_collection_ref
    users_collection_ref.document.return_value = user_ref
    user_ref.collection.return_value = ingredient_products_collection_ref
    ingredient_products_collection_ref.document.return_value = document_ref
    mocker.patch("app.services.food_library_service.get_firestore", return_value=client)

    row, updated = asyncio.run(
        food_library_service.create_user_ingredient_product(
            "user-1",
            _create_request(),
        )
    )

    assert updated is False
    assert row.ingredientProductId == "user-oats-1"
    document_ref.set.assert_not_called()


def test_create_user_ingredient_product_rejects_existing_different_mutation(
    mocker: MockerFixture,
) -> None:
    client = mocker.Mock()
    transaction = FakeTransaction()
    document_ref = mocker.Mock()
    existing_snapshot = FakeSnapshot(
        "user-oats-1",
        _record(
            {
                "ingredientProductId": "user-oats-1",
                "recordScope": "user_scoped",
                "ownerUserId": "user-1",
                "lifecycleState": "candidate",
                "creationClientMutationId": "other-mutation",
            }
        ),
    )
    existing_snapshot.exists = True  # type: ignore[attr-defined]
    document_ref.get.return_value = existing_snapshot
    client.transaction.return_value = transaction
    client.collection.return_value.document.return_value.collection.return_value.document.return_value = (
        document_ref
    )
    mocker.patch("app.services.food_library_service.get_firestore", return_value=client)

    with pytest.raises(food_library_service.IngredientProductMutationConflictError):
        asyncio.run(
            food_library_service.create_user_ingredient_product(
                "user-1",
                _create_request(),
            )
        )
    document_ref.set.assert_not_called()


def test_update_user_ingredient_product_updates_owned_user_record(
    mocker: MockerFixture,
) -> None:
    client = mocker.Mock()
    transaction = FakeTransaction()
    users_collection_ref = mocker.Mock()
    user_ref = mocker.Mock()
    ingredient_products_collection_ref = mocker.Mock()
    document_ref = mocker.Mock()
    existing_snapshot = FakeSnapshot(
        "user-oats-1",
        _record(
            {
                "ingredientProductId": "user-oats-1",
                "recordScope": "user_scoped",
                "ownerUserId": "user-1",
                "lifecycleState": "candidate",
                "displayName": "Owsianka domowa",
                "defaultServing": {"quantity": 50, "unit": "g"},
                "sourceAttribution": {
                    "sourceType": "user_created",
                    "sourceId": "mutation-1",
                    "sourceName": "manual_entry",
                },
                "confidence": {
                    "identity": "low",
                    "nutrition": "low",
                    "profile": "unknown",
                },
                "profileFlags": {
                    "compatibilityStatus": "unknown",
                    "dietaryFlags": [],
                    "allergenFlags": [],
                },
                "updatedAt": "2026-06-16T09:00:00.000Z",
            }
        ),
    )
    existing_snapshot.exists = True  # type: ignore[attr-defined]
    document_ref.get.return_value = existing_snapshot
    client.transaction.return_value = transaction
    client.collection.return_value = users_collection_ref
    users_collection_ref.document.return_value = user_ref
    user_ref.collection.return_value = ingredient_products_collection_ref
    ingredient_products_collection_ref.document.return_value = document_ref
    mocker.patch("app.services.food_library_service.get_firestore", return_value=client)

    row, updated = asyncio.run(
        food_library_service.update_user_ingredient_product(
            "user-1",
            ingredient_product_id="user-oats-1",
            request=_update_request({"ingredientName": "Owies edytowany"}),
        )
    )

    assert updated is True
    assert row.ingredientProductId == "user-oats-1"
    assert row.displayName == "Owsianka po edycji"
    assert row.defaultServing.quantity == 60
    assert row.nutritionPer100 is not None
    assert row.nutritionPer100.kcal == 371
    assert row.ingredientName == "Owies edytowany"
    assert row.ownerUserId == "user-1"
    assert len(transaction.set_calls) == 1
    assert transaction.set_calls[0][0] is document_ref
    payload = transaction.set_calls[0][1]
    assert payload["ingredientProductId"] == "user-oats-1"
    assert payload["recordScope"] == "user_scoped"
    assert payload["ownerUserId"] == "user-1"
    assert payload["displayName"] == "Owsianka po edycji"
    assert payload["defaultServing"] == {"quantity": 60.0, "unit": "g"}
    assert payload["nutritionPer100"]["kcal"] == 371.0
    assert payload["confidence"]["nutrition"] == "low"
    assert payload["profileFlags"]["dietaryFlags"] == ["vegetarian"]
    assert payload["updateClientMutationId"] == "update-mutation-1"
    history = payload["updateMutationHistory"]
    assert len(history) == 1
    assert history[0]["clientMutationId"] == "update-mutation-1"
    assert history[0]["fingerprintVersion"] == "ingredient_product_update_v1"
    assert len(history[0]["payloadFingerprint"]) == 64
    assert history[0]["updatedAt"] == payload["updatedAt"]
    assert payload["updatedAt"].endswith("Z")
    assert "ow" in payload["searchPrefixes"]
    assert "owsianka po edycji" in payload["searchPrefixes"]
    assert transaction.set_calls[0][2] is True


def test_update_user_ingredient_product_is_idempotent_for_same_mutation(
    mocker: MockerFixture,
) -> None:
    request = _update_request()
    client = mocker.Mock()
    transaction = FakeTransaction()
    document_ref = mocker.Mock()
    existing_snapshot = FakeSnapshot(
        "user-oats-1",
        _record(
            {
                "ingredientProductId": "user-oats-1",
                "recordScope": "user_scoped",
                "ownerUserId": "user-1",
                "lifecycleState": "candidate",
                "displayName": "Owsianka po edycji",
                "sourceAttribution": {
                    "sourceType": "user_created",
                    "sourceId": "mutation-1",
                    "sourceName": "manual_entry",
                },
                "confidence": {
                    "identity": "low",
                    "nutrition": "low",
                    "profile": "unknown",
                },
                "defaultServing": {"quantity": 60, "unit": "g"},
                "nutritionPer100": {
                    "basis": "per_100g",
                    "unit": "g",
                    "kcal": 371,
                    "protein": 14,
                    "fat": 7,
                    "carbs": 61,
                },
                "dietaryFlags": ["vegetarian"],
                "allergenFlags": [],
                "updateClientMutationId": "update-mutation-1",
                "updateMutationHistory": [
                    {
                        "clientMutationId": "update-mutation-1",
                        "payloadFingerprint": food_library_service._update_request_fingerprint(
                            request
                        ),
                        "fingerprintVersion": "ingredient_product_update_v1",
                        "updatedAt": "2026-06-16T09:00:00.000Z",
                    }
                ],
                "updatedAt": "2026-06-16T09:00:00.000Z",
            }
        ),
    )
    existing_snapshot.exists = True  # type: ignore[attr-defined]
    document_ref.get.return_value = existing_snapshot
    client.transaction.return_value = transaction
    client.collection.return_value.document.return_value.collection.return_value.document.return_value = (
        document_ref
    )
    mocker.patch("app.services.food_library_service.get_firestore", return_value=client)

    row, updated = asyncio.run(
        food_library_service.update_user_ingredient_product(
            "user-1",
            ingredient_product_id="user-oats-1",
            request=request,
        )
    )

    assert updated is False
    assert row.ingredientProductId == "user-oats-1"
    assert row.displayName == "Owsianka po edycji"
    assert transaction.set_calls == []


def test_update_user_ingredient_product_dedupes_late_retry_after_newer_update(
    mocker: MockerFixture,
) -> None:
    client = mocker.Mock()
    transaction = FakeTransaction()
    document_ref = mocker.Mock()
    stored_payload = _record(
        {
            "ingredientProductId": "user-oats-1",
            "recordScope": "user_scoped",
            "ownerUserId": "user-1",
            "lifecycleState": "candidate",
            "displayName": "Owsianka domowa",
            "sourceAttribution": {
                "sourceType": "user_created",
                "sourceId": "mutation-1",
                "sourceName": "manual_entry",
            },
            "confidence": {
                "identity": "low",
                "nutrition": "low",
                "profile": "unknown",
            },
            "profileFlags": {
                "compatibilityStatus": "unknown",
                "dietaryFlags": [],
                "allergenFlags": [],
            },
            "updatedAt": "2026-06-16T09:00:00.000Z",
        }
    )

    def get_snapshot(*_args: Any, **_kwargs: Any) -> FakeSnapshot:
        snapshot = FakeSnapshot("user-oats-1", dict(stored_payload))
        snapshot.exists = True  # type: ignore[attr-defined]
        return snapshot

    def set_payload(
        _document_ref: object,
        payload: dict[str, Any],
        merge: bool | None,
    ) -> None:
        assert merge is True
        stored_payload.update(payload)

    transaction = FakeTransaction(on_set=set_payload)
    document_ref.get.side_effect = get_snapshot
    client.transaction.return_value = transaction
    client.collection.return_value.document.return_value.collection.return_value.document.return_value = (
        document_ref
    )
    mocker.patch("app.services.food_library_service.get_firestore", return_value=client)
    mocker.patch(
        "app.services.food_library_service._utc_timestamp",
        side_effect=[
            "2026-06-16T09:05:00.000Z",
            "2026-06-16T09:10:00.000Z",
        ],
    )

    first_request = _update_request(
        {
            "clientMutationId": "update-mutation-1",
            "displayName": "Pierwsza edycja",
        }
    )
    second_request = _update_request(
        {
            "clientMutationId": "update-mutation-2",
            "displayName": "Druga edycja",
        }
    )

    _first_row, first_updated = asyncio.run(
        food_library_service.update_user_ingredient_product(
            "user-1",
            ingredient_product_id="user-oats-1",
            request=first_request,
        )
    )
    second_row, second_updated = asyncio.run(
        food_library_service.update_user_ingredient_product(
            "user-1",
            ingredient_product_id="user-oats-1",
            request=second_request,
        )
    )
    late_retry_row, late_retry_updated = asyncio.run(
        food_library_service.update_user_ingredient_product(
            "user-1",
            ingredient_product_id="user-oats-1",
            request=first_request,
        )
    )

    assert first_updated is True
    assert second_updated is True
    assert second_row.displayName == "Druga edycja"
    assert late_retry_updated is False
    assert late_retry_row.displayName == "Druga edycja"
    assert stored_payload["displayName"] == "Druga edycja"
    assert stored_payload["updatedAt"] == "2026-06-16T09:10:00.000Z"
    assert len(transaction.set_calls) == 2
    history = stored_payload["updateMutationHistory"]
    assert [entry["clientMutationId"] for entry in history] == [
        "update-mutation-2",
        "update-mutation-1",
    ]


def test_update_user_ingredient_product_rejects_same_mutation_with_different_payload(
    mocker: MockerFixture,
) -> None:
    client = mocker.Mock()
    transaction = FakeTransaction()
    document_ref = mocker.Mock()
    stored_payload = _record(
        {
            "ingredientProductId": "user-oats-1",
            "recordScope": "user_scoped",
            "ownerUserId": "user-1",
            "lifecycleState": "candidate",
            "displayName": "Owsianka domowa",
            "sourceAttribution": {
                "sourceType": "user_created",
                "sourceId": "mutation-1",
                "sourceName": "manual_entry",
            },
            "confidence": {
                "identity": "low",
                "nutrition": "low",
                "profile": "unknown",
            },
            "profileFlags": {
                "compatibilityStatus": "unknown",
                "dietaryFlags": [],
                "allergenFlags": [],
            },
            "updatedAt": "2026-06-16T09:00:00.000Z",
        }
    )

    def get_snapshot(*_args: Any, **_kwargs: Any) -> FakeSnapshot:
        snapshot = FakeSnapshot("user-oats-1", dict(stored_payload))
        snapshot.exists = True  # type: ignore[attr-defined]
        return snapshot

    def set_payload(
        _document_ref: object,
        payload: dict[str, Any],
        merge: bool | None,
    ) -> None:
        assert merge is True
        stored_payload.update(payload)

    transaction = FakeTransaction(on_set=set_payload)
    document_ref.get.side_effect = get_snapshot
    client.transaction.return_value = transaction
    client.collection.return_value.document.return_value.collection.return_value.document.return_value = (
        document_ref
    )
    mocker.patch("app.services.food_library_service.get_firestore", return_value=client)
    mocker.patch(
        "app.services.food_library_service._utc_timestamp",
        return_value="2026-06-16T09:05:00.000Z",
    )
    first_request = _update_request({"displayName": "Pierwsza edycja"})
    conflicting_request = _update_request({"displayName": "Inna edycja"})

    _row, updated = asyncio.run(
        food_library_service.update_user_ingredient_product(
            "user-1",
            ingredient_product_id="user-oats-1",
            request=first_request,
        )
    )

    assert updated is True
    with pytest.raises(food_library_service.IngredientProductMutationConflictError):
        asyncio.run(
            food_library_service.update_user_ingredient_product(
                "user-1",
                ingredient_product_id="user-oats-1",
                request=conflicting_request,
            )
        )

    assert len(transaction.set_calls) == 1
    assert stored_payload["displayName"] == "Pierwsza edycja"


def test_update_user_ingredient_product_bounds_mutation_history(
    mocker: MockerFixture,
) -> None:
    client = mocker.Mock()
    transaction = FakeTransaction()
    document_ref = mocker.Mock()
    existing_history = [
        {
            "clientMutationId": f"old-update-{index}",
            "payloadFingerprint": f"{index:064x}",
            "fingerprintVersion": "ingredient_product_update_v1",
            "updatedAt": f"2026-06-16T08:{index:02d}:00.000Z",
        }
        for index in range(food_library_service.UPDATE_MUTATION_HISTORY_LIMIT)
    ]
    existing_snapshot = FakeSnapshot(
        "user-oats-1",
        _record(
            {
                "ingredientProductId": "user-oats-1",
                "recordScope": "user_scoped",
                "ownerUserId": "user-1",
                "lifecycleState": "candidate",
                "displayName": "Owsianka domowa",
                "sourceAttribution": {
                    "sourceType": "user_created",
                    "sourceId": "mutation-1",
                    "sourceName": "manual_entry",
                },
                "confidence": {
                    "identity": "low",
                    "nutrition": "low",
                    "profile": "unknown",
                },
                "profileFlags": {
                    "compatibilityStatus": "unknown",
                    "dietaryFlags": [],
                    "allergenFlags": [],
                },
                "updateMutationHistory": existing_history,
                "updatedAt": "2026-06-16T09:00:00.000Z",
            }
        ),
    )
    existing_snapshot.exists = True  # type: ignore[attr-defined]
    document_ref.get.return_value = existing_snapshot
    client.transaction.return_value = transaction
    client.collection.return_value.document.return_value.collection.return_value.document.return_value = (
        document_ref
    )
    mocker.patch("app.services.food_library_service.get_firestore", return_value=client)
    mocker.patch(
        "app.services.food_library_service._utc_timestamp",
        return_value="2026-06-16T09:05:00.000Z",
    )

    _row, updated = asyncio.run(
        food_library_service.update_user_ingredient_product(
            "user-1",
            ingredient_product_id="user-oats-1",
            request=_update_request({"clientMutationId": "new-update"}),
        )
    )

    assert updated is True
    assert len(transaction.set_calls) == 1
    payload = transaction.set_calls[0][1]
    history = payload["updateMutationHistory"]
    assert len(history) == food_library_service.UPDATE_MUTATION_HISTORY_LIMIT
    assert history[0]["clientMutationId"] == "new-update"
    assert history[-1]["clientMutationId"] == "old-update-10"
    assert all(entry["clientMutationId"] != "old-update-11" for entry in history)


def test_update_user_ingredient_product_transaction_retry_preserves_history(
    mocker: MockerFixture,
) -> None:
    document_ref = mocker.Mock()
    first_request = _update_request(
        {
            "clientMutationId": "update-mutation-1",
            "displayName": "Pierwsza edycja",
        }
    )
    second_request = _update_request(
        {
            "clientMutationId": "update-mutation-2",
            "displayName": "Druga edycja",
        }
    )
    second_fingerprint = food_library_service._update_request_fingerprint(
        second_request
    )
    first_committed_payload = _record(
        {
            "ingredientProductId": "user-oats-1",
            "recordScope": "user_scoped",
            "ownerUserId": "user-1",
            "lifecycleState": "candidate",
            "displayName": "Pierwsza edycja",
            "sourceAttribution": {
                "sourceType": "user_created",
                "sourceId": "mutation-1",
                "sourceName": "manual_entry",
            },
            "confidence": {
                "identity": "low",
                "nutrition": "low",
                "profile": "unknown",
            },
            "profileFlags": {
                "compatibilityStatus": "unknown",
                "dietaryFlags": [],
                "allergenFlags": [],
            },
            "updateClientMutationId": "update-mutation-1",
            "updateMutationHistory": [
                {
                    "clientMutationId": "update-mutation-1",
                    "payloadFingerprint": food_library_service._update_request_fingerprint(
                        first_request
                    ),
                    "fingerprintVersion": "ingredient_product_update_v1",
                    "updatedAt": "2026-06-16T09:05:00.000Z",
                }
            ],
            "updatedAt": "2026-06-16T09:05:00.000Z",
        }
    )
    snapshot = FakeSnapshot("user-oats-1", first_committed_payload)
    snapshot.exists = True  # type: ignore[attr-defined]
    document_ref.get.return_value = snapshot
    transaction = FakeTransaction()
    mocker.patch(
        "app.services.food_library_service._utc_timestamp",
        return_value="2026-06-16T09:10:00.000Z",
    )

    row, updated = food_library_service._update_user_ingredient_product_transaction(
        cast(Any, transaction),
        document_ref=document_ref,
        user_id="user-1",
        product_id="user-oats-1",
        request=second_request,
        mutation_id="update-mutation-2",
        request_fingerprint=second_fingerprint,
    )

    assert updated is True
    assert row.displayName == "Druga edycja"
    assert len(transaction.set_calls) == 1
    payload = transaction.set_calls[0][1]
    assert [entry["clientMutationId"] for entry in payload["updateMutationHistory"]] == [
        "update-mutation-2",
        "update-mutation-1",
    ]


@pytest.mark.parametrize(
    "overrides",
    [
        {"ownerUserId": "other-user"},
        {"recordScope": "global_seed"},
        {"lifecycleState": "rejected"},
    ],
)
def test_update_user_ingredient_product_rejects_unowned_or_rejected_record(
    mocker: MockerFixture,
    overrides: dict[str, Any],
) -> None:
    client = mocker.Mock()
    transaction = FakeTransaction()
    document_ref = mocker.Mock()
    existing_snapshot = FakeSnapshot(
        "user-oats-1",
        _record(
            {
                "ingredientProductId": "user-oats-1",
                "recordScope": "user_scoped",
                "ownerUserId": "user-1",
                "lifecycleState": "candidate",
                "updatedAt": "2026-06-16T09:00:00.000Z",
                **overrides,
            }
        ),
    )
    existing_snapshot.exists = True  # type: ignore[attr-defined]
    document_ref.get.return_value = existing_snapshot
    client.transaction.return_value = transaction
    client.collection.return_value.document.return_value.collection.return_value.document.return_value = (
        document_ref
    )
    mocker.patch("app.services.food_library_service.get_firestore", return_value=client)

    with pytest.raises(food_library_service.IngredientProductNotFoundError):
        asyncio.run(
            food_library_service.update_user_ingredient_product(
                "user-1",
                ingredient_product_id="user-oats-1",
                request=_update_request(),
            )
        )
    assert transaction.set_calls == []


def test_update_user_ingredient_product_rejects_required_field_clear() -> None:
    with pytest.raises(ValidationError):
        _update_request({"displayName": None})


def test_update_user_ingredient_product_rejects_kind_specific_contract_before_write(
    mocker: MockerFixture,
) -> None:
    client = mocker.Mock()
    transaction = FakeTransaction()
    document_ref = mocker.Mock()
    existing_snapshot = FakeSnapshot(
        "user-oats-1",
        _record(
            {
                "ingredientProductId": "user-oats-1",
                "recordScope": "user_scoped",
                "ownerUserId": "user-1",
                "lifecycleState": "candidate",
                "kind": "generic_ingredient",
                "ingredientName": "Owies",
                "brandName": None,
                "updatedAt": "2026-06-16T09:00:00.000Z",
            }
        ),
    )
    existing_snapshot.exists = True  # type: ignore[attr-defined]
    document_ref.get.return_value = existing_snapshot
    client.transaction.return_value = transaction
    client.collection.return_value.document.return_value.collection.return_value.document.return_value = (
        document_ref
    )
    mocker.patch("app.services.food_library_service.get_firestore", return_value=client)

    with pytest.raises(food_library_service.IngredientProductInvalidUpdateError):
        asyncio.run(
            food_library_service.update_user_ingredient_product(
                "user-1",
                ingredient_product_id="user-oats-1",
                request=_update_request({"kind": "branded_product", "brandName": None}),
            )
        )
    assert transaction.set_calls == []


def test_update_user_ingredient_product_validates_merged_row_before_write(
    mocker: MockerFixture,
) -> None:
    client = mocker.Mock()
    transaction = FakeTransaction()
    document_ref = mocker.Mock()
    existing_payload = _record(
        {
            "ingredientProductId": "user-oats-1",
            "recordScope": "user_scoped",
            "ownerUserId": "user-1",
            "lifecycleState": "candidate",
            "defaultServing": None,
            "updatedAt": "2026-06-16T09:00:00.000Z",
        }
    )
    existing_snapshot = FakeSnapshot("user-oats-1", existing_payload)
    existing_snapshot.exists = True  # type: ignore[attr-defined]
    document_ref.get.return_value = existing_snapshot
    client.transaction.return_value = transaction
    client.collection.return_value.document.return_value.collection.return_value.document.return_value = (
        document_ref
    )
    mocker.patch("app.services.food_library_service.get_firestore", return_value=client)

    with pytest.raises(FirestoreServiceError):
        asyncio.run(
            food_library_service.update_user_ingredient_product(
                "user-1",
                ingredient_product_id="user-oats-1",
                request=IngredientProductUpdateRequest.model_validate(
                    {
                        "clientMutationId": "update-mutation-malformed",
                        "displayName": "Owsianka bez porcji",
                    }
                ),
            )
        )
    assert transaction.set_calls == []


def test_update_user_ingredient_product_rejects_path_like_document_id() -> None:
    with pytest.raises(ValueError):
        asyncio.run(
            food_library_service.update_user_ingredient_product(
                "user-1",
                ingredient_product_id="users/user-1/user-oats-1",
                request=_update_request(),
            )
        )


def test_delete_user_ingredient_product_marks_owned_record_rejected(
    mocker: MockerFixture,
) -> None:
    client = mocker.Mock()
    users_collection_ref = mocker.Mock()
    user_ref = mocker.Mock()
    ingredient_products_collection_ref = mocker.Mock()
    document_ref = mocker.Mock()
    existing_snapshot = FakeSnapshot(
        "user-oats-1",
        _record(
            {
                "ingredientProductId": "user-oats-1",
                "recordScope": "user_scoped",
                "ownerUserId": "user-1",
                "lifecycleState": "candidate",
                "updatedAt": "2026-06-16T09:00:00.000Z",
            }
        ),
    )
    existing_snapshot.exists = True  # type: ignore[attr-defined]
    document_ref.get.return_value = existing_snapshot
    client.collection.return_value = users_collection_ref
    users_collection_ref.document.return_value = user_ref
    user_ref.collection.return_value = ingredient_products_collection_ref
    ingredient_products_collection_ref.document.return_value = document_ref
    mocker.patch("app.services.food_library_service.get_firestore", return_value=client)

    product_id, updated_at, updated = asyncio.run(
        food_library_service.delete_user_ingredient_product(
            "user-1",
            ingredient_product_id="user-oats-1",
            client_mutation_id="delete-mutation-1",
        )
    )

    assert product_id == "user-oats-1"
    assert updated is True
    assert updated_at.endswith("Z")
    document_ref.set.assert_called_once()
    payload = document_ref.set.call_args.args[0]
    assert payload["ingredientProductId"] == "user-oats-1"
    assert payload["recordScope"] == "user_scoped"
    assert payload["ownerUserId"] == "user-1"
    assert payload["lifecycleState"] == "rejected"
    assert payload["deletionClientMutationId"] == "delete-mutation-1"
    assert payload["rejectionReason"] == "user_deleted"
    assert payload["updatedAt"] == updated_at
    assert document_ref.set.call_args.kwargs == {"merge": True}


def test_delete_user_ingredient_product_is_idempotent_when_already_rejected(
    mocker: MockerFixture,
) -> None:
    client = mocker.Mock()
    document_ref = mocker.Mock()
    existing_snapshot = FakeSnapshot(
        "user-oats-1",
        _record(
            {
                "ingredientProductId": "user-oats-1",
                "recordScope": "user_scoped",
                "ownerUserId": "user-1",
                "lifecycleState": "rejected",
                "deletionClientMutationId": "delete-mutation-1",
                "updatedAt": "2026-06-16T09:00:00.000Z",
            }
        ),
    )
    existing_snapshot.exists = True  # type: ignore[attr-defined]
    document_ref.get.return_value = existing_snapshot
    client.collection.return_value.document.return_value.collection.return_value.document.return_value = (
        document_ref
    )
    mocker.patch("app.services.food_library_service.get_firestore", return_value=client)

    product_id, updated_at, updated = asyncio.run(
        food_library_service.delete_user_ingredient_product(
            "user-1",
            ingredient_product_id="user-oats-1",
            client_mutation_id="delete-mutation-2",
        )
    )

    assert product_id == "user-oats-1"
    assert updated is False
    assert updated_at == "2026-06-16T09:00:00.000Z"
    document_ref.set.assert_not_called()


def test_delete_user_ingredient_product_rejects_missing_or_unowned_record(
    mocker: MockerFixture,
) -> None:
    client = mocker.Mock()
    document_ref = mocker.Mock()
    existing_snapshot = FakeSnapshot(
        "user-oats-1",
        _record(
            {
                "ingredientProductId": "user-oats-1",
                "recordScope": "user_scoped",
                "ownerUserId": "other-user",
                "lifecycleState": "candidate",
                "updatedAt": "2026-06-16T09:00:00.000Z",
            }
        ),
    )
    existing_snapshot.exists = True  # type: ignore[attr-defined]
    document_ref.get.return_value = existing_snapshot
    client.collection.return_value.document.return_value.collection.return_value.document.return_value = (
        document_ref
    )
    mocker.patch("app.services.food_library_service.get_firestore", return_value=client)

    with pytest.raises(food_library_service.IngredientProductNotFoundError):
        asyncio.run(
            food_library_service.delete_user_ingredient_product(
                "user-1",
                ingredient_product_id="user-oats-1",
                client_mutation_id="delete-mutation-1",
            )
        )
    document_ref.set.assert_not_called()


def test_pull_user_ingredient_products_reads_current_user_records_only(
    mocker: MockerFixture,
) -> None:
    client = mocker.Mock()
    users_collection_ref = mocker.Mock()
    user_ref = mocker.Mock()
    ingredient_products_collection_ref = mocker.Mock()
    where_query = mocker.Mock()
    ordered_by_updated_query = mocker.Mock()
    ordered_query = mocker.Mock()
    limited_query = mocker.Mock()
    limited_query.stream.return_value = [
        FakeSnapshot(
            "user-oats-1",
            _record(
                {
                    "ingredientProductId": "user-oats-1",
                    "recordScope": "user_scoped",
                    "ownerUserId": "user-1",
                    "lifecycleState": "candidate",
                    "displayName": "Owsianka domowa",
                    "sourceAttribution": {
                        "sourceType": "user_created",
                        "sourceId": "mutation-1",
                        "sourceName": "manual_entry",
                    },
                    "confidence": {
                        "identity": "low",
                        "nutrition": "low",
                        "profile": "unknown",
                    },
                    "updatedAt": "2026-06-16T10:00:00.000Z",
                }
            ),
        ),
        FakeSnapshot(
            "user-oats-2",
            _record(
                {
                    "ingredientProductId": "user-oats-2",
                    "recordScope": "user_scoped",
                    "ownerUserId": "user-1",
                    "lifecycleState": "candidate",
                    "updatedAt": "2026-06-16T11:00:00.000Z",
                }
            ),
        ),
    ]
    client.collection.return_value = users_collection_ref
    users_collection_ref.document.return_value = user_ref
    user_ref.collection.return_value = ingredient_products_collection_ref
    ingredient_products_collection_ref.where.return_value = where_query
    where_query.order_by.return_value = ordered_by_updated_query
    ordered_by_updated_query.order_by.return_value = ordered_query
    ordered_query.limit.return_value = limited_query
    mocker.patch("app.services.food_library_service.get_firestore", return_value=client)

    response = asyncio.run(
        food_library_service.pull_user_ingredient_products(
            "user-1",
            updated_after="2026-06-15T10:00:00.000Z",
            limit_count=999,
        )
    )

    assert [record.item.ingredientProductId for record in response.records] == [
        "user-oats-1",
        "user-oats-2",
    ]
    assert response.records[0].updatedAt == "2026-06-16T10:00:00.000Z"
    assert response.records[0].creationClientMutationId == "mutation-1"
    assert response.nextUpdatedAfter == "2026-06-16T11:00:00.000Z|user-oats-2"
    client.collection.assert_called_once_with("users")
    users_collection_ref.document.assert_called_once_with("user-1")
    user_ref.collection.assert_called_once_with("ingredientProducts")
    ingredient_products_collection_ref.where.assert_called_once()
    where_filter = ingredient_products_collection_ref.where.call_args.kwargs["filter"]
    assert where_filter.field_path == "updatedAt"
    assert where_filter.op_string == ">="
    assert where_filter.value == "2026-06-15T10:00:00.000Z"
    where_query.order_by.assert_called_once_with("updatedAt")
    ordered_by_updated_query.order_by.assert_called_once_with("ingredientProductId")
    ordered_query.limit.assert_called_once_with(250)


def test_pull_user_ingredient_products_uses_compound_cursor_without_skipping_equal_timestamp(
    mocker: MockerFixture,
) -> None:
    client = mocker.Mock()
    users_collection_ref = mocker.Mock()
    user_ref = mocker.Mock()
    ingredient_products_collection_ref = mocker.Mock()
    ordered_by_updated_query = mocker.Mock()
    ordered_query = mocker.Mock()
    cursor_query = mocker.Mock()
    limited_query = mocker.Mock()
    limited_query.stream.return_value = [
        FakeSnapshot(
            "user-oats-2",
            _record(
                {
                    "ingredientProductId": "user-oats-2",
                    "recordScope": "user_scoped",
                    "ownerUserId": "user-1",
                    "lifecycleState": "candidate",
                    "displayName": "Owsianka druga",
                    "sourceAttribution": {
                        "sourceType": "user_created",
                        "sourceId": "mutation-2",
                        "sourceName": "manual_entry",
                    },
                    "updatedAt": "2026-06-16T10:00:00.000Z",
                }
            ),
        )
    ]
    client.collection.return_value = users_collection_ref
    users_collection_ref.document.return_value = user_ref
    user_ref.collection.return_value = ingredient_products_collection_ref
    ingredient_products_collection_ref.order_by.return_value = ordered_by_updated_query
    ordered_by_updated_query.order_by.return_value = ordered_query
    ordered_query.start_after.return_value = cursor_query
    cursor_query.limit.return_value = limited_query
    mocker.patch("app.services.food_library_service.get_firestore", return_value=client)

    response = asyncio.run(
        food_library_service.pull_user_ingredient_products(
            "user-1",
            updated_after="2026-06-16T10:00:00.000Z|user-oats-1",
            limit_count=1,
        )
    )

    assert [record.item.ingredientProductId for record in response.records] == [
        "user-oats-2"
    ]
    assert response.nextUpdatedAfter == "2026-06-16T10:00:00.000Z|user-oats-2"
    ingredient_products_collection_ref.where.assert_not_called()
    ingredient_products_collection_ref.order_by.assert_called_once_with("updatedAt")
    ordered_by_updated_query.order_by.assert_called_once_with("ingredientProductId")
    ordered_query.start_after.assert_called_once_with(
        {
            "updatedAt": "2026-06-16T10:00:00.000Z",
            "ingredientProductId": "user-oats-1",
        }
    )
    cursor_query.limit.assert_called_once_with(1)


@pytest.mark.parametrize(
    ("document_id", "overrides"),
    [
        pytest.param(
            "other-user-oats",
            {
                "ingredientProductId": "other-user-oats",
                "recordScope": "user_scoped",
                "ownerUserId": "other-user",
                "lifecycleState": "candidate",
                "updatedAt": "2026-06-16T11:00:00.000Z",
            },
            id="owner-mismatch",
        ),
        pytest.param(
            "user-oats-missing-updated-at",
            {
                "ingredientProductId": "user-oats-missing-updated-at",
                "recordScope": "user_scoped",
                "ownerUserId": "user-1",
                "lifecycleState": "candidate",
                "updatedAt": "",
            },
            id="missing-updated-at",
        ),
        pytest.param(
            "user-oats-invalid-state",
            {
                "ingredientProductId": "user-oats-invalid-state",
                "recordScope": "user_scoped",
                "ownerUserId": "user-1",
                "lifecycleState": "archived",
                "updatedAt": "2026-06-16T11:00:00.000Z",
            },
            id="invalid-lifecycle-state",
        ),
    ],
)
def test_pull_user_ingredient_products_rejects_malformed_records(
    mocker: MockerFixture,
    document_id: str,
    overrides: dict[str, Any],
) -> None:
    client = mocker.Mock()
    users_collection_ref = mocker.Mock()
    user_ref = mocker.Mock()
    ingredient_products_collection_ref = mocker.Mock()
    ordered_by_updated_query = mocker.Mock()
    ordered_query = mocker.Mock()
    limited_query = mocker.Mock()
    limited_query.stream.return_value = [
        FakeSnapshot(document_id, _record(overrides)),
    ]
    client.collection.return_value = users_collection_ref
    users_collection_ref.document.return_value = user_ref
    user_ref.collection.return_value = ingredient_products_collection_ref
    ingredient_products_collection_ref.order_by.return_value = ordered_by_updated_query
    ordered_by_updated_query.order_by.return_value = ordered_query
    ordered_query.limit.return_value = limited_query
    mocker.patch("app.services.food_library_service.get_firestore", return_value=client)

    with pytest.raises(FirestoreServiceError):
        asyncio.run(
            food_library_service.pull_user_ingredient_products(
                "user-1",
                limit_count=10,
            )
        )


def test_pull_user_ingredient_products_returns_removed_records_and_advances_cursor(
    mocker: MockerFixture,
) -> None:
    client = mocker.Mock()
    users_collection_ref = mocker.Mock()
    user_ref = mocker.Mock()
    ingredient_products_collection_ref = mocker.Mock()
    ordered_by_updated_query = mocker.Mock()
    ordered_query = mocker.Mock()
    limited_query = mocker.Mock()
    limited_query.stream.return_value = [
        FakeSnapshot(
            "user-oats-rejected",
            _record(
                {
                    "ingredientProductId": "user-oats-rejected",
                    "recordScope": "user_scoped",
                    "ownerUserId": "user-1",
                    "lifecycleState": "rejected",
                    "updatedAt": "2026-06-16T12:00:00.000Z",
                }
            ),
        )
    ]
    client.collection.return_value = users_collection_ref
    users_collection_ref.document.return_value = user_ref
    user_ref.collection.return_value = ingredient_products_collection_ref
    ingredient_products_collection_ref.order_by.return_value = ordered_by_updated_query
    ordered_by_updated_query.order_by.return_value = ordered_query
    ordered_query.limit.return_value = limited_query
    mocker.patch("app.services.food_library_service.get_firestore", return_value=client)

    response = asyncio.run(
        food_library_service.pull_user_ingredient_products(
            "user-1",
            limit_count=10,
        )
    )

    assert response.records == []
    assert [
        removed.model_dump(mode="json") for removed in response.removedRecords
    ] == [
        {
            "ingredientProductId": "user-oats-rejected",
            "updatedAt": "2026-06-16T12:00:00.000Z",
            "removalReason": "rejected",
        }
    ]
    assert response.nextUpdatedAfter == "2026-06-16T12:00:00.000Z|user-oats-rejected"


def test_search_respects_scope_flags_without_cross_scope_fallback(
    mocker: MockerFixture,
) -> None:
    user_collection = FakeCollectionRef(
        [
            _record(
                {
                    "ingredientProductId": "user-oats",
                    "recordScope": "user_scoped",
                    "ownerUserId": "user-1",
                    "displayName": "Owies prywatny",
                    "sourceAttribution": {
                        "sourceType": "user_created",
                        "sourceId": "user-oats",
                        "sourceName": "User created",
                    },
                }
            )
        ]
    )
    global_collection = FakeCollectionRef(
        [_record({"ingredientProductId": "global-oats"})]
    )
    mocker.patch(
        "app.services.food_library_service.get_firestore",
        return_value=FakeClient(
            user_collection=user_collection,
            global_collection=global_collection,
        ),
    )

    global_only = asyncio.run(
        food_library_service.search_ingredient_products(
            "user-1",
            query="owies",
            include_user_scoped=False,
            include_global=True,
        )
    )

    assert [item.ingredientProductId for item in global_only.items] == ["global-oats"]
    assert global_only.queryEcho.includeUserScoped is False
    assert global_only.queryEcho.includeGlobal is True
    assert user_collection.where_calls == []
    assert global_collection.where_calls == [
        ("searchPrefixes", "array_contains", "owies")
    ]

    user_collection.where_calls.clear()
    global_collection.where_calls.clear()

    user_only = asyncio.run(
        food_library_service.search_ingredient_products(
            "user-1",
            query="owies",
            include_user_scoped=True,
            include_global=False,
        )
    )

    assert [item.ingredientProductId for item in user_only.items] == ["user-oats"]
    assert user_only.queryEcho.includeUserScoped is True
    assert user_only.queryEcho.includeGlobal is False
    assert user_collection.where_calls == [
        ("searchPrefixes", "array_contains", "owies")
    ]
    assert global_collection.where_calls == []

    user_collection.where_calls.clear()
    global_collection.where_calls.clear()

    disabled_scopes = asyncio.run(
        food_library_service.search_ingredient_products(
            "user-1",
            query="owies",
            include_user_scoped=False,
            include_global=False,
        )
    )

    assert disabled_scopes.items == []
    assert disabled_scopes.warnings == []
    assert disabled_scopes.queryEcho.includeUserScoped is False
    assert disabled_scopes.queryEcho.includeGlobal is False
    assert user_collection.where_calls == []
    assert global_collection.where_calls == []


def test_search_orders_profile_warnings_deterministically_without_medical_payload(
    mocker: MockerFixture,
) -> None:
    user_collection = FakeCollectionRef([])
    global_collection = FakeCollectionRef(
        [
            _record(
                {
                    "ingredientProductId": "profile-warning-oats",
                    "displayName": "Owies profil warning",
                    "profileFlags": {
                        "compatibilityStatus": "warning",
                        "dietaryFlags": ["vegetarian"],
                        "allergenFlags": ["wheat"],
                        "medicalConditionMatches": ["diabetes"],
                    },
                }
            ),
            _record(
                {
                    "ingredientProductId": "nutrition-low-oats",
                    "displayName": "Owies niski confidence",
                    "confidence": {
                        "identity": "high",
                        "nutrition": "low",
                        "profile": "high",
                    },
                    "profileFlags": {
                        "compatibilityStatus": "compatible",
                        "dietaryFlags": [],
                        "allergenFlags": [],
                    },
                }
            ),
            _record(
                {
                    "ingredientProductId": "profile-incompatible-oats",
                    "displayName": "Owies incompatible",
                    "profileFlags": {
                        "compatibilityStatus": "incompatible",
                        "dietaryFlags": [],
                        "allergenFlags": ["wheat"],
                        "medicalAdvice": "Do not eat this product.",
                    },
                }
            ),
            _record(
                {
                    "ingredientProductId": "clean-oats",
                    "displayName": "Owies czysty",
                }
            ),
        ]
    )
    mocker.patch(
        "app.services.food_library_service.get_firestore",
        return_value=FakeClient(
            user_collection=user_collection,
            global_collection=global_collection,
        ),
    )

    response = asyncio.run(
        food_library_service.search_ingredient_products("user-1", query="owies")
    )

    assert [item.ingredientProductId for item in response.items] == [
        "clean-oats",
        "profile-warning-oats",
        "nutrition-low-oats",
        "profile-incompatible-oats",
    ]
    warning_row = response.items[1]
    assert warning_row.profileCompatibility.status == "warning"
    assert warning_row.profileCompatibility.dietaryFlags == ["vegetarian"]
    assert warning_row.profileCompatibility.allergenFlags == ["wheat"]
    assert warning_row.warningReasonCodes == ["profile_warning"]
    assert warning_row.rankingSignals == [
        "exact_match",
        "verified_seed",
        "profile_warning",
    ]

    low_confidence_row = response.items[2]
    assert low_confidence_row.warningReasonCodes == ["nutrition_low_confidence"]
    assert low_confidence_row.rankingSignals == [
        "exact_match",
        "verified_seed",
        "nutrition_warning",
    ]

    incompatible_row = response.items[3]
    assert incompatible_row.profileCompatibility.status == "incompatible"
    assert incompatible_row.warningReasonCodes == ["profile_incompatible"]
    assert incompatible_row.rankingSignals == [
        "exact_match",
        "verified_seed",
        "profile_warning",
    ]

    serialized = response.model_dump(mode="json")
    assert "medicalConditionMatches" not in str(serialized)
    assert "medicalAdvice" not in str(serialized)
    assert "diabetes" not in str(serialized)
    assert "Do not eat" not in str(serialized)


def test_search_enforces_ownership_and_excludes_rejected_and_candidate_only(
    mocker: MockerFixture,
) -> None:
    user_collection = FakeCollectionRef(
        [
            _record(
                {
                    "ingredientProductId": "owned-candidate",
                    "recordScope": "user_scoped",
                    "ownerUserId": "user-1",
                    "lifecycleState": "candidate",
                    "displayName": "Owies prywatny",
                    "ingredientName": "Platki prywatne",
                    "sourceAttribution": {
                        "sourceType": "user_created",
                        "sourceId": "owned-candidate",
                        "sourceName": "User created",
                    },
                }
            ),
            _record(
                {
                    "ingredientProductId": "other-user-record",
                    "recordScope": "user_scoped",
                    "ownerUserId": "other-user",
                    "sourceAttribution": {
                        "sourceType": "user_created",
                        "sourceId": "other-user-record",
                        "sourceName": "User created",
                    },
                }
            ),
            _record(
                {
                    "ingredientProductId": "user-rejected",
                    "recordScope": "user_scoped",
                    "ownerUserId": "user-1",
                    "lifecycleState": "rejected",
                    "sourceAttribution": {
                        "sourceType": "user_created",
                        "sourceId": "user-rejected",
                        "sourceName": "User created",
                    },
                }
            ),
        ]
    )
    global_collection = FakeCollectionRef(
        [
            _record({"ingredientProductId": "global-verified"}),
            _record(
                {
                    "ingredientProductId": "barcode-candidate",
                    "sourceAttribution": {
                        "sourceType": "barcode_identity",
                        "sourceId": "barcode-1",
                        "sourceName": "Barcode candidate",
                    },
                }
            ),
            _record(
                {
                    "ingredientProductId": "runtime-ai-candidate",
                    "sourceAttribution": {
                        "sourceType": "runtime_ai_candidate",
                        "sourceId": "ai-candidate-1",
                        "sourceName": "Runtime AI candidate",
                    },
                }
            ),
            _record(
                {
                    "ingredientProductId": "global-rejected",
                    "lifecycleState": "rejected",
                }
            ),
        ]
    )
    mocker.patch(
        "app.services.food_library_service.get_firestore",
        return_value=FakeClient(
            user_collection=user_collection,
            global_collection=global_collection,
        ),
    )

    response = asyncio.run(
        food_library_service.search_ingredient_products("user-1", query="owies")
    )

    ids = [item.ingredientProductId for item in response.items]
    assert ids == ["global-verified", "owned-candidate"]
    assert "runtime-ai-candidate" not in ids
    owned = response.items[1]
    assert owned.ownerUserId == "user-1"
    assert "pending_user_record" in owned.warningReasonCodes
    assert response.warnings == ["source_candidate_only"]


def test_search_skips_rows_without_default_serving(
    mocker: MockerFixture,
) -> None:
    user_collection = FakeCollectionRef([])
    global_collection = FakeCollectionRef(
        [
            _record(
                {
                    "ingredientProductId": "missing-serving",
                    "defaultServing": None,
                }
            ),
            _record({"ingredientProductId": "usable-record"}),
        ]
    )
    mocker.patch(
        "app.services.food_library_service.get_firestore",
        return_value=FakeClient(
            user_collection=user_collection,
            global_collection=global_collection,
        ),
    )

    response = asyncio.run(
        food_library_service.search_ingredient_products("user-1", query="owies")
    )

    assert [item.ingredientProductId for item in response.items] == ["usable-record"]
    assert response.items[0].defaultServing.quantity == 50


def test_search_raises_firestore_service_error_when_firestore_query_fails(
    mocker: MockerFixture,
) -> None:
    user_collection = FakeCollectionRef([], stream_error=GoogleAPICallError("boom"))
    global_collection = FakeCollectionRef([])
    mocker.patch(
        "app.services.food_library_service.get_firestore",
        return_value=FakeClient(
            user_collection=user_collection,
            global_collection=global_collection,
        ),
    )

    with pytest.raises(FirestoreServiceError):
        asyncio.run(
            food_library_service.search_ingredient_products("user-1", query="owies")
        )


def test_search_maps_firestore_init_value_error_to_service_error(
    mocker: MockerFixture,
) -> None:
    mocker.patch(
        "app.services.food_library_service.get_firestore",
        side_effect=ValueError("Firebase credentials are not configured"),
    )

    with pytest.raises(FirestoreServiceError):
        asyncio.run(
            food_library_service.search_ingredient_products("user-1", query="owies")
        )
