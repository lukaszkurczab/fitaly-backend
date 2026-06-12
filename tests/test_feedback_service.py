import asyncio
from io import BytesIO
from typing import BinaryIO
from unittest.mock import Mock

from fastapi import UploadFile
import pytest
from pytest_mock import MockerFixture
from starlette.datastructures import Headers

from app.services import feedback_service
from app.services.meal_storage import MAX_UPLOAD_BYTES


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


def _upload(
    content: bytes,
    *,
    filename: str,
    content_type: str,
) -> UploadFile:
    return UploadFile(
        BytesIO(content),
        filename=filename,
        headers=Headers({"content-type": content_type}),
    )


def _patch_feedback_firestore(mocker: MockerFixture) -> Mock:
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
    return document_ref


def test_create_feedback_with_attachment_stores_canonical_attachment_ref(
    mocker: MockerFixture,
) -> None:
    document_ref = _patch_feedback_firestore(mocker)

    bucket = mocker.Mock()
    bucket.name = "fitaly-test.appspot.com"
    blob = mocker.Mock()
    bucket.blob.return_value = blob
    uploaded: dict[str, object] = {}

    def upload_from_file(file_obj: BinaryIO, *, content_type: str | None = None) -> None:
        uploaded["bytes"] = file_obj.read()
        uploaded["content_type"] = content_type

    blob.upload_from_file.side_effect = upload_from_file
    mocker.patch("app.services.feedback_service.get_storage_bucket", return_value=bucket)
    mocker.patch("app.services.feedback_service.uuid4", return_value="token-1")
    mocker.patch(
        "app.services.feedback_service._utc_timestamp_ms",
        side_effect=[1000, 2000],
    )

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
            attachment=_upload(
                b"\xff\xd8\xfffeedback\xff\xd9",
                filename="feedback.jpg",
                content_type="image/jpeg",
            ),
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
    assert uploaded == {"bytes": b"\xff\xd8\xfffeedback\xff\xd9", "content_type": "image/jpeg"}
    blob.patch.assert_called_once_with()
    document_ref.set.assert_called_once_with(result, merge=True)


@pytest.mark.parametrize(
    ("filename", "content_type", "content", "expected_content_type"),
    [
        ("feedback.jpg", "image/jpeg", b"\xff\xd8\xfffeedback\xff\xd9", "image/jpeg"),
        ("feedback.png", "image/png", b"\x89PNG\r\n\x1a\nfeedback", "image/png"),
        ("feedback.webp", "image/webp", b"RIFF\x10\x00\x00\x00WEBPfeedback", "image/webp"),
        ("feedback.gif", "image/gif", b"GIF89afeedback", "image/gif"),
    ],
)
def test_create_feedback_uploads_supported_attachment_images_with_full_bytes(
    mocker: MockerFixture,
    filename: str,
    content_type: str,
    content: bytes,
    expected_content_type: str,
) -> None:
    document_ref = _patch_feedback_firestore(mocker)

    bucket = mocker.Mock()
    bucket.name = "fitaly-test.appspot.com"
    blob = mocker.Mock()
    bucket.blob.return_value = blob
    uploaded: dict[str, object] = {}

    def upload_from_file(file_obj: BinaryIO, *, content_type: str | None = None) -> None:
        uploaded["bytes"] = file_obj.read()
        uploaded["content_type"] = content_type

    blob.upload_from_file.side_effect = upload_from_file
    mocker.patch("app.services.feedback_service.get_storage_bucket", return_value=bucket)
    mocker.patch("app.services.feedback_service.uuid4", return_value="token-1")
    mocker.patch(
        "app.services.feedback_service._utc_timestamp_ms",
        side_effect=[1000, 2000],
    )

    result = asyncio.run(
        feedback_service.create_feedback(
            user_id="user-1",
            message="App is great",
            attachment=_upload(content, filename=filename, content_type=content_type),
        )
    )

    expected_storage_path = f"feedback/user-1/feedback-1/{filename}"
    assert uploaded == {"bytes": content, "content_type": expected_content_type}
    assert result["attachmentRef"] == {"storagePath": expected_storage_path}
    assert result["attachmentUrl"] == (
        "https://firebasestorage.googleapis.com/v0/b/fitaly-test.appspot.com/o/"
        f"feedback%2Fuser-1%2Ffeedback-1%2F{filename}?alt=media&token=token-1"
    )
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

    result = asyncio.run(
        feedback_service.create_feedback(
            user_id="user-1",
            message="App is great",
            attachment=_upload(
                b"\xff\xd8\xfffeedback\xff\xd9",
                filename=upload_filename,
                content_type="image/jpeg",
            ),
        )
    )

    expected_storage_path = f"feedback/user-1/feedback-1/{expected_storage_filename}"
    assert result["attachmentRef"] == {"storagePath": expected_storage_path}
    assert "attachmentPath" not in result
    _assert_feedback_storage_path_is_cleanup_safe(expected_storage_path)
    bucket.blob.assert_called_once_with(expected_storage_path)
    document_ref.set.assert_called_once_with(result, merge=True)


