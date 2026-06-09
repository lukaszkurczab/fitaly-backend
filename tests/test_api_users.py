from unittest.mock import MagicMock

from fastapi import HTTPException
from fastapi.testclient import TestClient
from pytest_mock import MockerFixture

from app.core.exceptions import FirestoreServiceError
from app.main import app
from app.services.user_account_service import (
    AvatarMetadataValidationError,
    EmailValidationError,
    UserProfileMutationDedupeConflictError,
)
from tests.types import AuthHeaders

client = TestClient(app)


def test_get_user_profile_returns_401_without_token() -> None:
    response = client.get("/api/v1/users/me/profile")

    assert response.status_code == 401
    assert response.json() == {"detail": "Authentication required"}


def test_get_user_profile_returns_401_for_invalid_token(
    mock_auth_token_decoder: MagicMock,
) -> None:
    mock_auth_token_decoder.side_effect = HTTPException(
        status_code=401,
        detail="Invalid authentication credentials",
    )

    response = client.get(
        "/api/v1/users/me/profile",
        headers={"Authorization": "Bearer invalid-token"},
    )

    assert response.status_code == 401
    assert response.json() == {"detail": "Invalid authentication credentials"}


def test_post_email_pending_returns_updated_payload(
    mocker: MockerFixture,
    auth_headers: AuthHeaders,
) -> None:
    set_email_pending = mocker.patch(
        "app.api.routes.users.user_account_service.set_email_pending",
        return_value="new@example.com",
    )

    response = client.post(
        "/api/v1/users/me/email-pending",
        json={"email": "new@example.com"},
        headers=auth_headers("user-1"),
    )

    assert response.status_code == 200
    assert response.json() == {
        "emailPending": "new@example.com",
        "updated": True,
    }
    set_email_pending.assert_called_once_with("user-1", "new@example.com")


def test_get_user_profile_returns_backend_payload(
    mocker: MockerFixture,
    auth_headers: AuthHeaders,
) -> None:
    canonical_profile = {"language": "pl"}
    get_user_profile_data = mocker.patch(
        "app.api.routes.users.user_account_service.get_user_profile_data",
        return_value={"uid": "user-1", "username": "neo", "profile": canonical_profile},
    )

    response = client.get("/api/v1/users/me/profile", headers=auth_headers("user-1"))

    assert response.status_code == 200
    assert response.json() == {
        "profile": {"uid": "user-1", "username": "neo", "profile": canonical_profile},
    }
    get_user_profile_data.assert_called_once_with(
        "user-1",
        touch_last_login=True,
        auth_email=None,
    )


def test_get_user_profile_uses_token_uid_not_client_supplied_uid(
    mocker: MockerFixture,
    auth_headers: AuthHeaders,
) -> None:
    get_user_profile_data = mocker.patch(
        "app.api.routes.users.user_account_service.get_user_profile_data",
        return_value={"uid": "token-user", "username": "neo"},
    )

    response = client.get(
        "/api/v1/users/me/profile?uid=attacker-user",
        headers=auth_headers("token-user"),
    )

    assert response.status_code == 200
    assert response.json() == {
        "profile": {"uid": "token-user", "username": "neo"},
    }
    get_user_profile_data.assert_called_once_with(
        "token-user",
        touch_last_login=True,
        auth_email=None,
    )


def test_get_user_profile_passes_auth_email_for_pending_cleanup(
    mocker: MockerFixture,
    mock_auth_token_decoder: MagicMock,
) -> None:
    mock_auth_token_decoder.side_effect = None
    mock_auth_token_decoder.return_value = {
        "uid": "user-1",
        "email": "new@example.com",
    }
    get_user_profile_data = mocker.patch(
        "app.api.routes.users.user_account_service.get_user_profile_data",
        return_value={"uid": "user-1", "email": "new@example.com"},
    )

    response = client.get(
        "/api/v1/users/me/profile",
        headers={"Authorization": "Bearer user-1"},
    )

    assert response.status_code == 200
    get_user_profile_data.assert_called_once_with(
        "user-1",
        touch_last_login=True,
        auth_email="new@example.com",
    )


