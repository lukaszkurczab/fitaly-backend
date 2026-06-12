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


pytestmark = pytest.mark.skipif(
    not os.getenv("FIREBASE_AUTH_EMULATOR_HOST")
    or not os.getenv("FIRESTORE_EMULATOR_HOST"),
    reason="Firebase Auth and Firestore emulators are not configured.",
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
        # Best-effort cleanup: Firestore cleanup below is the route evidence guard.
        return


def _reset_firebase_singletons() -> None:
    from app.db import firebase as firebase_db

    firebase_db.get_firestore.cache_clear()
    firebase_db.get_storage_bucket.cache_clear()
    delete_app = cast(Callable[[firebase_admin.App], None], getattr(firebase_admin, "delete_app"))
    for firebase_app in list(firebase_admin._apps.values()):
        delete_app(firebase_app)


def _patch_emulator_firebase_settings(monkeypatch: MonkeyPatch) -> None:
    from app.core.config import settings

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


def _meal_payload(meal_id: str, *, updated_at: str) -> dict[str, object]:
    return {
        "clientMutationId": f"mutation-{meal_id}",
        "mealId": meal_id,
        "timestamp": "2026-04-18T12:00:00.000Z",
        "dayKey": "2026-04-18",
        "type": "lunch",
        "name": "Route emulator lunch",
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
        "updatedAt": updated_at,
        "source": "manual",
        "inputMethod": "manual",
    }


def _auth_headers(id_token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {id_token}"}


def test_pr3_meals_routes_reject_authenticated_malformed_queries_with_real_auth(
    mock_auth_token_decoder: MagicMock,
    monkeypatch: MonkeyPatch,
    mocker: MockerFixture,
) -> None:
    mocker.stop(mock_auth_token_decoder)
    _patch_emulator_firebase_settings(monkeypatch)
    _reset_firebase_singletons()

    from app.main import app

    client = TestClient(app)
    firestore_client = _emulator_firestore_client()
    run_id = uuid4().hex
    email = f"pr3-meals-malformed-{run_id}@example.invalid"
    password = "emulator-password-123"
    user_uid = ""
    token = ""

    try:
        user_uid, token = _sign_up_auth_emulator_user(email, password)
        headers = _auth_headers(token)

        malformed_day_key = client.get(
            "/api/v1/users/me/meals/history?dayKeyStart=2026/04/18",
            headers=headers,
        )
        assert malformed_day_key.status_code == 400
        assert malformed_day_key.json() == {
            "detail": "dayKey must use YYYY-MM-DD format"
        }

        reversed_day_key_range = client.get(
            "/api/v1/users/me/meals/history"
            "?dayKeyStart=2026-04-20&dayKeyEnd=2026-04-18",
            headers=headers,
        )
        assert reversed_day_key_range.status_code == 400
        assert reversed_day_key_range.json() == {"detail": "Invalid dayKey range"}

        legacy_logged_at_range = client.get(
            "/api/v1/users/me/meals/history"
            "?loggedAtStart=2026-04-18T00:00:00.000Z"
            "&loggedAtEnd=2026-04-18T23:59:59.999Z",
            headers=headers,
        )
        assert legacy_logged_at_range.status_code == 400
        assert legacy_logged_at_range.json() == {
            "detail": "Use dayKeyStart/dayKeyEnd for meal history ranges"
        }

        malformed_after_cursor = client.get(
            "/api/v1/users/me/meals/changes?afterCursor=not-a-cursor",
            headers=headers,
        )
        assert malformed_after_cursor.status_code == 400
        assert malformed_after_cursor.json() == {"detail": "Invalid cursor"}
    finally:
        if user_uid:
            firestore_client.collection("users").document(user_uid).delete()
        if token:
            _delete_auth_emulator_user(token)
        _reset_firebase_singletons()


def test_pr3_meals_routes_use_real_auth_token_and_firestore_emulator(
    mock_auth_token_decoder: MagicMock,
    monkeypatch: MonkeyPatch,
    mocker: MockerFixture,
) -> None:
    mocker.stop(mock_auth_token_decoder)
    _patch_emulator_firebase_settings(monkeypatch)
    _reset_firebase_singletons()

    from app.main import app

    client = TestClient(app)
    firestore_client = _emulator_firestore_client()
    run_id = uuid4().hex
    email_a = f"pr3-meals-a-{run_id}@example.invalid"
    email_b = f"pr3-meals-b-{run_id}@example.invalid"
    password = "emulator-password-123"
    meal_a_id = f"route-meal-a-{run_id}"
    meal_b_id = f"route-meal-b-{run_id}"
    user_a_uid = ""
    user_b_uid = ""
    token_a = ""
    token_b = ""

    mocker.patch("app.services.meal_service.streak_service.sync_streak_from_meals")

    try:
        user_a_uid, token_a = _sign_up_auth_emulator_user(email_a, password)
        user_b_uid, token_b = _sign_up_auth_emulator_user(email_b, password)

        unauthenticated = client.get("/api/v1/users/me/meals/history")
        assert unauthenticated.status_code == 401
        assert unauthenticated.json() == {"detail": "Authentication required"}

        upsert_a = client.post(
            "/api/v1/users/me/meals",
            json=_meal_payload(meal_a_id, updated_at="2026-04-18T12:10:00.000Z"),
            headers=_auth_headers(token_a),
        )
        assert upsert_a.status_code == 200
        assert upsert_a.json()["meal"]["id"] == meal_a_id

        stored_a = (
            firestore_client.collection("users")
            .document(user_a_uid)
            .collection("meals")
            .document(meal_a_id)
            .get()
        )
        assert stored_a.exists is True

        upsert_b = client.post(
            "/api/v1/users/me/meals",
            json=_meal_payload(meal_b_id, updated_at="2026-04-18T12:11:00.000Z"),
            headers=_auth_headers(token_b),
        )
        assert upsert_b.status_code == 200

        history = client.get(
            "/api/v1/users/me/meals/history"
            "?dayKeyStart=2026-04-18&dayKeyEnd=2026-04-18",
            headers=_auth_headers(token_a),
        )
        assert history.status_code == 200
        history_items = history.json()["items"]
        assert [item["id"] for item in history_items] == [meal_a_id]
        assert meal_b_id not in {item["id"] for item in history_items}

        changes = client.get(
            "/api/v1/users/me/meals/changes",
            headers=_auth_headers(token_a),
        )
        repeated_changes = client.get(
            "/api/v1/users/me/meals/changes",
            headers=_auth_headers(token_a),
        )
        assert changes.status_code == 200
        assert repeated_changes.status_code == 200
        assert repeated_changes.json() == changes.json()
        changes_by_id = {item["id"]: item for item in changes.json()["items"]}
        assert set(changes_by_id) == {meal_a_id}
        assert changes_by_id[meal_a_id]["deleted"] is False

        deleted = client.post(
            f"/api/v1/users/me/meals/{meal_a_id}/delete",
            json={
                "clientMutationId": f"mutation-delete-{meal_a_id}",
                "updatedAt": "2026-04-18T12:30:00.000Z",
            },
            headers=_auth_headers(token_a),
        )
        assert deleted.status_code == 200
        assert deleted.json() == {
            "mealId": meal_a_id,
            "updatedAt": "2026-04-18T12:30:00.000Z",
            "deleted": True,
        }

        history_after_delete = client.get(
            "/api/v1/users/me/meals/history"
            "?dayKeyStart=2026-04-18&dayKeyEnd=2026-04-18",
            headers=_auth_headers(token_a),
        )
        assert history_after_delete.status_code == 200
        assert history_after_delete.json()["items"] == []

        changes_after_delete = client.get(
            "/api/v1/users/me/meals/changes",
            headers=_auth_headers(token_a),
        )
        assert changes_after_delete.status_code == 200
        deleted_changes_by_id = {
            item["id"]: item for item in changes_after_delete.json()["items"]
        }
        assert set(deleted_changes_by_id) == {meal_a_id}
        assert deleted_changes_by_id[meal_a_id]["deleted"] is True
        assert deleted_changes_by_id[meal_a_id]["updatedAt"] == (
            "2026-04-18T12:30:00.000Z"
        )
    finally:
        for uid, meal_id in ((user_a_uid, meal_a_id), (user_b_uid, meal_b_id)):
            if not uid:
                continue
            user_ref = firestore_client.collection("users").document(uid)
            user_ref.collection("meals").document(meal_id).delete()
            user_ref.delete()
        if token_a:
            _delete_auth_emulator_user(token_a)
        if token_b:
            _delete_auth_emulator_user(token_b)
        _reset_firebase_singletons()
