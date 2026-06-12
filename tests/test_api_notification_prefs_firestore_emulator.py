"""Route-level Firestore/Auth emulator evidence for notification preferences."""

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
from app.core.firestore_constants import PREFS_SUBCOLLECTION, USERS_COLLECTION
from app.services.notification_service import GLOBAL_PREFS_DOCUMENT


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


def _prefs_ref(
    client: firestore.Client,
    user_id: str,
) -> firestore.DocumentReference:
    return (
        _user_ref(client, user_id)
        .collection(PREFS_SUBCOLLECTION)
        .document(GLOBAL_PREFS_DOCUMENT)
    )


def _legacy_notification_doc_ids(client: firestore.Client, user_id: str) -> list[str]:
    return [
        snapshot.id
        for snapshot in _user_ref(client, user_id).collection("notifications").stream()
    ]


def _auth_headers(id_token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {id_token}"}


def _empty_notification_response() -> dict[str, object]:
    return {
        "notifications": {
            "smartRemindersEnabled": None,
            "motivationEnabled": None,
            "statsEnabled": None,
            "weekdays0to6": None,
            "daysAhead": None,
            "quietHours": None,
        },
    }


def test_notification_preferences_routes_use_real_auth_and_canonical_firestore_path(
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
    token_a = ""
    token_b = ""
    user_a_uid = ""
    user_b_uid = ""

    try:
        user_a_uid, token_a = _sign_up_auth_emulator_user(
            f"notification-prefs-a-{run_id}@example.invalid",
            password,
        )
        user_b_uid, token_b = _sign_up_auth_emulator_user(
            f"notification-prefs-b-{run_id}@example.invalid",
            password,
        )

        unauthenticated = api_client.get("/api/v1/users/me/notifications/preferences")
        assert unauthenticated.status_code == 401
        assert unauthenticated.json() == {"detail": "Authentication required"}

        prefs_a_ref = _prefs_ref(firestore_client, user_a_uid)
        prefs_b_ref = _prefs_ref(firestore_client, user_b_uid)
        prefs_a_ref.set(
            {
                "notifications": {
                    "smartRemindersEnabled": True,
                    "motivationEnabled": False,
                    "statsEnabled": True,
                    "weekdays0to6": [6, 1, 1],
                    "daysAhead": 5,
                    "quietHours": {"startHour": 22, "endHour": 7},
                    "unknownLegacyField": "ignored-by-response-schema",
                },
                "profile": {"locale": "pl-PL"},
            }
        )

        get_a = api_client.get(
            "/api/v1/users/me/notifications/preferences",
            headers=_auth_headers(token_a),
        )
        assert get_a.status_code == 200
        assert get_a.json() == {
            "notifications": {
                "smartRemindersEnabled": True,
                "motivationEnabled": False,
                "statsEnabled": True,
                "weekdays0to6": [1, 6],
                "daysAhead": 5,
                "quietHours": {"startHour": 22, "endHour": 7},
            },
        }

        get_b = api_client.get(
            "/api/v1/users/me/notifications/preferences",
            headers=_auth_headers(token_b),
        )
        assert get_b.status_code == 200
        assert get_b.json() == _empty_notification_response()
        assert prefs_b_ref.get().exists is False

        update_a = api_client.post(
            "/api/v1/users/me/notifications/preferences",
            json={
                "notifications": {
                    "motivationEnabled": True,
                    "quietHours": {"startHour": 21, "endHour": 6},
                }
            },
            headers=_auth_headers(token_a),
        )
        assert update_a.status_code == 200
        assert update_a.json() == {
            "notifications": {
                "smartRemindersEnabled": True,
                "motivationEnabled": True,
                "statsEnabled": True,
                "weekdays0to6": [1, 6],
                "daysAhead": 5,
                "quietHours": {"startHour": 21, "endHour": 6},
            },
            "updated": True,
        }

        stored_a = prefs_a_ref.get().to_dict() or {}
        stored_a_notifications = cast(dict[str, object], stored_a["notifications"])
        assert stored_a_notifications["smartRemindersEnabled"] is True
        assert stored_a_notifications["motivationEnabled"] is True
        assert stored_a_notifications["statsEnabled"] is True
        assert stored_a_notifications["weekdays0to6"] == [1, 6]
        assert stored_a_notifications["daysAhead"] == 5
        assert stored_a_notifications["quietHours"] == {"startHour": 21, "endHour": 6}
        assert stored_a["profile"] == {"locale": "pl-PL"}

        invalid_b = api_client.post(
            "/api/v1/users/me/notifications/preferences",
            json={"notifications": {"daysAhead": 15}},
            headers=_auth_headers(token_b),
        )
        # Pydantic rejects this before the notification service, so no canonical
        # Firestore document is created for User B.
        assert invalid_b.status_code == 422
        assert prefs_b_ref.get().exists is False

        assert _legacy_notification_doc_ids(firestore_client, user_a_uid) == []
        assert _legacy_notification_doc_ids(firestore_client, user_b_uid) == []
    finally:
        for uid in (user_a_uid, user_b_uid):
            if not uid:
                continue
            user_ref = _user_ref(firestore_client, uid)
            user_ref.collection(PREFS_SUBCOLLECTION).document(GLOBAL_PREFS_DOCUMENT).delete()
            for legacy_doc in user_ref.collection("notifications").stream():
                legacy_doc.reference.delete()
            user_ref.delete()
        if token_a:
            _delete_auth_emulator_user(token_a)
        if token_b:
            _delete_auth_emulator_user(token_b)
        _reset_firebase_singletons()