def test_post_user_profile_returns_updated_payload(
    mocker: MockerFixture,
    auth_headers: AuthHeaders,
) -> None:
    canonical_profile = {"language": "pl"}
    upsert_user_profile_data = mocker.patch(
        "app.api.routes.users.user_account_service.upsert_user_profile_data",
        return_value={"uid": "user-1", "username": "neo", "profile": canonical_profile},
    )

    response = client.post(
        "/api/v1/users/me/profile",
        json={"clientMutationId": "profile-mutation-1", "profile": {"language": "pl"}},
        headers=auth_headers("user-1"),
    )

    assert response.status_code == 200
    assert response.json() == {
        "profile": {"uid": "user-1", "username": "neo", "profile": canonical_profile},
        "updated": True,
    }
    upsert_user_profile_data.assert_called_once_with(
        "user-1",
        {"profile": {"language": "pl"}},
        client_mutation_id="profile-mutation-1",
        auth_email=None,
    )


def test_post_user_profile_returns_409_for_reused_mutation_id_conflict(
    mocker: MockerFixture,
    auth_headers: AuthHeaders,
) -> None:
    upsert_user_profile_data = mocker.patch(
        "app.api.routes.users.user_account_service.upsert_user_profile_data",
        side_effect=UserProfileMutationDedupeConflictError(
            "clientMutationId was already used for a different profile mutation"
        ),
    )

    response = client.post(
        "/api/v1/users/me/profile",
        json={"clientMutationId": "profile-mutation-1", "profile": {"language": "pl"}},
        headers=auth_headers("user-1"),
    )

    assert response.status_code == 409
    assert "different profile mutation" in response.json()["detail"]
    upsert_user_profile_data.assert_called_once_with(
        "user-1",
        {"profile": {"language": "pl"}},
        client_mutation_id="profile-mutation-1",
        auth_email=None,
    )


def test_post_user_profile_returns_422_for_unknown_profile_fields(
    mocker: MockerFixture,
    auth_headers: AuthHeaders,
) -> None:
    upsert_user_profile_data = mocker.patch(
        "app.api.routes.users.user_account_service.upsert_user_profile_data",
        return_value={"uid": "user-1"},
    )

    response = client.post(
        "/api/v1/users/me/profile",
        json={"clientMutationId": "profile-mutation-unknown", "username": "neo"},
        headers=auth_headers("user-1"),
    )

    assert response.status_code == 422
    assert "extra_forbidden" in str(response.json())
    upsert_user_profile_data.assert_not_called()


def test_post_user_profile_returns_422_for_empty_payload(
    mocker: MockerFixture,
    auth_headers: AuthHeaders,
) -> None:
    upsert_user_profile_data = mocker.patch(
        "app.api.routes.users.user_account_service.upsert_user_profile_data",
        return_value={"uid": "user-1"},
    )

    response = client.post(
        "/api/v1/users/me/profile",
        json={"clientMutationId": "profile-mutation-empty"},
        headers=auth_headers("user-1"),
    )

    assert response.status_code == 422
    assert "must not be empty" in str(response.json())
    upsert_user_profile_data.assert_not_called()


def test_post_user_profile_returns_422_for_missing_client_mutation_id(
    mocker: MockerFixture,
    auth_headers: AuthHeaders,
) -> None:
    upsert_user_profile_data = mocker.patch(
        "app.api.routes.users.user_account_service.upsert_user_profile_data",
        return_value={"uid": "user-1"},
    )

    response = client.post(
        "/api/v1/users/me/profile",
        json={"profile": {"language": "pl"}},
        headers=auth_headers("user-1"),
    )

    assert response.status_code == 422
    assert "clientMutationId" in str(response.json())
    upsert_user_profile_data.assert_not_called()


