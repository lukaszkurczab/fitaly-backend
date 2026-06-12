"""Route-level Firestore/Auth emulator evidence for nutrition state."""

from datetime import datetime, timezone
import json
import os
from pathlib import Path
from typing import Any, Callable, cast
from unittest.mock import MagicMock
from urllib import request
from uuid import uuid4

import firebase_admin
import pytest
from fastapi.testclient import TestClient
from google.cloud import firestore
from pytest import MonkeyPatch
from pytest_mock import MockerFixture

from app.core.config import settings
from app.core.firestore_constants import (
    AI_CREDITS_CURRENT_DOCUMENT_ID,
    AI_CREDITS_SUBCOLLECTION,
    BILLING_DOCUMENT_ID,
    BILLING_SUBCOLLECTION,
    MEALS_SUBCOLLECTION,
    STREAK_SUBCOLLECTION,
    USERS_COLLECTION,
)
from app.services.streak_service import STREAK_DOCUMENT_ID


pytestmark = pytest.mark.skipif(
    not os.getenv("FIRESTORE_EMULATOR_HOST")
    or not os.getenv("FIREBASE_AUTH_EMULATOR_HOST")
    or not os.getenv("FIREBASE_PROJECT_ID"),
    reason="Firestore/Auth emulators and FIREBASE_PROJECT_ID are not configured.",
)


def _auth_emulator_url(path: str) -> str:
    host = os.environ["FIREBASE_AUTH_EMULATOR_HOST"].strip()
    return f"http://{host}/identitytoolkit.googleapis.com/v1/{path}?key=fake-api-key"