def test_create_feedback_rejects_invalid_attachment_mime_before_writes(
    mocker: MockerFixture,
) -> None:
    document_ref = _patch_feedback_firestore(mocker)
    get_storage_bucket = mocker.patch("app.services.feedback_service.get_storage_bucket")

    upload = _upload(
        b"plain text is not an image",
        filename="feedback.txt",
        content_type="text/plain",
    )

    with pytest.raises(
        feedback_service.FeedbackValidationError,
        match="Unsupported or unrecognized file type",
    ):
        asyncio.run(
            feedback_service.create_feedback(
                user_id="user-1",
                message="App is great",
                attachment=upload,
            )
        )

    get_storage_bucket.assert_not_called()
    document_ref.set.assert_not_called()


def test_create_feedback_rejects_invalid_declared_mime_with_image_bytes_before_writes(
    mocker: MockerFixture,
) -> None:
    document_ref = _patch_feedback_firestore(mocker)
    get_storage_bucket = mocker.patch("app.services.feedback_service.get_storage_bucket")

    upload = _upload(
        b"\xff\xd8\xfffeedback\xff\xd9",
        filename="feedback.jpg",
        content_type="text/plain",
    )

    with pytest.raises(
        feedback_service.FeedbackValidationError,
        match="Unsupported or unrecognized file type",
    ):
        asyncio.run(
            feedback_service.create_feedback(
                user_id="user-1",
                message="App is great",
                attachment=upload,
            )
        )

    get_storage_bucket.assert_not_called()
    document_ref.set.assert_not_called()


def test_create_feedback_rejects_spoofed_allowed_attachment_mime_before_writes(
    mocker: MockerFixture,
) -> None:
    document_ref = _patch_feedback_firestore(mocker)
    get_storage_bucket = mocker.patch("app.services.feedback_service.get_storage_bucket")

    upload = _upload(
        b"plain text is not an image",
        filename="feedback.jpg",
        content_type="image/jpeg",
    )

    with pytest.raises(
        feedback_service.FeedbackValidationError,
        match="Unsupported or unrecognized file type",
    ):
        asyncio.run(
            feedback_service.create_feedback(
                user_id="user-1",
                message="App is great",
                attachment=upload,
            )
        )

    get_storage_bucket.assert_not_called()
    document_ref.set.assert_not_called()


def test_create_feedback_rejects_oversized_attachment_before_writes(
    mocker: MockerFixture,
) -> None:
    document_ref = _patch_feedback_firestore(mocker)
    get_storage_bucket = mocker.patch("app.services.feedback_service.get_storage_bucket")

    upload = _upload(
        b"\xff\xd8\xff" + (b"0" * (MAX_UPLOAD_BYTES + 1)),
        filename="feedback.jpg",
        content_type="image/jpeg",
    )

    with pytest.raises(
        feedback_service.FeedbackValidationError,
        match=f"File exceeds maximum allowed size of {MAX_UPLOAD_BYTES} bytes",
    ):
        asyncio.run(
            feedback_service.create_feedback(
                user_id="user-1",
                message="App is great",
                attachment=upload,
            )
        )

    get_storage_bucket.assert_not_called()
    document_ref.set.assert_not_called()
