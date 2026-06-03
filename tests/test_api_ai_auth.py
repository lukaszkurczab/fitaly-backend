from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient
from pytest_mock import MockerFixture

from app.main import app

client = TestClient(app)


@pytest.mark.parametrize(
    ("path", "payload"),
    [
        (
            "/api/v2/ai/chat/runs",
            {
                "threadId": "thread-1",
                "clientMessageId": "client-msg-1",
                "message": "Suggest a dinner",
            },
        ),
        ("/api/v1/ai/text-meal/analyze", {"payload": {"name": "burger"}}),
        ("/api/v1/ai/photo/analyze", {"imageBase64": "base64-image"}),
    ],
)
def test_ai_endpoints_require_authentication(path: str, payload: dict[str, object]) -> None:
    response = client.post(path, json=payload)

    assert response.status_code == 401
    assert response.json() == {"detail": "Authentication required"}
    assert response.headers.get("WWW-Authenticate") == "Bearer"


def test_malformed_bearer_token_is_rejected_without_firebase(
    mock_auth_token_decoder: MagicMock,
    mocker: MockerFixture,
) -> None:
    mocker.stop(mock_auth_token_decoder)

    response = client.get(
        "/api/v1/users/me/profile",
        headers={"Authorization": "Bearer not-a-jwt"},
    )

    assert response.status_code == 401
    assert response.json() == {"detail": "Invalid authentication credentials"}
    assert response.headers.get("WWW-Authenticate") == "Bearer"
