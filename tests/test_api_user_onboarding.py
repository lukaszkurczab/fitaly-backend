from fastapi.testclient import TestClient
from pytest_mock import MockerFixture

from app.core.exceptions import FirestoreServiceError
from app.main import app
from app.services.user_account_service import (
    OnboardingUsernameUnavailableError,
    OnboardingValidationError,
)

client = TestClient(app)


def _canonical_profile(language: str = "pl") -> dict[str, object]:
    return {"language": language}


def test_post_user_onboarding_returns_backend_payload(
    mocker: MockerFixture,
) -> None:
    mocker.patch(
        "app.api.deps.auth.decode_firebase_token",
        return_value={"uid": "user-1", "email": "user@example.com"},
    )
    initialize = mocker.patch(
        "app.api.routes.users.user_account_service.initialize_onboarding_profile",
        return_value=(
            "neo",
            {
                "uid": "user-1",
                "email": "user@example.com",
                "username": "neo",
                "profile": _canonical_profile("pl"),
                "plan": "free",
            },
        ),
    )

    response = client.post(
        "/api/v1/users/me/onboarding",
        json={"username": "Neo", "language": "pl"},
        headers={"Authorization": "Bearer user-1"},
    )

    assert response.status_code == 200
    assert response.json() == {
        "username": "neo",
        "profile": {
            "uid": "user-1",
            "email": "user@example.com",
            "username": "neo",
            "profile": _canonical_profile("pl"),
            "plan": "free",
        },
        "updated": True,
    }
    initialize.assert_called_once_with(
        "user-1",
        username="Neo",
        language="pl",
        auth_email="user@example.com",
    )


def test_post_user_onboarding_repeated_same_uid_username_succeeds(
    mocker: MockerFixture,
) -> None:
    mocker.patch(
        "app.api.deps.auth.decode_firebase_token",
        return_value={"uid": "user-1", "email": "user@example.com"},
    )
    initialize = mocker.patch(
        "app.api.routes.users.user_account_service.initialize_onboarding_profile",
        return_value=(
            "neo",
            {
                "uid": "user-1",
                "email": "user@example.com",
                "username": "neo",
                "profile": _canonical_profile("pl"),
                "plan": "free",
            },
        ),
    )

    first_response = client.post(
        "/api/v1/users/me/onboarding",
        json={"username": "Neo", "language": "pl"},
        headers={"Authorization": "Bearer user-1"},
    )
    second_response = client.post(
        "/api/v1/users/me/onboarding",
        json={"username": "Neo", "language": "pl"},
        headers={"Authorization": "Bearer user-1"},
    )

    assert first_response.status_code == 200
    assert second_response.status_code == 200
    assert first_response.json()["username"] == second_response.json()["username"] == "neo"
    assert initialize.call_count == 2


def test_post_user_onboarding_same_username_other_uid_returns_409(
    mocker: MockerFixture,
) -> None:
    def decode_token(token: str) -> dict[str, str]:
        return {"uid": token, "email": f"{token}@example.com"}

    mocker.patch(
        "app.api.deps.auth.decode_firebase_token",
        side_effect=decode_token,
    )

    def initialize_side_effect(
        user_id: str,
        *,
        username: str,
        language: str | None = None,
        auth_email: str | None = None,
    ) -> tuple[str, dict[str, object | None]]:
        if user_id == "other-user":
            raise OnboardingUsernameUnavailableError("Username unavailable.")
        return (
            username.strip().lower(),
            {
                "uid": user_id,
                "email": auth_email,
                "username": username.strip().lower(),
                "profile": _canonical_profile(language or "en"),
                "plan": "free",
            },
        )

    mocker.patch(
        "app.api.routes.users.user_account_service.initialize_onboarding_profile",
        side_effect=initialize_side_effect,
    )

    first_response = client.post(
        "/api/v1/users/me/onboarding",
        json={"username": "Neo", "language": "pl"},
        headers={"Authorization": "Bearer user-1"},
    )
    second_response = client.post(
        "/api/v1/users/me/onboarding",
        json={"username": "Neo", "language": "pl"},
        headers={"Authorization": "Bearer other-user"},
    )

    assert first_response.status_code == 200
    assert second_response.status_code == 409
    assert second_response.json() == {"detail": "Username unavailable."}


def test_post_user_onboarding_same_uid_different_username_succeeds(
    mocker: MockerFixture,
) -> None:
    mocker.patch(
        "app.api.deps.auth.decode_firebase_token",
        return_value={"uid": "user-1", "email": "user@example.com"},
    )
    initialize = mocker.patch(
        "app.api.routes.users.user_account_service.initialize_onboarding_profile",
        side_effect=[
            (
                "neo",
                {
                    "uid": "user-1",
                    "email": "user@example.com",
                    "username": "neo",
                    "profile": _canonical_profile("pl"),
                    "plan": "free",
                },
            ),
            (
                "trinity",
                {
                    "uid": "user-1",
                    "email": "user@example.com",
                    "username": "trinity",
                    "profile": _canonical_profile("pl"),
                    "plan": "free",
                },
            ),
        ],
    )

    first_response = client.post(
        "/api/v1/users/me/onboarding",
        json={"username": "Neo", "language": "pl"},
        headers={"Authorization": "Bearer user-1"},
    )
    second_response = client.post(
        "/api/v1/users/me/onboarding",
        json={"username": "Trinity", "language": "pl"},
        headers={"Authorization": "Bearer user-1"},
    )

    assert first_response.status_code == 200
    assert first_response.json()["username"] == "neo"
    assert second_response.status_code == 200
    assert second_response.json()["username"] == "trinity"
    assert initialize.call_count == 2


def test_post_user_onboarding_returns_400_for_invalid_username(
    mocker: MockerFixture,
) -> None:
    mocker.patch(
        "app.api.routes.users.user_account_service.initialize_onboarding_profile",
        side_effect=OnboardingValidationError("Username must be at least 3 characters long."),
    )

    response = client.post(
        "/api/v1/users/me/onboarding",
        json={"username": "ab"},
        headers={"Authorization": "Bearer user-1"},
    )

    assert response.status_code == 400
    assert response.json() == {"detail": "Username must be at least 3 characters long."}


def test_post_user_onboarding_returns_409_when_username_taken(
    mocker: MockerFixture,
) -> None:
    mocker.patch(
        "app.api.routes.users.user_account_service.initialize_onboarding_profile",
        side_effect=OnboardingUsernameUnavailableError("Username unavailable."),
    )

    response = client.post(
        "/api/v1/users/me/onboarding",
        json={"username": "neo"},
        headers={"Authorization": "Bearer user-1"},
    )

    assert response.status_code == 409
    assert response.json() == {"detail": "Username unavailable."}


def test_post_user_onboarding_returns_500_for_firestore_errors(
    mocker: MockerFixture,
) -> None:
    mocker.patch(
        "app.api.routes.users.user_account_service.initialize_onboarding_profile",
        side_effect=FirestoreServiceError("db failed"),
    )

    response = client.post(
        "/api/v1/users/me/onboarding",
        json={"username": "neo"},
        headers={"Authorization": "Bearer user-1"},
    )

    assert response.status_code == 500
    assert response.json() == {"detail": "Database error"}
