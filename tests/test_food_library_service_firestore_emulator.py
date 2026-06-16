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
from app.schemas.food_library import IngredientProductCreateRequest
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