def test_post_user_profile_returns_422_for_blank_client_mutation_id(
    mocker: MockerFixture,
    auth_headers: AuthHeaders,
) -> None:
    upsert_user_profile_data = mocker.patch(
        "app.api.routes.users.user_account_service.upsert_user_profile_data",
        return_value={"uid": "user-1"},
    )

    response = client.post(
        "/api/v1/users/me/profile",
        json={"clientMutationId": "   ", "profile": {"language": "pl"}},
        headers=auth_headers("user-1"),
    )

    assert response.status_code == 422
    assert "clientMutationId" in str(response.json())
    upsert_user_profile_data.assert_not_called()


def test_post_user_profile_returns_422_for_invalid_enum_value(
    mocker: MockerFixture,
    auth_headers: AuthHeaders,
) -> None:
    upsert_user_profile_data = mocker.patch(
        "app.api.routes.users.user_account_service.upsert_user_profile_data",
        return_value={"uid": "user-1"},
    )

    response = client.post(
        "/api/v1/users/me/profile",
        json={
            "clientMutationId": "profile-mutation-invalid-enum",
            "profile": {"nutritionProfile": {"unitsSystem": "si"}},
        },
        headers=auth_headers("user-1"),
    )

    assert response.status_code == 422
    assert "unitsSystem" in str(response.json())
    upsert_user_profile_data.assert_not_called()


def test_post_user_profile_rejects_ai_consent_patch(
    mocker: MockerFixture,
    auth_headers: AuthHeaders,
) -> None:
    upsert_user_profile_data = mocker.patch(
        "app.api.routes.users.user_account_service.upsert_user_profile_data",
        return_value={"uid": "user-1"},
    )

    response = client.post(
        "/api/v1/users/me/profile",
        json={
            "clientMutationId": "profile-mutation-ai-consent",
            "profile": {
                "aiConsent": {
                    "status": "granted",
                    "grantedAt": "2026-05-01T10:00:00Z",
                    "revokedAt": None,
                }
            }
        },
        headers=auth_headers("user-1"),
    )

    assert response.status_code == 422
    assert "aiConsent" in str(response.json())
    upsert_user_profile_data.assert_not_called()


def test_post_user_profile_rejects_readiness_patch_even_with_editable_field(
    mocker: MockerFixture,
    auth_headers: AuthHeaders,
) -> None:
    upsert_user_profile_data = mocker.patch(
        "app.api.routes.users.user_account_service.upsert_user_profile_data",
        return_value={"uid": "user-1"},
    )

    response = client.post(
        "/api/v1/users/me/profile",
        json={
            "clientMutationId": "profile-mutation-readiness",
            "profile": {
                "language": "pl",
                "readiness": {
                    "status": "ready",
                    "onboardingCompletedAt": "2026-05-01T10:00:00Z",
                    "readyAt": "2026-05-01T10:00:00Z",
                },
            }
        },
        headers=auth_headers("user-1"),
    )

    assert response.status_code == 422
    assert "readiness" in str(response.json())
    upsert_user_profile_data.assert_not_called()


def test_post_ai_consent_grant_returns_minimal_payload(
    mocker: MockerFixture,
    auth_headers: AuthHeaders,
) -> None:
    grant_ai_consent = mocker.patch(
        "app.api.routes.users.ConsentService.grant_ai_consent",
        return_value={
            "status": "granted",
            "grantedAt": "2026-05-01T10:00:00Z",
            "revokedAt": None,
        },
    )

    response = client.post(
        "/api/v1/users/me/ai-consent/grant",
        headers=auth_headers("user-1"),
    )

    assert response.status_code == 200
    assert response.json() == {
        "aiConsent": {
            "status": "granted",
            "grantedAt": "2026-05-01T10:00:00Z",
            "revokedAt": None,
        },
    }
    grant_ai_consent.assert_called_once_with(user_id="user-1", auth_email=None)


