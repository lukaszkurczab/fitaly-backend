import asyncio
from io import BytesIO
from unittest.mock import Mock

from fastapi import UploadFile
import pytest
from pytest_mock import MockerFixture
from starlette.datastructures import Headers

from app.core.exceptions import FirestoreServiceError
from app.services.meal_storage import _validate_upload, upload_photo_to_storage


def _upload(content: bytes, *, content_type: str) -> UploadFile:
    return UploadFile(
        BytesIO(content),
        filename="upload.jpg",
        headers=Headers({"content-type": content_type}),
    )


def test_validate_upload_default_preserves_declared_image_fallback() -> None:
    assert _validate_upload(_upload(b"not sniffable", content_type="image/jpeg")) == "image/jpeg"


def test_validate_upload_strict_mode_requires_supported_image_signature() -> None:
    with pytest.raises(ValueError, match="Unsupported or unrecognized file type"):
        _validate_upload(
            _upload(b"not sniffable", content_type="image/jpeg"),
            require_detected_image=True,
        )


def test_validate_upload_strict_mode_requires_allowed_declared_image_mime() -> None:
    with pytest.raises(ValueError, match="Unsupported or unrecognized file type"):
        _validate_upload(
            _upload(b"\xff\xd8\xffvalid-jpeg-body", content_type="text/plain"),
            require_detected_image=True,
        )


def test_validate_upload_strict_mode_requires_declared_image_mime() -> None:
    with pytest.raises(ValueError, match="Unsupported or unrecognized file type"):
        _validate_upload(
            _upload(b"\xff\xd8\xffvalid-jpeg-body", content_type=""),
            require_detected_image=True,
        )


def test_validate_upload_default_preserves_mock_file_declared_image_fallback() -> None:
    upload = Mock()
    upload.content_type = "image/jpeg"
    upload.file = Mock()

    assert _validate_upload(upload) == "image/jpeg"


def test_upload_photo_failure_log_extra_redacts_storage_object_path(
    mocker: MockerFixture,
) -> None:
    object_path = "meals/user-1/private-image.jpg"
    blob = Mock()
    blob.upload_from_file.side_effect = OSError("storage unavailable")
    bucket = Mock()
    bucket.blob.return_value = blob
    mocker.patch("app.services.meal_storage.get_storage_bucket", return_value=bucket)
    log_exception = mocker.patch("app.services.meal_storage.logger.exception")

    with pytest.raises(FirestoreServiceError):
        asyncio.run(
            upload_photo_to_storage(
                "user-1",
                _upload(b"\xff\xd8\xffvalid-jpeg-body", content_type="image/jpeg"),
                object_path,
            )
        )

    log_exception.assert_called_once()
    assert object_path not in repr(log_exception.call_args)
    assert log_exception.call_args.kwargs["extra"] == {
        "user_id": "user-1",
        "object_path": "[REDACTED_STORAGE_PATH]",
    }
