import pytest
from pytest_mock import MockerFixture

from app.db import firebase


def test_normalize_private_key_supports_double_escaped_newlines() -> None:
    raw = "-----BEGIN PRIVATE KEY-----\\\\nsecret\\\\n-----END PRIVATE KEY-----\\\\n"
    normalized = firebase._normalize_firebase_private_key(raw)
    assert normalized == "-----BEGIN PRIVATE KEY-----\nsecret\n-----END PRIVATE KEY-----\n"


def test_init_firebase_prefers_inline_service_account_credentials(
    mocker: MockerFixture,
) -> None:
    certificate = object()
    initialized_app = object()

    mocker.patch.object(firebase.firebase_admin, "_apps", [])
    mocker.patch.object(firebase.settings, "FIREBASE_PROJECT_ID", "demo-project")
    mocker.patch.object(firebase.settings, "FIREBASE_STORAGE_BUCKET", "")
    mocker.patch.object(firebase.settings, "FIREBASE_CLIENT_EMAIL", "firebase@example.com")
    mocker.patch.object(
        firebase.settings,
        "FIREBASE_PRIVATE_KEY",
        "-----BEGIN PRIVATE KEY-----\\nsecret\\n-----END PRIVATE KEY-----\\n",
    )
    mocker.patch.object(firebase.settings, "GOOGLE_APPLICATION_CREDENTIALS", "")
    certificate_factory = mocker.patch(
        "app.db.firebase.credentials.Certificate",
        return_value=certificate,
    )
    initialize_app = mocker.patch(
        "app.db.firebase.firebase_admin.initialize_app",
        return_value=initialized_app,
    )

    result = firebase.init_firebase()

    certificate_factory.assert_called_once_with(
        {
            "type": "service_account",
            "project_id": "demo-project",
            "client_email": "firebase@example.com",
            "private_key": "-----BEGIN PRIVATE KEY-----\nsecret\n-----END PRIVATE KEY-----\n",
            "token_uri": "https://oauth2.googleapis.com/token",
        }
    )
    initialize_app.assert_called_once_with(
        credential=certificate,
        options={
            "projectId": "demo-project",
            "storageBucket": "demo-project.appspot.com",
        },
    )
    assert result is initialized_app


def test_init_firebase_falls_back_to_service_account_file(mocker: MockerFixture) -> None:
    certificate = object()
    initialized_app = object()

    mocker.patch.object(firebase.firebase_admin, "_apps", [])
    mocker.patch.object(firebase.settings, "FIREBASE_PROJECT_ID", "demo-project")
    mocker.patch.object(firebase.settings, "FIREBASE_STORAGE_BUCKET", "")
    mocker.patch.object(firebase.settings, "FIREBASE_CLIENT_EMAIL", "")
    mocker.patch.object(firebase.settings, "FIREBASE_PRIVATE_KEY", "")
    mocker.patch.object(
        firebase.settings,
        "GOOGLE_APPLICATION_CREDENTIALS",
        "/app/service-account.json",
    )
    certificate_factory = mocker.patch(
        "app.db.firebase.credentials.Certificate",
        return_value=certificate,
    )
    initialize_app = mocker.patch(
        "app.db.firebase.firebase_admin.initialize_app",
        return_value=initialized_app,
    )

    result = firebase.init_firebase()

    certificate_factory.assert_called_once_with("/app/service-account.json")
    initialize_app.assert_called_once_with(
        credential=certificate,
        options={
            "projectId": "demo-project",
            "storageBucket": "demo-project.appspot.com",
        },
    )
    assert result is initialized_app