def test_post_ai_consent_revoke_returns_minimal_payload(
    mocker: MockerFixture,
    auth_headers: AuthHeaders,
) -> None:
    revoke_ai_consent = mocker.patch(
        "app.api.routes.users.ConsentService.revoke_ai_consent",
        return_value={
            "status": "revoked",
            "grantedAt": "2026-05-01T10:00:00Z",
            "revokedAt": "2026-05-02T10:00:00Z",
        },
    )

    response = client.post(
        "/api/v1/users/me/ai-consent/revoke",
        headers=auth_headers("user-1"),
    )

    assert response.status_code == 200
    assert response.json() == {
        "aiConsent": {
            "status": "revoked",
            "grantedAt": "2026-05-01T10:00:00Z",
            "revokedAt": "2026-05-02T10:00:00Z",
        },
    }
    revoke_ai_consent.assert_called_once_with(user_id="user-1", auth_email=None)


def test_ai_consent_endpoints_preserve_transition_semantics(
    mocker: MockerFixture,
    auth_headers: AuthHeaders,
) -> None:
    state: dict[str, str | None] = {
        "status": "not_granted",
        "grantedAt": None,
        "revokedAt": None,
    }
    timestamps = iter(
        [
            "2026-05-01T10:00:00Z",
            "2026-05-02T10:00:00Z",
            "2026-05-03T10:00:00Z",
        ]
    )
    calls: list[tuple[str, str, str | None]] = []

    class StatefulConsentService:
        def __init__(self, _user_profile_service: object) -> None:
            pass

        async def grant_ai_consent(
            self,
            *,
            user_id: str,
            auth_email: str | None = None,
        ) -> dict[str, str | None]:
            calls.append(("grant", user_id, auth_email))
            if not (
                state["status"] == "granted"
                and state["grantedAt"] is not None
                and state["revokedAt"] is None
            ):
                state.update(
                    {
                        "status": "granted",
                        "grantedAt": next(timestamps),
                        "revokedAt": None,
                    }
                )
            return dict(state)

        async def revoke_ai_consent(
            self,
            *,
            user_id: str,
            auth_email: str | None = None,
        ) -> dict[str, str | None]:
            calls.append(("revoke", user_id, auth_email))
            if state["status"] != "revoked":
                state.update(
                    {
                        "status": "revoked",
                        "grantedAt": state["grantedAt"],
                        "revokedAt": next(timestamps),
                    }
                )
            return dict(state)

    mocker.patch("app.api.routes.users.ConsentService", StatefulConsentService)

    first_grant = client.post(
        "/api/v1/users/me/ai-consent/grant",
        headers=auth_headers("user-1"),
    )
    assert first_grant.status_code == 200
    assert first_grant.json() == {
        "aiConsent": {
            "status": "granted",
            "grantedAt": "2026-05-01T10:00:00Z",
            "revokedAt": None,
        }
    }

    repeat_grant = client.post(
        "/api/v1/users/me/ai-consent/grant",
        headers=auth_headers("user-1"),
    )
    assert repeat_grant.status_code == 200
    assert repeat_grant.json() == first_grant.json()

    revoke = client.post(
        "/api/v1/users/me/ai-consent/revoke",
        headers=auth_headers("user-1"),
    )
    assert revoke.status_code == 200
    assert revoke.json() == {
        "aiConsent": {
            "status": "revoked",
            "grantedAt": "2026-05-01T10:00:00Z",
            "revokedAt": "2026-05-02T10:00:00Z",
        }
    }

    repeat_revoke = client.post(
        "/api/v1/users/me/ai-consent/revoke",
        headers=auth_headers("user-1"),
    )
    assert repeat_revoke.status_code == 200
    assert repeat_revoke.json() == revoke.json()

    regrant = client.post(
        "/api/v1/users/me/ai-consent/grant",
        headers=auth_headers("user-1"),
    )
    assert regrant.status_code == 200
    assert regrant.json() == {
        "aiConsent": {
            "status": "granted",
            "grantedAt": "2026-05-03T10:00:00Z",
            "revokedAt": None,
        }
    }
    assert calls == [
        ("grant", "user-1", None),
        ("grant", "user-1", None),
        ("revoke", "user-1", None),
        ("revoke", "user-1", None),
        ("grant", "user-1", None),
    ]


