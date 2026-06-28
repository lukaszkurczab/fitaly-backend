"""Firestore emulator evidence for Product/Ingredient user-created writes."""

from __future__ import annotations

import os
from typing import Any, cast
from uuid import uuid4

import pytest
from google.cloud import firestore
from pytest_mock import MockerFixture

from app.core.firestore_constants import (
    INGREDIENT_PRODUCTS_COLLECTION,
    INGREDIENT_PRODUCTS_SUBCOLLECTION,
    USERS_COLLECTION,
)
from app.schemas.food_library import (
    IngredientProductCreateRequest,
    IngredientProductUpdateRequest,
)
from app.services import food_library_service, user_account_service


pytestmark = pytest.mark.skipif(
    not os.getenv("FIRESTORE_EMULATOR_HOST"),
    reason="Firestore emulator is not configured.",
)


def _emulator_client() -> firestore.Client:
    project_id = os.getenv("FIREBASE_PROJECT_ID") or "demo-fitaly-local"
    database_id = os.getenv("FIRESTORE_DATABASE_ID") or "(default)"
    client_class = cast(Any, firestore.Client)
    return cast(firestore.Client, client_class(project=project_id, database=database_id))


def _export_payload(export_data: tuple[Any, ...]) -> dict[str, Any]:
    (
        _profile,
        _meals,
        _my_meals,
        _chat_messages,
        _chat_memory,
        _ai_runs,
        _notifications,
        _notification_prefs,
        _feedback,
        _meal_mutation_dedupe,
        ingredient_products,
        *_rest,
    ) = export_data
    return {"ingredientProducts": ingredient_products}


def _snapshot_update_time(snapshot: Any) -> object:
    return cast(object, getattr(snapshot, "update_time", None))


async def test_user_created_ingredient_product_writes_user_scope_and_exports(
    mocker: MockerFixture,
) -> None:
    client = _emulator_client()
    run_id = uuid4().hex
    user_id = f"food-library-create-current-{run_id}"
    product_id = f"user-oats-{run_id}"
    user_ref = client.collection(USERS_COLLECTION).document(user_id)
    user_product_ref = user_ref.collection(INGREDIENT_PRODUCTS_SUBCOLLECTION).document(
        product_id
    )
    global_product_ref = client.collection(INGREDIENT_PRODUCTS_COLLECTION).document(
        product_id
    )
    seeded_refs: list[firestore.DocumentReference] = [user_ref, user_product_ref]

    mocker.patch("app.services.food_library_service.get_firestore", return_value=client)
    mocker.patch("app.services.user_account_service.get_firestore", return_value=client)

    user_ref.set({"uid": user_id})

    try:
        request = IngredientProductCreateRequest.model_validate(
            {
                "clientMutationId": f"mutation-{run_id}",
                "ingredientProductId": product_id,
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
            }
        )

        created_row, created = await food_library_service.create_user_ingredient_product(
            user_id,
            request,
        )
        retried_row, retried = await food_library_service.create_user_ingredient_product(
            user_id,
            request,
        )

        assert created is True
        assert retried is False
        assert created_row.ingredientProductId == product_id
        assert retried_row.ingredientProductId == product_id
        assert created_row.recordScope == "user_scoped"
        assert created_row.lifecycleState == "candidate"
        assert created_row.ownerUserId == user_id
        assert "pending_user_record" in created_row.warningReasonCodes

        user_snapshot = user_product_ref.get()
        assert user_snapshot.exists is True
        user_payload = user_snapshot.to_dict() or {}
        assert user_payload["ownerUserId"] == user_id
        assert user_payload["recordScope"] == "user_scoped"
        assert user_payload["lifecycleState"] == "candidate"
        assert user_payload["sourceAttribution"]["sourceType"] == "user_created"
        assert global_product_ref.get().exists is False

        export = _export_payload(
            await user_account_service.get_user_export_data(user_id)
        )
        assert [item["id"] for item in export["ingredientProducts"]] == [product_id]
    finally:
        for document_ref in reversed(seeded_refs):
            document_ref.delete()


