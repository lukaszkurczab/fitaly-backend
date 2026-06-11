from io import BytesIO
from unittest.mock import Mock

from fastapi import UploadFile
import pytest
from starlette.datastructures import Headers

from app.services.meal_storage import _validate_upload


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


def test_validate_upload_default_preserves_mock_file_declared_image_fallback() -> None:
    upload = Mock()
    upload.content_type = "image/jpeg"
    upload.file = Mock()

    assert _validate_upload(upload) == "image/jpeg"
