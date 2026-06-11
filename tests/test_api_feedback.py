from unittest.mock import Mock

from fastapi.testclient import TestClient
from pytest_mock import MockerFixture

from app.core.exceptions import FirestoreServiceError
from app.main import app
from app.services.feedback_service import FeedbackValidationError
from app.services.meal_storage import MAX_UPLOAD_BYTES
from tests.types import AuthHeaders

client = TestClient(app)


def _patch_feedback_firestore(mocker: MockerFixture) -> Mock:
    client_mock = mocker.Mock()
    users_collection_ref = mocker.Mock()
    user_ref = mocker.Mock()
    feedback_collection_ref = mocker.Mock()
    document_ref = mocker.Mock()
    document_ref.id = "feedback-1"
    client_mock.collection.return_value = users_collection_ref
    users_collection_ref.document.return_value = user_ref
    user_ref.collection.return_value = feedback_collection_ref
    feedback_collection_ref.document.return_value = document_ref
    mocker.patch("app.services.feedback_service.get_firestore", return_value=client_mock)
    return document_ref


def test_post_feedback_returns_created_payload(
    mocker: MockerFixture,
    auth_headers: AuthHeaders,
) -> None:
    create_feedback = mocker.patch(
        "app.api.routes.feedback.feedback_service.create_feedback",
        return_value={
            "id": "feedback-1",
            "message": "App is great",
            "userUid": "user-1",
            "email": "user@example.com",
            "deviceInfo": {
                "modelName": "iPhone",
                "osName": "iOS",
                "osVersion": "18",
            },
            "createdAt": 1,
            "updatedAt": 2,
            "status": "new",
            "attachmentUrl": "https://cdn/feedback.jpg",
            "attachmentRef": {
                "storagePath": "feedback/user-1/feedback-1/feedback.jpg",
            },
        },
    )

    response = client.post(
        "/api/v1/users/me/feedback",
        data={
            "message": "App is great",
            "deviceModelName": "iPhone",
            "deviceOsName": "iOS",
            "deviceOsVersion": "18",
        },
        files={"file": ("feedback.jpg", b"image", "image/jpeg")},
        headers=auth_headers("user-1"),
    )

    assert response.status_code == 200
    assert response.json() == {
        "feedback": {
            "id": "feedback-1",
            "message": "App is great",
            "userUid": "user-1",
            "email": "user@example.com",
            "deviceInfo": {
                "modelName": "iPhone",
                "osName": "iOS",
                "osVersion": "18",
            },
            "createdAt": 1,
            "updatedAt": 2,
            "status": "new",
            "attachmentUrl": "https://cdn/feedback.jpg",
            "attachmentRef": {
                "storagePath": "feedback/user-1/feedback-1/feedback.jpg",
            },
        },
        "created": True,
    }
    assert "attachmentPath" not in response.json()["feedback"]
    create_feedback.assert_called_once_with(
        user_id="user-1",
        message="App is great",
        email=None,
        device_info={
            "modelName": "iPhone",
            "osName": "iOS",
            "osVersion": "18",
        },
        attachment=mocker.ANY,
    )


def test_post_feedback_returns_400_for_invalid_payload(
    mocker: MockerFixture,
    auth_headers: AuthHeaders,
) -> None:
    mocker.patch(
        "app.api.routes.feedback.feedback_service.create_feedback",
        side_effect=FeedbackValidationError("Feedback message is required."),
    )

    response = client.post(
        "/api/v1/users/me/feedback",
        data={"message": ""},
        headers=auth_headers("user-1"),
    )

    assert response.status_code == 400
    assert response.json() == {"detail": "Feedback message is required."}


def test_post_feedback_returns_400_for_invalid_attachment_mime_without_writes(
    mocker: MockerFixture,
    auth_headers: AuthHeaders,
) -> None:
    document_ref = _patch_feedback_firestore(mocker)
    get_storage_bucket = mocker.patch("app.services.feedback_service.get_storage_bucket")

    response = client.post(
        "/api/v1/users/me/feedback",
        data={"message": "App is great"},
        files={"file": ("feedback.txt", b"plain text", "text/plain")},
        headers=auth_headers("user-1"),
    )

    assert response.status_code == 400
    assert response.json() == {"detail": "Unsupported or unrecognized file type"}
    get_storage_bucket.assert_not_called()
    document_ref.set.assert_not_called()


def test_post_feedback_returns_400_for_spoofed_allowed_attachment_mime_without_writes(
    mocker: MockerFixture,
    auth_headers: AuthHeaders,
) -> None:
    document_ref = _patch_feedback_firestore(mocker)
    get_storage_bucket = mocker.patch("app.services.feedback_service.get_storage_bucket")

    response = client.post(
        "/api/v1/users/me/feedback",
        data={"message": "App is great"},
        files={"file": ("feedback.jpg", b"plain text", "image/jpeg")},
        headers=auth_headers("user-1"),
    )

    assert response.status_code == 400
    assert response.json() == {"detail": "Unsupported or unrecognized file type"}
    get_storage_bucket.assert_not_called()
    document_ref.set.assert_not_called()


def test_post_feedback_returns_400_for_oversized_attachment_without_writes(
    mocker: MockerFixture,
    auth_headers: AuthHeaders,
) -> None:
    document_ref = _patch_feedback_firestore(mocker)
    get_storage_bucket = mocker.patch("app.services.feedback_service.get_storage_bucket")

    response = client.post(
        "/api/v1/users/me/feedback",
        data={"message": "App is great"},
        files={
            "file": (
                "feedback.jpg",
                b"\xff\xd8\xff" + (b"0" * (MAX_UPLOAD_BYTES + 1)),
                "image/jpeg",
            )
        },
        headers=auth_headers("user-1"),
    )

    assert response.status_code == 400
    assert response.json() == {
        "detail": f"File exceeds maximum allowed size of {MAX_UPLOAD_BYTES} bytes"
    }
    get_storage_bucket.assert_not_called()
    document_ref.set.assert_not_called()


def test_post_feedback_returns_500_for_firestore_errors(
    mocker: MockerFixture,
    auth_headers: AuthHeaders,
) -> None:
    mocker.patch(
        "app.api.routes.feedback.feedback_service.create_feedback",
        side_effect=FirestoreServiceError("boom"),
    )

    response = client.post(
        "/api/v1/users/me/feedback",
        data={"message": "App is great"},
        headers=auth_headers("user-1"),
    )

    assert response.status_code == 500
    assert response.json() == {"detail": "Database error"}
