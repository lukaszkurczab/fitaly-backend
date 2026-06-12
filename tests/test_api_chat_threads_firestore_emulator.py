"""Route-level Firestore/Auth emulator evidence for AI Chat v2 thread projections."""

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
    CHAT_THREADS_SUBCOLLECTION,
    MESSAGES_SUBCOLLECTION,
    USERS_COLLECTION,
)


pytestmark = [
    pytest.mark.ai_v2,
    pytest.mark.skipif(
        not os.getenv("FIRESTORE_EMULATOR_HOST")
        or not os.getenv("FIREBASE_AUTH_EMULATOR_HOST"),
        reason="Firestore/Auth emulators are not configured.",
    ),
]


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
    return str(payload["localId"]), str(payload["idToken"])


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


def _threads_ref(
    client: firestore.Client,
    user_id: str,
) -> firestore.CollectionReference:
    return _user_ref(client, user_id).collection(CHAT_THREADS_SUBCOLLECTION)


def _messages_ref(
    client: firestore.Client,
    user_id: str,
    thread_id: str,
) -> firestore.CollectionReference:
    return _threads_ref(client, user_id).document(thread_id).collection(MESSAGES_SUBCOLLECTION)


def _auth_headers(id_token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {id_token}"}


def _seed_user_a_chat_threads(client: firestore.Client, user_id: str) -> None:
    _threads_ref(client, user_id).document("thread-a").set(
        {
            "title": "Thread A",
            "createdAt": 1000,
            "updatedAt": 3000,
            "lastMessage": "Newest thread",
            "lastMessageAt": 3000,
        }
    )
    _threads_ref(client, user_id).document("thread-b").set(
        {
            "title": "Thread B",
            "createdAt": 900,
            "updatedAt": 2000,
            "lastMessage": "Middle thread",
            "lastMessageAt": 2000,
        }
    )
    _threads_ref(client, user_id).document("thread-c").set(
        {
            "title": "Thread C",
            "createdAt": 800,
            "updatedAt": 1000,
            "lastMessage": "Oldest thread",
            "lastMessageAt": 1000,
        }
    )

    _messages_ref(client, user_id, "thread-a").document("msg-new").set(
        {
            "role": "user",
            "content": "Latest user message",
            "createdAt": 3000,
            "lastSyncedAt": 3100,
        }
    )
    _messages_ref(client, user_id, "thread-a").document("msg-middle").set(
        {
            "role": "tool",
            "content": "Invalid role should normalize",
            "createdAt": 2000,
        }
    )
    _messages_ref(client, user_id, "thread-a").document("msg-old").set(
        {
            "role": "assistant",
            "content": "Older assistant message",
            "createdAt": 1000,
            "lastSyncedAt": 1100,
        }
    )


def _delete_user_chat_tree(client: firestore.Client, user_id: str) -> None:
    if not user_id:
        return

    user_ref = _user_ref(client, user_id)
    for thread in user_ref.collection(CHAT_THREADS_SUBCOLLECTION).stream():
        for message in thread.reference.collection(MESSAGES_SUBCOLLECTION).stream():
            message.reference.delete()
        thread.reference.delete()
    user_ref.delete()


def test_chat_thread_projection_routes_use_real_auth_and_firestore_emulator_state(
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
            f"chat-threads-a-{run_id}@example.invalid",
            password,
        )
        user_b_uid, token_b = _sign_up_auth_emulator_user(
            f"chat-threads-b-{run_id}@example.invalid",
            password,
        )
        assert user_a_uid != user_b_uid

        unauthenticated_threads = api_client.get("/api/v2/users/me/chat/threads")
        assert unauthenticated_threads.status_code == 401
        assert unauthenticated_threads.json() == {"detail": "Authentication required"}

        unauthenticated_messages = api_client.get(
            "/api/v2/users/me/chat/threads/thread-a/messages"
        )
        assert unauthenticated_messages.status_code == 401
        assert unauthenticated_messages.json() == {"detail": "Authentication required"}

        v1_threads = api_client.get(
            "/api/v1/users/me/chat/threads",
            headers=_auth_headers(token_a),
        )
        assert v1_threads.status_code == 404

        direct_message_post = api_client.post(
            "/api/v2/users/me/chat/threads/thread-a/messages",
            json={
                "messageId": "msg-direct",
                "role": "user",
                "content": "Not mounted",
                "createdAt": 4000,
            },
            headers=_auth_headers(token_a),
        )
        assert direct_message_post.status_code == 405

        _seed_user_a_chat_threads(firestore_client, user_a_uid)

        threads_page_1 = api_client.get(
            "/api/v2/users/me/chat/threads?limit=2",
            headers=_auth_headers(token_a),
        )
        assert threads_page_1.status_code == 200
        assert threads_page_1.json() == {
            "items": [
                {
                    "id": "thread-a",
                    "title": "Thread A",
                    "createdAt": 1000,
                    "updatedAt": 3000,
                    "lastMessage": "Newest thread",
                    "lastMessageAt": 3000,
                },
                {
                    "id": "thread-b",
                    "title": "Thread B",
                    "createdAt": 900,
                    "updatedAt": 2000,
                    "lastMessage": "Middle thread",
                    "lastMessageAt": 2000,
                },
            ],
            "nextBeforeUpdatedAt": 2000,
        }

        threads_page_2 = api_client.get(
            "/api/v2/users/me/chat/threads?beforeUpdatedAt=2000",
            headers=_auth_headers(token_a),
        )
        assert threads_page_2.status_code == 200
        assert threads_page_2.json() == {
            "items": [
                {
                    "id": "thread-c",
                    "title": "Thread C",
                    "createdAt": 800,
                    "updatedAt": 1000,
                    "lastMessage": "Oldest thread",
                    "lastMessageAt": 1000,
                }
            ],
            "nextBeforeUpdatedAt": None,
        }

        messages_page_1 = api_client.get(
            "/api/v2/users/me/chat/threads/thread-a/messages?limit=2",
            headers=_auth_headers(token_a),
        )
        assert messages_page_1.status_code == 200
        assert messages_page_1.json() == {
            "items": [
                {
                    "id": "msg-new",
                    "role": "user",
                    "content": "Latest user message",
                    "createdAt": 3000,
                    "lastSyncedAt": 3100,
                    "deleted": False,
                },
                {
                    "id": "msg-middle",
                    "role": "assistant",
                    "content": "Invalid role should normalize",
                    "createdAt": 2000,
                    "lastSyncedAt": 2000,
                    "deleted": False,
                },
            ],
            "nextBeforeCreatedAt": 2000,
        }

        messages_page_2 = api_client.get(
            "/api/v2/users/me/chat/threads/thread-a/messages?beforeCreatedAt=2000",
            headers=_auth_headers(token_a),
        )
        assert messages_page_2.status_code == 200
        assert messages_page_2.json() == {
            "items": [
                {
                    "id": "msg-old",
                    "role": "assistant",
                    "content": "Older assistant message",
                    "createdAt": 1000,
                    "lastSyncedAt": 1100,
                    "deleted": False,
                }
            ],
            "nextBeforeCreatedAt": None,
        }

        user_b_threads = api_client.get(
            "/api/v2/users/me/chat/threads",
            headers=_auth_headers(token_b),
        )
        assert user_b_threads.status_code == 200
        assert user_b_threads.json() == {"items": [], "nextBeforeUpdatedAt": None}

        user_b_messages = api_client.get(
            "/api/v2/users/me/chat/threads/thread-a/messages",
            headers=_auth_headers(token_b),
        )
        assert user_b_messages.status_code == 200
        assert user_b_messages.json() == {"items": [], "nextBeforeCreatedAt": None}
    finally:
        _delete_user_chat_tree(firestore_client, user_a_uid)
        _delete_user_chat_tree(firestore_client, user_b_uid)
        if token_a:
            _delete_auth_emulator_user(token_a)
        if token_b:
            _delete_auth_emulator_user(token_b)
        _reset_firebase_singletons()