def test_post_old_ai_consent_route_is_not_exposed(
    auth_headers: AuthHeaders,
) -> None:
    response = client.post(
        "/api/v1/users/me/ai-health-data-consent",
        json={"accepted": True},
        headers=auth_headers("user-1"),
    )

    assert response.status_code == 404


def test_post_email_pending_returns_400_for_invalid_email(
    mocker: MockerFixture,
    auth_headers: AuthHeaders,
) -> None:
    mocker.patch(
        "app.api.routes.users.user_account_service.set_email_pending",
        side_effect=EmailValidationError("Invalid email address."),
    )

    response = client.post(
        "/api/v1/users/me/email-pending",
        json={"email": "bad"},
        headers=auth_headers("user-1"),
    )

    assert response.status_code == 400
    assert response.json() == {"detail": "Invalid email address."}


def test_post_email_pending_returns_500_for_firestore_errors(
    mocker: MockerFixture,
    auth_headers: AuthHeaders,
) -> None:
    mocker.patch(
        "app.api.routes.users.user_account_service.set_email_pending",
        side_effect=FirestoreServiceError("boom"),
    )

    response = client.post(
        "/api/v1/users/me/email-pending",
        json={"email": "new@example.com"},
        headers=auth_headers("user-1"),
    )

    assert response.status_code == 500
    assert response.json() == {"detail": "Database error"}


def test_post_delete_user_returns_success(mocker: MockerFixture, auth_headers: AuthHeaders) -> None:
    delete_account_data = mocker.patch(
        "app.api.routes.users.user_account_service.delete_account_data",
        return_value=None,
    )

    response = client.post("/api/v1/users/me/delete", headers=auth_headers("user-1"))

    assert response.status_code == 200
    assert response.json() == {"deleted": True}
    delete_account_data.assert_called_once_with("user-1")


def test_post_avatar_metadata_returns_updated_payload(
    mocker: MockerFixture,
    auth_headers: AuthHeaders,
) -> None:
    set_avatar_metadata = mocker.patch(
        "app.api.routes.users.user_account_service.set_avatar_metadata",
        return_value=("https://cdn/avatar.jpg", "2026-03-03T12:00:00Z"),
    )

    response = client.post(
        "/api/v1/users/me/avatar-metadata",
        json={"avatarUrl": "https://cdn/avatar.jpg"},
        headers=auth_headers("user-1"),
    )

    assert response.status_code == 200
    assert response.json() == {
        "avatarUrl": "https://cdn/avatar.jpg",
        "avatarlastSyncedAt": "2026-03-03T12:00:00Z",
        "updated": True,
    }
    set_avatar_metadata.assert_called_once_with("user-1", "https://cdn/avatar.jpg")


def test_post_avatar_metadata_returns_400_for_invalid_url(
    mocker: MockerFixture,
    auth_headers: AuthHeaders,
) -> None:
    mocker.patch(
        "app.api.routes.users.user_account_service.set_avatar_metadata",
        side_effect=AvatarMetadataValidationError("Invalid avatar URL."),
    )

    response = client.post(
        "/api/v1/users/me/avatar-metadata",
        json={"avatarUrl": "file:///avatar.jpg"},
        headers=auth_headers("user-1"),
    )

    assert response.status_code == 400
    assert response.json() == {"detail": "Invalid avatar URL."}


