import asyncio
from unittest.mock import Mock

import pytest
from pytest_mock import MockerFixture

from app.services import feedback_service


def _assert_feedback_storage_path_is_cleanup_safe(
    storage_path: str,
    *,
    user_id: str = "user-1",
    feedback_id: str = "feedback-1",
) -> None:
    parts = storage_path.split("/")
    assert parts[:3] == ["feedback", user_id, feedback_id]
    assert len(parts) == 4
    assert all(part.strip() and part not in {".", ".."} and "\\" not in part for part in parts)


def test_create_feedback_with_attachment_stores_canonical_attachment_ref(
    mocker: MockerFixture,
) -> None:
    client = mocker.Mock()
    users_collection_ref = mocker.Mock()
    user_ref = mocker.Mock()
    feedback_collection_ref = mocker.Mock()
    document_ref = mocker.Mock()
    document_ref.id = "feedback-1"
    client.collection.return_value = users_collection_ref
    users_collection_ref.document.return_value = user_ref
    user_ref.collection.return_value = feedback_collection_ref
    feedback_collection_ref.document.return_value = document_ref
    mocker.patch("app.services.feedback_service.get_firestore", return_value=client)

    bucket = mocker.Mock()
    bucket.name = "fitaly-test.appspot.com"
    blob = mocker.Mock()
    bucket.blob.return_value = blob
    mocker.patch("app.services.feedback_service.get_storage_bucket", return_value=bucket)
    mocker.patch("app.services.feedback_service.uuid4", return_value="token-1")
    mocker.patch(
        "app.services.feedback_service._utc_timestamp_ms",
        side_effect=[1000, 2000],
    )

    upload = Mock()
    upload.filename = "feedback.jpg"
    upload.content_type = "image/jpeg"
    upload.file = Mock()

    result = asyncio.run(
        feedback_service.create_feedback(
            user_id="user-1",
            message="App is great",
            email="user@example.com",
            device_info={
                "modelName": "iPhone",
                "osName": "iOS",
                "osVersion": "18",
            },
            attachment=upload,
        )
    )

    assert result == {
        "id": "feedback-1",
        "message": "App is great",
        "userUid": "user-1",
        "email": "user@example.com",
        "deviceInfo": {
            "modelName": "iPhone",
            "osName": "iOS",
            "osVersion": "18",
        },
        "createdAt": 1000,
        "updatedAt": 2000,
        "status": "new",
        "attachmentUrl": (
            "https://firebasestorage.googleapis.com/v0/b/fitaly-test.appspot.com/o/"
            "feedback%2Fuser-1%2Ffeedback-1%2Ffeedback.jpg?alt=media&token=token-1"
        ),
        "attachmentRef": {
            "storagePath": "feedback/user-1/feedback-1/feedback.jpg",
        },
    }
    assert "attachmentPath" not in result
    bucket.blob.assert_called_once_with("feedback/user-1/feedback-1/feedback.jpg")
    upload.file.seek.assert_called_once_with(0)
    blob.upload_from_file.assert_called_once_with(upload.file, content_type="image/jpeg")
    blob.patch.assert_called_once_with()
    upload.file.close.assert_called_once_with()
    document_ref.set.assert_called_once_with(result, merge=True)


@pytest.mark.parametrize(
    ("upload_filename", "expected_storage_filename"),
    [
        ("..", "attachment.jpg"),
        ("../..", "attachment.jpg"),
        (".", "attachment.jpg"),
        ("", "attachment.jpg"),
        ("feedback/unsafe.png", "attachment.png"),
        (r"feedback\unsafe.png", "attachment.png"),
    ],
)
def test_create_feedback_with_attachment_normalizes_unsafe_filename_segments(
    mocker: MockerFixture,
    upload_filename: str,
    expected_storage_filename: str,
) -> None:
    client = mocker.Mock()
    users_collection_ref = mocker.Mock()
    user_ref = mocker.Mock()
    feedback_collection_ref = mocker.Mock()
    document_ref = mocker.Mock()
    document_ref.id = "feedback-1"
    client.collection.return_value = users_collection_ref
    users_collection_ref.document.return_value = user_ref
    user_ref.collection.return_value = feedback_collection_ref
    feedback_collection_ref.document.return_value = document_ref
    mocker.patch("app.services.feedback_service.get_firestore", return_value=client)

    bucket = mocker.Mock()
    bucket.name = "fitaly-test.appspot.com"
    blob = mocker.Mock()
    bucket.blob.return_value = blob
    mocker.patch("app.services.feedback_service.get_storage_bucket", return_value=bucket)
    mocker.patch("app.services.feedback_service.uuid4", return_value="token-1")
    mocker.patch(
        "app.services.feedback_service._utc_timestamp_ms",
        side_effect=[1000, 2000],
    )

    upload = Mock()
    upload.filename = upload_filename
    upload.content_type = "image/jpeg"
    upload.file = Mock()

    result = asyncio.run(
        feedback_service.create_feedback(
            user_id="user-1",
            message="App is great",
            attachment=upload,
        )
    )

    expected_storage_path = f"feedback/user-1/feedback-1/{expected_storage_filename}"
    assert result["attachmentRef"] == {"storagePath": expected_storage_path}
    assert "attachmentPath" not in result
    _assert_feedback_storage_path_is_cleanup_safe(expected_storage_path)
    bucket.blob.assert_called_once_with(expected_storage_path)
    document_ref.set.assert_called_once_with(result, merge=True)
