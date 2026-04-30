from fastapi.testclient import TestClient
import pytest
from pytest_mock import MockerFixture

from app.core.exceptions import FirestoreServiceError
from app.main import app
from tests.types import AuthHeaders

client = TestClient(app)
pytestmark = pytest.mark.ai_v2


def test_v1_chat_thread_projection_is_not_mounted(auth_headers: AuthHeaders) -> None:
    response = client.get("/api/v1/users/me/chat/threads", headers=auth_headers("user-1"))

    assert response.status_code == 404


def test_get_chat_threads_returns_backend_payload(
    mocker: MockerFixture,
    auth_headers: AuthHeaders,
) -> None:
    list_threads = mocker.patch(
        "app.api.routes.chat_threads.chat_thread_service.list_threads",
        return_value=(
            [
                {
                    "id": "thread-1",
                    "title": "First chat",
                    "createdAt": 100,
                    "updatedAt": 200,
                    "lastMessage": "hello",
                    "lastMessageAt": 200,
                }
            ],
            200,
        ),
    )

    response = client.get("/api/v2/users/me/chat/threads", headers=auth_headers("user-1"))

    assert response.status_code == 200
    assert response.json() == {
        "items": [
            {
                "id": "thread-1",
                "title": "First chat",
                "createdAt": 100,
                "updatedAt": 200,
                "lastMessage": "hello",
                "lastMessageAt": 200,
            }
        ],
        "nextBeforeUpdatedAt": 200,
    }
    list_threads.assert_called_once_with(
        "user-1",
        limit_count=20,
        before_updated_at=None,
    )


def test_get_chat_messages_returns_backend_payload(
    mocker: MockerFixture,
    auth_headers: AuthHeaders,
) -> None:
    list_messages = mocker.patch(
        "app.api.routes.chat_threads.chat_thread_service.list_messages",
        return_value=(
            [
                {
                    "id": "msg-1",
                    "role": "user",
                    "content": "hello",
                    "createdAt": 100,
                    "lastSyncedAt": 100,
                    "deleted": False,
                }
            ],
            100,
        ),
    )

    response = client.get(
        "/api/v2/users/me/chat/threads/thread-1/messages",
        headers=auth_headers("user-1"),
    )

    assert response.status_code == 200
    assert response.json() == {
        "items": [
            {
                "id": "msg-1",
                "role": "user",
                "content": "hello",
                "createdAt": 100,
                "lastSyncedAt": 100,
                "deleted": False,
            }
        ],
        "nextBeforeCreatedAt": 100,
    }
    list_messages.assert_called_once_with(
        "user-1",
        "thread-1",
        limit_count=50,
        before_created_at=None,
    )


def test_direct_chat_message_persist_endpoint_is_not_mounted(auth_headers: AuthHeaders) -> None:
    response = client.post(
        "/api/v2/users/me/chat/threads/thread-1/messages",
        json={
            "messageId": "msg-1",
            "role": "user",
            "content": "hello",
            "createdAt": 100,
            "title": "First chat",
        },
        headers=auth_headers("user-1"),
    )

    assert response.status_code == 405


def test_get_chat_threads_returns_500_for_firestore_errors(
    mocker: MockerFixture,
    auth_headers: AuthHeaders,
) -> None:
    mocker.patch(
        "app.api.routes.chat_threads.chat_thread_service.list_threads",
        side_effect=FirestoreServiceError("boom"),
    )

    response = client.get("/api/v2/users/me/chat/threads", headers=auth_headers("user-1"))

    assert response.status_code == 500
    assert response.json() == {"detail": "Database error"}
