"""Route-level Firestore/Auth emulator evidence for habit signals."""

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
from app.core.firestore_constants import MEALS_SUBCOLLECTION, USERS_COLLECTION


pytestmark = pytest.mark.skipif(
    not os.getenv("FIRESTORE_EMULATOR_HOST")
    or not os.getenv("FIREBASE_AUTH_EMULATOR_HOST"),
    reason="Firestore/Auth emulators are not configured.",
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
    project_id = os.getenv("FIREBASE_PROJECT_ID") or "demo-fitaly-local"
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


def _emulator_firestore_client() -> firestore.Client:
    from app.db.firebase import get_firestore

    return get_firestore()


def _user_ref(
    client: firestore.Client,
    user_id: str,
) -> firestore.DocumentReference:
    return client.collection(USERS_COLLECTION).document(user_id)


def _auth_headers(id_token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {id_token}"}


def _meal_doc(
    meal_id: str,
    *,
    day_key: str,
    meal_type: str,
    logged_at_local_min: int,
    kcal: float,
    protein: float,
    deleted: bool = False,
) -> dict[str, object]:
    hour = logged_at_local_min // 60
    minute = logged_at_local_min % 60
    logged_at = f"{day_key}T{hour:02d}:{minute:02d}:00Z"
    return {
        "mealId": meal_id,
        "cloudId": meal_id,
        "dayKey": day_key,
        "timestamp": logged_at,
        "loggedAt": logged_at,
        "loggedAtLocalMin": logged_at_local_min,
        "type": meal_type,
        "name": f"Habit emulator {meal_type}",
        "deleted": deleted,
        "totals": {
            "kcal": kcal,
            "protein": protein,
            "carbs": 0,
            "fat": 0,
        },
        "ingredients": [
            {
                "id": f"{meal_id}-ingredient",
                "name": "Ingredient",
                "amount": 100,
                "unit": "g",
                "kcal": kcal,
                "protein": protein,
                "carbs": 0,
                "fat": 0,
            }
        ],
    }


def _set_meal(
    client: firestore.Client,
    user_id: str,
    run_id: str,
    *,
    suffix: str,
    day_key: str,
    meal_type: str,
    logged_at_local_min: int,
    kcal: float,
    protein: float,
    deleted: bool = False,
) -> None:
    meal_id = f"{suffix}-{run_id}"
    (
        _user_ref(client, user_id)
        .collection(MEALS_SUBCOLLECTION)
        .document(meal_id)
        .set(
            _meal_doc(
                meal_id,
                day_key=day_key,
                meal_type=meal_type,
                logged_at_local_min=logged_at_local_min,
                kcal=kcal,
                protein=protein,
                deleted=deleted,
            )
        )
    )


def _seed_user_a_state(client: firestore.Client, user_id: str, run_id: str) -> None:
    _user_ref(client, user_id).set(
        {
            "profile": {
                "nutritionProfile": {
                    "calorieTarget": 2000,
                    "macroTargets": {
                        "proteinGrams": 100,
                    },
                },
            },
        }
    )

    _set_meal(
        client,
        user_id,
        run_id,
        suffix="other-2026-05-29",
        day_key="2026-05-29",
        meal_type="other",
        logged_at_local_min=12 * 60,
        kcal=900,
        protein=100,
    )
    _set_meal(
        client,
        user_id,
        run_id,
        suffix="breakfast-2026-05-30",
        day_key="2026-05-30",
        meal_type="breakfast",
        logged_at_local_min=8 * 60,
        kcal=500,
        protein=45,
    )
    _set_meal(
        client,
        user_id,
        run_id,
        suffix="lunch-2026-05-30",
        day_key="2026-05-30",
        meal_type="lunch",
        logged_at_local_min=13 * 60,
        kcal=700,
        protein=50,
    )
    _set_meal(
        client,
        user_id,
        run_id,
        suffix="snack-2026-05-30",
        day_key="2026-05-30",
        meal_type="snack",
        logged_at_local_min=17 * 60,
        kcal=200,
        protein=10,
    )
    _set_meal(
        client,
        user_id,
        run_id,
        suffix="snack-2026-05-31",
        day_key="2026-05-31",
        meal_type="snack",
        logged_at_local_min=16 * 60,
        kcal=300,
        protein=20,
    )
    _set_meal(
        client,
        user_id,
        run_id,
        suffix="lunch-2026-06-01",
        day_key="2026-06-01",
        meal_type="lunch",
        logged_at_local_min=13 * 60,
        kcal=1000,
        protein=90,
    )
    _set_meal(
        client,
        user_id,
        run_id,
        suffix="breakfast-2026-06-02",
        day_key="2026-06-02",
        meal_type="breakfast",
        logged_at_local_min=8 * 60,
        kcal=600,
        protein=50,
    )
    _set_meal(
        client,
        user_id,
        run_id,
        suffix="breakfast-2026-06-04",
        day_key="2026-06-04",
        meal_type="breakfast",
        logged_at_local_min=8 * 60,
        kcal=500,
        protein=40,
    )
    _set_meal(
        client,
        user_id,
        run_id,
        suffix="lunch-2026-06-04",
        day_key="2026-06-04",
        meal_type="lunch",
        logged_at_local_min=13 * 60,
        kcal=800,
        protein=60,
    )
    _set_meal(
        client,
        user_id,
        run_id,
        suffix="deleted-dinner-2026-06-03",
        day_key="2026-06-03",
        meal_type="dinner",
        logged_at_local_min=19 * 60,
        kcal=999,
        protein=999,
        deleted=True,
    )


def _delete_user_tree(client: firestore.Client, user_id: str) -> None:
    if not user_id:
        return

    user_ref = _user_ref(client, user_id)
    for meal in user_ref.collection(MEALS_SUBCOLLECTION).stream():
        meal.reference.delete()
    user_ref.delete()


def test_habits_route_uses_real_auth_and_firestore_emulator_state(
    mock_auth_token_decoder: MagicMock,
    monkeypatch: MonkeyPatch,
    mocker: MockerFixture,
) -> None:
    mocker.stop(mock_auth_token_decoder)
    _patch_emulator_firebase_settings(monkeypatch)
    _reset_firebase_singletons()

    fixed_now = datetime(2026, 6, 4, 12, 0, tzinfo=timezone.utc)
    mocker.patch("app.services.habit_signal_service.utc_now", return_value=fixed_now)

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
            f"habits-a-{run_id}@example.invalid",
            password,
        )
        user_b_uid, token_b = _sign_up_auth_emulator_user(
            f"habits-b-{run_id}@example.invalid",
            password,
        )
        assert user_a_uid != user_b_uid

        unauthenticated = api_client.get("/api/v2/users/me/habits")
        assert unauthenticated.status_code == 401
        assert unauthenticated.json() == {"detail": "Authentication required"}

        _seed_user_a_state(firestore_client, user_a_uid, run_id)

        response_a = api_client.get(
            "/api/v2/users/me/habits",
            headers=_auth_headers(token_a),
        )

        assert response_a.status_code == 200
        payload_a = response_a.json()
        assert payload_a["computedAt"] == "2026-06-04T12:00:00Z"
        assert payload_a["windowDays"] == {
            "recentActivity": 7,
            "adherence": 14,
            "consistency": 28,
        }
        assert payload_a["behavior"]["loggingDays7"] == 6
        assert payload_a["behavior"]["validLoggingDays7"] == 6
        assert payload_a["behavior"]["loggingConsistency28"] == 0.2143
        assert payload_a["behavior"]["validLoggingConsistency28"] == 0.2143
        assert payload_a["behavior"]["avgMealsPerLoggedDay14"] == 1.5
        assert payload_a["behavior"]["avgValidMealsPerValidLoggedDay14"] == 1.5
        assert payload_a["behavior"]["mealTypeCoverage14"] == {
            "breakfast": True,
            "lunch": True,
            "dinner": False,
            "snack": True,
            "other": True,
            "coveredCount": 4,
        }
        assert payload_a["behavior"]["mealTypeFrequency14"] == {
            "breakfast": 3,
            "lunch": 3,
            "dinner": 0,
            "snack": 2,
            "other": 1,
        }
        assert payload_a["behavior"]["dayCoverage14"] == {
            "loggedDays": 6,
            "validLoggedDays": 6,
        }
        assert payload_a["behavior"]["kcalAdherence14"] == 0.4583
        assert payload_a["behavior"]["kcalUnderTargetRatio14"] == 1.0
        assert payload_a["behavior"]["proteinDaysHit14"] == {
            "hitDays": 4,
            "eligibleDays": 6,
            "unknownDays": 0,
            "ratio": 0.6667,
        }
        assert payload_a["behavior"]["timingPatterns14"] == {
            "available": True,
            "observedDays": 6,
            "firstMealMedianHour": 10.0,
            "lastMealMedianHour": 13.0,
            "eatingWindowHoursMedian": 0.0,
            "breakfastMedianHour": 8.0,
            "lunchMedianHour": 13.0,
            "dinnerMedianHour": None,
            "snackMedianHour": 16.5,
            "otherMedianHour": 12.0,
        }
        assert payload_a["dataQuality"] == {
            "daysWithUnknownMealDetails14": 0,
            "daysUsingTimestampDayFallback14": 0,
            "daysUsingTimestampTimingFallback14": 0,
        }
        assert payload_a["topRisk"] == "under_logging"
        assert payload_a["coachPriority"] == "logging_foundation"

        response_b = api_client.get(
            "/api/v2/users/me/habits",
            headers=_auth_headers(token_b),
        )

        assert response_b.status_code == 200
        payload_b = response_b.json()
        assert payload_b["computedAt"] == "2026-06-04T12:00:00Z"
        assert payload_b["behavior"]["loggingDays7"] == 0
        assert payload_b["behavior"]["validLoggingDays7"] == 0
        assert payload_b["behavior"]["avgMealsPerLoggedDay14"] == 0.0
        assert payload_b["behavior"]["mealTypeCoverage14"] == {
            "breakfast": False,
            "lunch": False,
            "dinner": False,
            "snack": False,
            "other": False,
            "coveredCount": 0,
        }
        assert payload_b["behavior"]["mealTypeFrequency14"] == {
            "breakfast": 0,
            "lunch": 0,
            "dinner": 0,
            "snack": 0,
            "other": 0,
        }
        assert payload_b["behavior"]["dayCoverage14"] == {
            "loggedDays": 0,
            "validLoggedDays": 0,
        }
        assert payload_b["behavior"]["proteinDaysHit14"] == {
            "hitDays": 0,
            "eligibleDays": 0,
            "unknownDays": 0,
            "ratio": None,
        }
        assert payload_b["behavior"]["timingPatterns14"] == {
            "available": False,
            "observedDays": 0,
            "firstMealMedianHour": None,
            "lastMealMedianHour": None,
            "eatingWindowHoursMedian": None,
            "breakfastMedianHour": None,
            "lunchMedianHour": None,
            "dinnerMedianHour": None,
            "snackMedianHour": None,
            "otherMedianHour": None,
        }
        assert payload_b["dataQuality"] == {
            "daysWithUnknownMealDetails14": 0,
            "daysUsingTimestampDayFallback14": 0,
            "daysUsingTimestampTimingFallback14": 0,
        }
        assert payload_b["topRisk"] == "under_logging"
        assert payload_b["coachPriority"] == "logging_foundation"
    finally:
        _delete_user_tree(firestore_client, user_a_uid)
        _delete_user_tree(firestore_client, user_b_uid)
        if token_a:
            _delete_auth_emulator_user(token_a)
        if token_b:
            _delete_auth_emulator_user(token_b)
        _reset_firebase_singletons()