def test_post_avatar_upload_returns_updated_payload(
    mocker: MockerFixture,
    auth_headers: AuthHeaders,
) -> None:
    upload_avatar = mocker.patch(
        "app.api.routes.users.user_account_service.upload_avatar",
        return_value=("https://cdn/avatar.jpg", "2026-03-03T12:00:00Z"),
    )

    response = client.post(
        "/api/v1/users/me/avatar",
        files={"file": ("avatar.jpg", b"avatar-bytes", "image/jpeg")},
        headers=auth_headers("user-1"),
    )

    assert response.status_code == 200
    assert response.json() == {
        "avatarUrl": "https://cdn/avatar.jpg",
        "avatarlastSyncedAt": "2026-03-03T12:00:00Z",
        "updated": True,
    }
    upload_avatar.assert_called_once()
    assert upload_avatar.call_args.args[0] == "user-1"


def test_post_avatar_upload_returns_500_for_firestore_errors(
    mocker: MockerFixture,
    auth_headers: AuthHeaders,
) -> None:
    mocker.patch(
        "app.api.routes.users.user_account_service.upload_avatar",
        side_effect=FirestoreServiceError("boom"),
    )

    response = client.post(
        "/api/v1/users/me/avatar",
        files={"file": ("avatar.jpg", b"avatar-bytes", "image/jpeg")},
        headers=auth_headers("user-1"),
    )

    assert response.status_code == 500
    assert response.json() == {"detail": "Database error"}


def test_get_user_export_returns_backend_payload(
    mocker: MockerFixture,
    auth_headers: AuthHeaders,
) -> None:
    get_user_export_data = mocker.patch(
        "app.api.routes.users.user_account_service.get_user_export_data",
        return_value=(
            {"uid": "user-1", "username": "neo"},
            [{"id": "meal-1"}],
            [{"id": "saved-1"}],
            [{"id": "chat-1"}],
            [{"id": "memory-1"}],
            [{"id": "run-1"}],
            [{"id": "notif-1"}],
            {"motivationEnabled": True},
            [{"id": "feedback-1"}],
            [{"clientMutationId": "profile-mutation-1", "kind": "profile_update"}],
        ),
    )

    response = client.get("/api/v1/users/me/export", headers=auth_headers("user-1"))

    assert response.status_code == 200
    assert response.json() == {
        "profile": {"uid": "user-1", "username": "neo"},
        "meals": [{"id": "meal-1"}],
        "myMeals": [{"id": "saved-1"}],
        "chatMessages": [{"id": "chat-1"}],
        "chatMemory": [{"id": "memory-1"}],
        "aiRuns": [{"id": "run-1"}],
        "notifications": [{"id": "notif-1"}],
        "notificationPrefs": {"motivationEnabled": True},
        "feedback": [{"id": "feedback-1"}],
        "mealMutationDedupe": [
            {"clientMutationId": "profile-mutation-1", "kind": "profile_update"}
        ],
    }
    get_user_export_data.assert_called_once_with("user-1")


def test_get_user_export_returns_500_for_firestore_errors(
    mocker: MockerFixture,
    auth_headers: AuthHeaders,
) -> None:
    mocker.patch(
        "app.api.routes.users.user_account_service.get_user_export_data",
        side_effect=FirestoreServiceError("boom"),
    )

    response = client.get("/api/v1/users/me/export", headers=auth_headers("user-1"))

    assert response.status_code == 500
    assert response.json() == {"detail": "Database error"}


def test_post_delete_user_returns_500_for_firestore_errors(
    mocker: MockerFixture,
    auth_headers: AuthHeaders,
) -> None:
    mocker.patch(
        "app.api.routes.users.user_account_service.delete_account_data",
        side_effect=FirestoreServiceError("boom"),
    )

    response = client.post("/api/v1/users/me/delete", headers=auth_headers("user-1"))

    assert response.status_code == 500
    assert response.json() == {"detail": "Database error"}


def test_get_user_export_requires_authentication() -> None:
    response = client.get("/api/v1/users/me/export")

    assert response.status_code == 401
    assert response.json() == {"detail": "Authentication required"}