async def test_user_created_ingredient_product_update_writes_user_scope_and_pulls_record(
    mocker: MockerFixture,
) -> None:
    client = _emulator_client()
    run_id = uuid4().hex
    user_id = f"food-library-update-current-{run_id}"
    product_id = f"user-oats-update-{run_id}"
    create_mutation_id = f"create-mutation-{run_id}"
    update_mutation_id = f"update-mutation-{run_id}"
    second_update_mutation_id = f"update-mutation-second-{run_id}"
    created_at = "2026-06-16T09:00:00.000Z"
    first_updated_at = "2026-06-16T09:05:00.000Z"
    second_updated_at = "2026-06-16T09:10:00.000Z"
    user_ref = client.collection(USERS_COLLECTION).document(user_id)
    user_product_ref = user_ref.collection(INGREDIENT_PRODUCTS_SUBCOLLECTION).document(
        product_id
    )
    global_product_ref = client.collection(INGREDIENT_PRODUCTS_COLLECTION).document(
        product_id
    )
    seeded_refs: list[firestore.DocumentReference] = [user_ref, user_product_ref]

    mocker.patch("app.services.food_library_service.get_firestore", return_value=client)
    mocker.patch(
        "app.services.food_library_service._utc_timestamp",
        side_effect=[created_at, first_updated_at, second_updated_at],
    )

    user_ref.set({"uid": user_id})

    try:
        create_request = IngredientProductCreateRequest.model_validate(
            {
                "clientMutationId": create_mutation_id,
                "ingredientProductId": product_id,
                "displayName": "Owsianka domowa",
                "kind": "generic_ingredient",
                "ingredientName": "Owies domowy",
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
        )
        _created_row, created = await food_library_service.create_user_ingredient_product(
            user_id,
            create_request,
        )
        assert created is True

        update_request = IngredientProductUpdateRequest.model_validate(
            {
                "clientMutationId": update_mutation_id,
                "displayName": "Kasza jaglana po edycji",
                "ingredientName": "Kasza jaglana gotowana",
                "category": "Zboza",
                "defaultServing": {"quantity": 75, "unit": "g"},
                "nutritionPer100": {
                    "basis": "per_100g",
                    "unit": "g",
                    "kcal": 119,
                    "protein": 3.5,
                    "fat": 1,
                    "carbs": 23.7,
                },
                "dietaryFlags": ["vegetarian", "gluten_free"],
                "allergenFlags": [],
            }
        )

        updated_row, updated = await food_library_service.update_user_ingredient_product(
            user_id,
            ingredient_product_id=product_id,
            request=update_request,
        )

        assert updated is True
        assert updated_row.ingredientProductId == product_id
        assert updated_row.displayName == "Kasza jaglana po edycji"
        assert updated_row.defaultServing.quantity == 75
        assert updated_row.nutritionPer100 is not None
        assert updated_row.nutritionPer100.kcal == 119
        assert updated_row.ingredientName == "Kasza jaglana gotowana"
        assert updated_row.category == "Zboza"
        assert updated_row.ownerUserId == user_id
        assert updated_row.recordScope == "user_scoped"

        updated_snapshot = user_product_ref.get()
        assert updated_snapshot.exists is True
        updated_payload = updated_snapshot.to_dict() or {}
        first_update_time = _snapshot_update_time(updated_snapshot)
        assert first_update_time is not None
        search_prefixes = cast(list[str], updated_payload["searchPrefixes"])
        assert updated_payload["ingredientProductId"] == product_id
        assert updated_payload["recordScope"] == "user_scoped"
        assert updated_payload["ownerUserId"] == user_id
        assert updated_payload["lifecycleState"] == "candidate"
        assert updated_payload["sourceAttribution"] == {
            "sourceType": "user_created",
            "sourceId": create_mutation_id,
            "sourceName": "manual_entry",
            "observedAt": created_at,
        }
        assert updated_payload["creationClientMutationId"] == create_mutation_id
        assert updated_payload["displayName"] == "Kasza jaglana po edycji"
        assert updated_payload["ingredientName"] == "Kasza jaglana gotowana"
        assert updated_payload["category"] == "Zboza"
        assert updated_payload["defaultServing"] == {"quantity": 75.0, "unit": "g"}
        assert updated_payload["nutritionPer100"]["kcal"] == 119.0
        assert updated_payload["nutritionPer100"]["protein"] == 3.5
        assert updated_payload["nutritionPer100"]["fat"] == 1.0
        assert updated_payload["nutritionPer100"]["carbs"] == 23.7
        assert updated_payload["confidence"]["nutrition"] == "low"
        assert updated_payload["dietaryFlags"] == ["vegetarian", "gluten_free"]
        assert updated_payload["allergenFlags"] == []
        assert updated_payload["profileFlags"] == {
            "compatibilityStatus": "unknown",
            "dietaryFlags": ["vegetarian", "gluten_free"],
            "allergenFlags": [],
        }
        assert updated_payload["updateClientMutationId"] == update_mutation_id
        assert updated_payload["createdAt"] == created_at
        assert updated_payload["updatedAt"] == first_updated_at
        update_history = cast(list[dict[str, object]], updated_payload["updateMutationHistory"])
        assert len(update_history) == 1
        assert update_history[0]["clientMutationId"] == update_mutation_id
        assert update_history[0]["fingerprintVersion"] == "ingredient_product_update_v1"
        assert isinstance(update_history[0]["payloadFingerprint"], str)
        assert len(cast(str, update_history[0]["payloadFingerprint"])) == 64
        assert update_history[0]["updatedAt"] == first_updated_at
        assert "kasza jaglana po edycji" in search_prefixes
        assert "gotowana" in search_prefixes
        assert "zboza" in search_prefixes
        assert "owsianka domowa" not in search_prefixes
        assert "owies" not in search_prefixes
        assert "domowy" not in search_prefixes
        assert global_product_ref.get().exists is False

        pull_response = await food_library_service.pull_user_ingredient_products(user_id)

        assert pull_response.removedRecords == []
        assert len(pull_response.records) == 1
        pulled_record = pull_response.records[0]
        assert pulled_record.updatedAt == first_updated_at
        assert pulled_record.creationClientMutationId == create_mutation_id
        assert pulled_record.item.ingredientProductId == product_id
        assert pulled_record.item.displayName == "Kasza jaglana po edycji"
        assert pulled_record.item.defaultServing.quantity == 75
        assert pulled_record.item.nutritionPer100 is not None
        assert pulled_record.item.nutritionPer100.kcal == 119
        assert pulled_record.item.ownerUserId == user_id
        assert pulled_record.item.recordScope == "user_scoped"

        retried_row, retried = await food_library_service.update_user_ingredient_product(
            user_id,
            ingredient_product_id=product_id,
            request=update_request,
        )

        retry_snapshot = user_product_ref.get()
        retry_payload = retry_snapshot.to_dict() or {}
        assert retried is False
        assert retried_row.ingredientProductId == product_id
        assert retried_row.displayName == "Kasza jaglana po edycji"
        assert _snapshot_update_time(retry_snapshot) == first_update_time
        assert retry_payload == updated_payload
        assert retry_payload["updatedAt"] == first_updated_at
        assert retry_payload["updateClientMutationId"] == update_mutation_id

        second_update_request = IngredientProductUpdateRequest.model_validate(
            {
                "clientMutationId": second_update_mutation_id,
                "displayName": "Kasza jaglana druga edycja",
                "ingredientName": "Kasza jaglana druga",
                "category": "Zboza",
                "defaultServing": {"quantity": 80, "unit": "g"},
                "nutritionPer100": {
                    "basis": "per_100g",
                    "unit": "g",
                    "kcal": 121,
                    "protein": 3.8,
                    "fat": 1.2,
                    "carbs": 24,
                },
                "dietaryFlags": ["vegetarian"],
                "allergenFlags": [],
            }
        )

        second_row, second_updated = await food_library_service.update_user_ingredient_product(
            user_id,
            ingredient_product_id=product_id,
            request=second_update_request,
        )

        second_snapshot = user_product_ref.get()
        second_payload = second_snapshot.to_dict() or {}
        second_update_time = _snapshot_update_time(second_snapshot)
        assert second_update_time is not None
        assert second_updated is True
        assert second_row.displayName == "Kasza jaglana druga edycja"
        assert second_payload["displayName"] == "Kasza jaglana druga edycja"
        assert second_payload["updateClientMutationId"] == second_update_mutation_id
        assert second_payload["updatedAt"] == second_updated_at
        second_history = cast(list[dict[str, object]], second_payload["updateMutationHistory"])
        assert [entry["clientMutationId"] for entry in second_history] == [
            second_update_mutation_id,
            update_mutation_id,
        ]

        late_retry_row, late_retry_updated = (
            await food_library_service.update_user_ingredient_product(
                user_id,
                ingredient_product_id=product_id,
                request=update_request,
            )
        )

        late_retry_snapshot = user_product_ref.get()
        late_retry_payload = late_retry_snapshot.to_dict() or {}
        assert late_retry_updated is False
        assert late_retry_row.displayName == "Kasza jaglana druga edycja"
        assert late_retry_payload == second_payload
        assert _snapshot_update_time(late_retry_snapshot) == second_update_time
    finally:
        for document_ref in reversed(seeded_refs):
            document_ref.delete()


async def test_user_created_ingredient_product_delete_writes_tombstone_and_pulls_removal(
    mocker: MockerFixture,
) -> None:
    client = _emulator_client()
    run_id = uuid4().hex
    user_id = f"food-library-delete-current-{run_id}"
    product_id = f"user-oats-delete-{run_id}"
    create_mutation_id = f"create-mutation-{run_id}"
    delete_mutation_id = f"delete-mutation-{run_id}"
    user_ref = client.collection(USERS_COLLECTION).document(user_id)
    user_product_ref = user_ref.collection(INGREDIENT_PRODUCTS_SUBCOLLECTION).document(
        product_id
    )
    global_product_ref = client.collection(INGREDIENT_PRODUCTS_COLLECTION).document(
        product_id
    )
    seeded_refs: list[firestore.DocumentReference] = [user_ref, user_product_ref]

    mocker.patch("app.services.food_library_service.get_firestore", return_value=client)

    user_ref.set({"uid": user_id})

    try:
        request = IngredientProductCreateRequest.model_validate(
            {
                "clientMutationId": create_mutation_id,
                "ingredientProductId": product_id,
                "displayName": "Owsianka do usuniecia",
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
            }
        )
        _created_row, created = await food_library_service.create_user_ingredient_product(
            user_id,
            request,
        )

        product_id_deleted, deleted_at, deleted = (
            await food_library_service.delete_user_ingredient_product(
                user_id,
                ingredient_product_id=product_id,
                client_mutation_id=delete_mutation_id,
            )
        )

        assert created is True
        assert product_id_deleted == product_id
        assert deleted is True
        assert deleted_at

        deleted_snapshot = user_product_ref.get()
        assert deleted_snapshot.exists is True
        tombstone_payload = deleted_snapshot.to_dict() or {}
        assert tombstone_payload["ingredientProductId"] == product_id
        assert tombstone_payload["recordScope"] == "user_scoped"
        assert tombstone_payload["ownerUserId"] == user_id
        assert tombstone_payload["lifecycleState"] == "rejected"
        assert tombstone_payload["rejectionReason"] == "user_deleted"
        assert tombstone_payload["deletionClientMutationId"] == delete_mutation_id
        assert tombstone_payload["updatedAt"] == deleted_at
        assert tombstone_payload["rejectedAt"]
        assert global_product_ref.get().exists is False

        pull_response = await food_library_service.pull_user_ingredient_products(user_id)

        assert [
            record.item.ingredientProductId for record in pull_response.records
        ] == []
        assert [
            removed.model_dump(mode="json")
            for removed in pull_response.removedRecords
        ] == [
            {
                "ingredientProductId": product_id,
                "updatedAt": deleted_at,
                "removalReason": "rejected",
            }
        ]

        _repeat_id, repeat_deleted_at, repeat_deleted = (
            await food_library_service.delete_user_ingredient_product(
                user_id,
                ingredient_product_id=product_id,
                client_mutation_id=f"{delete_mutation_id}-repeat",
            )
        )

        repeat_payload = user_product_ref.get().to_dict() or {}
        assert repeat_deleted is False
        assert repeat_deleted_at == deleted_at
        assert repeat_payload["updatedAt"] == tombstone_payload["updatedAt"]
        assert repeat_payload["rejectedAt"] == tombstone_payload["rejectedAt"]
        assert repeat_payload["deletionClientMutationId"] == delete_mutation_id
    finally:
        for document_ref in reversed(seeded_refs):
            document_ref.delete()
