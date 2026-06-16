import asyncio
from typing import Any, cast

import pytest
from google.api_core.exceptions import GoogleAPICallError
from pytest_mock import MockerFixture

from app.core.exceptions import FirestoreServiceError
from app.services import food_library_service


class FakeSnapshot:
    def __init__(self, document_id: str, data: dict[str, Any]) -> None:
        self.id = document_id
        self._data = data

    def to_dict(self) -> dict[str, Any]:
        return dict(self._data)


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