def _post_auth_emulator(path: str, payload: dict[str, object]) -> dict[str, Any]:
    body = json.dumps(payload).encode("utf-8")
    req = request.Request(
        _auth_emulator_url(path),
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with request.urlopen(req, timeout=10) as response:
        return dict(json.loads(response.read().decode("utf-8")))


def _sign_up_auth_emulator_user(email: str, password: str) -> tuple[str, str]:
    payload = _post_auth_emulator(
        "accounts:signUp",
        {"email": email, "password": password, "returnSecureToken": True},
    )
    uid = str(payload["localId"])
    id_token = str(payload["idToken"])
    return uid, id_token


def _delete_auth_emulator_user(id_token: str) -> None:
    try:
        _post_auth_emulator("accounts:delete", {"idToken": id_token})
    except Exception:
        return


def _reset_firebase_singletons() -> None:
    from app.db import firebase as firebase_db

    firebase_db.get_firestore.cache_clear()
    firebase_db.get_storage_bucket.cache_clear()
    delete_app = cast(Callable[[firebase_admin.App], None], getattr(firebase_admin, "delete_app"))
    for firebase_app in list(firebase_admin._apps.values()):
        delete_app(firebase_app)


def _patch_emulator_firebase_settings(monkeypatch: MonkeyPatch) -> None:
    project_id = os.environ["FIREBASE_PROJECT_ID"].strip()
    database_id = os.getenv("FIRESTORE_DATABASE_ID") or "(default)"
    credentials_path = os.getenv("GOOGLE_APPLICATION_CREDENTIALS", "").strip()
    if not credentials_path:
        local_credentials = Path("service-account.json")
        if local_credentials.exists():
            credentials_path = str(local_credentials)

    monkeypatch.setenv("FIREBASE_PROJECT_ID", project_id)
    monkeypatch.setenv("FIRESTORE_DATABASE_ID", database_id)
    if credentials_path:
        monkeypatch.setenv("GOOGLE_APPLICATION_CREDENTIALS", credentials_path)

    monkeypatch.setattr(settings, "FIREBASE_PROJECT_ID", project_id)
    monkeypatch.setattr(settings, "FIRESTORE_DATABASE_ID", database_id)
    monkeypatch.setattr(settings, "GOOGLE_APPLICATION_CREDENTIALS", credentials_path)
    monkeypatch.setattr(settings, "STATE_ENABLED", True)
    monkeypatch.setattr(settings, "HABITS_ENABLED", True)


def _emulator_firestore_client() -> firestore.Client:
    from app.db.firebase import get_firestore

    return get_firestore()


def _user_ref(
    client: firestore.Client,
    user_id: str,
) -> firestore.DocumentReference:
    return client.collection(USERS_COLLECTION).document(user_id)


def _billing_ref(
    client: firestore.Client,
    user_id: str,
) -> firestore.DocumentReference:
    return (
        _user_ref(client, user_id)
        .collection(BILLING_SUBCOLLECTION)
        .document(BILLING_DOCUMENT_ID)
    )


def _credits_ref(
    client: firestore.Client,
    user_id: str,
) -> firestore.DocumentReference:
    return (
        _billing_ref(client, user_id)
        .collection(AI_CREDITS_SUBCOLLECTION)
        .document(AI_CREDITS_CURRENT_DOCUMENT_ID)
    )


def _streak_ref(
    client: firestore.Client,
    user_id: str,
) -> firestore.DocumentReference:
    return (
        _user_ref(client, user_id)
        .collection(STREAK_SUBCOLLECTION)
        .document(STREAK_DOCUMENT_ID)
    )


def _auth_headers(id_token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {id_token}"}


def _meal_doc(
    meal_id: str,
    *,
    meal_type: str,
    kcal: float,
    protein: float,
    carbs: float,
    fat: float,
    deleted: bool = False,
) -> dict[str, object]:
    return {
        "mealId": meal_id,
        "cloudId": meal_id,
        "dayKey": "2026-06-04",
        "timestamp": f"2026-06-04T12:{len(meal_id) % 60:02d}:00Z",
        "loggedAt": f"2026-06-04T12:{len(meal_id) % 60:02d}:00Z",
        "loggedAtLocalMin": 12 * 60,
        "type": meal_type,
        "name": f"Nutrition state {meal_type}",
        "deleted": deleted,
        "totals": {
            "kcal": kcal,
            "protein": protein,
            "carbs": carbs,
            "fat": fat,
        },
        "ingredients": [
            {
                "id": f"{meal_id}-ingredient",
                "name": "Ingredient",
                "amount": 100,
                "unit": "g",
                "kcal": kcal,
                "protein": protein,
                "carbs": carbs,
                "fat": fat,
            }
        ],
    }


def _seed_user_a_state(client: firestore.Client, user_id: str, run_id: str) -> None:
    user_ref = _user_ref(client, user_id)
    user_ref.set(
        {
            "profile": {
                "nutritionProfile": {
                    "calorieTarget": 2100,
                    "macroTargets": {
                        "proteinGrams": 130,
                        "carbsGrams": 230,
                        "fatGrams": 80,
                    },
                },
            },
        }
    )

    meal_collection = user_ref.collection(MEALS_SUBCOLLECTION)
    meal_collection.document(f"breakfast-{run_id}").set(
        _meal_doc(
            f"breakfast-{run_id}",
            meal_type="breakfast",
            kcal=450,
            protein=35,
            carbs=40,
            fat=12,
        )
    )
    meal_collection.document(f"lunch-{run_id}").set(
        _meal_doc(
            f"lunch-{run_id}",
            meal_type="lunch",
            kcal=650,
            protein=40,
            carbs=70,
            fat=20,
        )
    )
    meal_collection.document(f"deleted-{run_id}").set(
        _meal_doc(
            f"deleted-{run_id}",
            meal_type="dinner",
            kcal=999,
            protein=99,
            carbs=99,
            fat=99,
            deleted=True,
        )
    )
    _streak_ref(client, user_id).set({"current": 6, "lastDate": "2026-06-04"})
    _credits_ref(client, user_id).set(
        {
            "tier": "premium",
            "balance": 640,
            "allocation": settings.AI_CREDITS_PREMIUM,
            "periodStartAt": datetime(2026, 6, 1, 0, 0, tzinfo=timezone.utc),
            "periodEndAt": datetime(2026, 7, 1, 0, 0, tzinfo=timezone.utc),
            "renewalAnchorSource": "premium_activation",
            "revenueCatEntitlementId": "premium",
            "revenueCatExpirationAt": datetime(2026, 7, 1, 0, 0, tzinfo=timezone.utc),
            "lastRevenueCatEventId": f"evt-nutrition-state-{run_id}",
            "createdAt": datetime(2026, 6, 1, 0, 0, tzinfo=timezone.utc),
            "updatedAt": datetime(2026, 6, 4, 9, 0, tzinfo=timezone.utc),
        }
    )


def _delete_user_tree(client: firestore.Client, user_id: str) -> None:
    if not user_id:
        return

    user_ref = _user_ref(client, user_id)
    for meal in user_ref.collection(MEALS_SUBCOLLECTION).stream():
        meal.reference.delete()
    _streak_ref(client, user_id).delete()
    _credits_ref(client, user_id).delete()
    _billing_ref(client, user_id).delete()
    user_ref.delete()


def test_nutrition_state_route_uses_real_auth_and_firestore_emulator_state(
    mock_auth_token_decoder: MagicMock,
    monkeypatch: MonkeyPatch,
    mocker: MockerFixture,
) -> None:
    mocker.stop(mock_auth_token_decoder)
    _patch_emulator_firebase_settings(monkeypatch)
    _reset_firebase_singletons()

    from app.main import app

    api_client = TestClient(app)
    firestore_client = _emulator_firestore_client()
    run_id = uuid4().hex
    password = "emulator-password-123"
    user_a_uid = ""
    user_b_uid = ""
    token_a = ""
    token_b = ""

    try:
        user_a_uid, token_a = _sign_up_auth_emulator_user(
            f"nutrition-state-a-{run_id}@example.invalid",
            password,
        )
        user_b_uid, token_b = _sign_up_auth_emulator_user(
            f"nutrition-state-b-{run_id}@example.invalid",
            password,
        )
        assert user_a_uid != user_b_uid

        _seed_user_a_state(firestore_client, user_a_uid, run_id)

        response_a = api_client.get(
            "/api/v2/users/me/state?day=2026-06-04",
            headers=_auth_headers(token_a),
        )

        assert response_a.status_code == 200
        payload_a = response_a.json()
        assert payload_a["dayKey"] == "2026-06-04"
        assert payload_a["targets"] == {
            "kcal": 2100.0,
            "protein": 130.0,
            "carbs": 230.0,
            "fat": 80.0,
        }
        assert payload_a["consumed"] == {
            "kcal": 1100.0,
            "protein": 75.0,
            "carbs": 110.0,
            "fat": 32.0,
        }
        assert payload_a["remaining"] == {
            "kcal": 1000.0,
            "protein": 55.0,
            "carbs": 120.0,
            "fat": 48.0,
        }
        assert payload_a["overTarget"] == {
            "kcal": 0.0,
            "protein": 0.0,
            "carbs": 0.0,
            "fat": 0.0,
        }
        assert payload_a["quality"] == {
            "mealsLogged": 2,
            "missingNutritionMeals": 0,
            "dataCompletenessScore": 1.0,
        }
        assert payload_a["streak"] == {
            "available": True,
            "current": 6,
            "lastDate": "2026-06-04",
        }
        assert payload_a["ai"] == {
            "available": True,
            "tier": "premium",
            "balance": 640,
            "allocation": settings.AI_CREDITS_PREMIUM,
            "usedThisPeriod": settings.AI_CREDITS_PREMIUM - 640,
            "periodStartAt": "2026-06-01T00:00:00Z",
            "periodEndAt": "2026-07-01T00:00:00Z",
            "costs": {
                "chat": settings.AI_CREDIT_COST_CHAT,
                "textMeal": settings.AI_CREDIT_COST_TEXT_MEAL,
                "photo": settings.AI_CREDIT_COST_PHOTO,
            },
        }
        assert payload_a["meta"] == {
            "isDegraded": False,
            "componentStatus": {
                "habits": "ok",
                "streak": "ok",
                "ai": "ok",
            },
        }

        response_b = api_client.get(
            "/api/v2/users/me/state?day=2026-06-04",
            headers=_auth_headers(token_b),
        )

        assert response_b.status_code == 200
        payload_b = response_b.json()
        assert payload_b["dayKey"] == "2026-06-04"
        assert payload_b["targets"] == {
            "kcal": None,
            "protein": None,
            "carbs": None,
            "fat": None,
        }
        assert payload_b["consumed"] == {
            "kcal": 0.0,
            "protein": 0.0,
            "carbs": 0.0,
            "fat": 0.0,
        }
        assert payload_b["quality"] == {
            "mealsLogged": 0,
            "missingNutritionMeals": 0,
            "dataCompletenessScore": 0.0,
        }
        assert payload_b["streak"] == {
            "available": True,
            "current": 0,
            "lastDate": None,
        }
        assert payload_b["ai"]["tier"] == "free"
        assert payload_b["ai"]["balance"] == settings.AI_CREDITS_FREE
        assert payload_b["ai"]["allocation"] == settings.AI_CREDITS_FREE
        assert payload_b["ai"]["usedThisPeriod"] == 0
        assert payload_b["meta"]["isDegraded"] is False
        assert payload_b["meta"]["componentStatus"] == {
            "habits": "ok",
            "streak": "ok",
            "ai": "ok",
        }

        credits_a = dict(_credits_ref(firestore_client, user_a_uid).get().to_dict() or {})
        credits_b = dict(_credits_ref(firestore_client, user_b_uid).get().to_dict() or {})
        assert credits_a["tier"] == "premium"
        assert credits_a["balance"] == 640
        assert credits_b["tier"] == "free"
        assert credits_b["balance"] == settings.AI_CREDITS_FREE

        invalid_day = api_client.get(
            "/api/v2/users/me/state?day=2026/06/04",
            headers=_auth_headers(token_a),
        )
        assert invalid_day.status_code == 400
        assert invalid_day.json() == {
            "detail": "Invalid day key. Expected YYYY-MM-DD."
        }
    finally:
        _delete_user_tree(firestore_client, user_a_uid)
        _delete_user_tree(firestore_client, user_b_uid)
        if token_a:
            _delete_auth_emulator_user(token_a)
        if token_b:
            _delete_auth_emulator_user(token_b)
        _reset_firebase_singletons()