def test_init_firebase_uses_emulator_without_service_account_credentials(
    mocker: MockerFixture,
) -> None:
    anonymous_credentials = object()
    initialized_app = object()

    mocker.patch.object(firebase.firebase_admin, "_apps", [])
    mocker.patch.object(firebase.settings, "FIREBASE_PROJECT_ID", "demo-project")
    mocker.patch.object(firebase.settings, "FIREBASE_STORAGE_BUCKET", "")
    mocker.patch.object(firebase.settings, "FIREBASE_CLIENT_EMAIL", "")
    mocker.patch.object(firebase.settings, "FIREBASE_PRIVATE_KEY", "")
    mocker.patch.object(firebase.settings, "GOOGLE_APPLICATION_CREDENTIALS", "")
    mocker.patch.object(firebase.settings, "ENVIRONMENT", "local")
    mocker.patch.dict(
        firebase.os.environ,
        {
            "FIREBASE_AUTH_EMULATOR_HOST": "127.0.0.1:9099",
            "FIRESTORE_EMULATOR_HOST": "127.0.0.1:8080",
        },
    )
    certificate_factory = mocker.patch("app.db.firebase.credentials.Certificate")
    anonymous_credentials_factory = mocker.patch(
        "app.db.firebase.AnonymousCredentials",
        return_value=anonymous_credentials,
    )
    initialize_app = mocker.patch(
        "app.db.firebase.firebase_admin.initialize_app",
        return_value=initialized_app,
    )

    result = firebase.init_firebase()

    certificate_factory.assert_not_called()
    anonymous_credentials_factory.assert_called_once_with()
    initialize_app.assert_called_once()
    credential = initialize_app.call_args.kwargs["credential"]
    assert isinstance(credential, firebase._AnonymousFirebaseCredential)
    assert credential.get_credential() is anonymous_credentials
    assert initialize_app.call_args.kwargs["options"] == {
        "projectId": "demo-project",
        "storageBucket": "demo-project.appspot.com",
    }
    assert result is initialized_app


def test_init_firebase_requires_credentials_with_only_auth_emulator(
    mocker: MockerFixture,
) -> None:
    mocker.patch.object(firebase.firebase_admin, "_apps", [])
    mocker.patch.object(firebase.settings, "FIREBASE_PROJECT_ID", "demo-project")
    mocker.patch.object(firebase.settings, "FIREBASE_STORAGE_BUCKET", "")
    mocker.patch.object(firebase.settings, "FIREBASE_CLIENT_EMAIL", "")
    mocker.patch.object(firebase.settings, "FIREBASE_PRIVATE_KEY", "")
    mocker.patch.object(firebase.settings, "GOOGLE_APPLICATION_CREDENTIALS", "")
    mocker.patch.object(firebase.settings, "ENVIRONMENT", "local")
    mocker.patch.dict(
        firebase.os.environ,
        {"FIREBASE_AUTH_EMULATOR_HOST": "127.0.0.1:9099"},
        clear=True,
    )
    certificate_factory = mocker.patch("app.db.firebase.credentials.Certificate")
    anonymous_credentials_factory = mocker.patch("app.db.firebase.AnonymousCredentials")
    initialize_app = mocker.patch("app.db.firebase.firebase_admin.initialize_app")

    with pytest.raises(ValueError, match="Firebase credentials are not configured"):
        firebase.init_firebase()

    certificate_factory.assert_not_called()
    anonymous_credentials_factory.assert_not_called()
    initialize_app.assert_not_called()


def test_init_firebase_requires_credentials_for_production_data_emulator(
    mocker: MockerFixture,
) -> None:
    mocker.patch.object(firebase.firebase_admin, "_apps", [])
    mocker.patch.object(firebase.settings, "FIREBASE_PROJECT_ID", "demo-project")
    mocker.patch.object(firebase.settings, "FIREBASE_STORAGE_BUCKET", "")
    mocker.patch.object(firebase.settings, "FIREBASE_CLIENT_EMAIL", "")
    mocker.patch.object(firebase.settings, "FIREBASE_PRIVATE_KEY", "")
    mocker.patch.object(firebase.settings, "GOOGLE_APPLICATION_CREDENTIALS", "")
    mocker.patch.object(firebase.settings, "ENVIRONMENT", "production")
    mocker.patch.dict(
        firebase.os.environ,
        {"FIRESTORE_EMULATOR_HOST": "127.0.0.1:8080"},
        clear=True,
    )
    certificate_factory = mocker.patch("app.db.firebase.credentials.Certificate")
    anonymous_credentials_factory = mocker.patch("app.db.firebase.AnonymousCredentials")
    initialize_app = mocker.patch("app.db.firebase.firebase_admin.initialize_app")

    with pytest.raises(ValueError, match="Firebase credentials are not configured"):
        firebase.init_firebase()

    certificate_factory.assert_not_called()
    anonymous_credentials_factory.assert_not_called()
    initialize_app.assert_not_called()
