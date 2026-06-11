from __future__ import annotations

import os
from contextlib import suppress
from io import BytesIO
from typing import BinaryIO, Protocol, cast
from urllib.parse import parse_qs, quote, unquote, urlparse
from uuid import uuid4

import pytest
from fastapi import UploadFile
from starlette.datastructures import Headers

from app.db.firebase import get_storage_bucket
from app.services import meal_service, meal_storage, my_meal_service


pytestmark = pytest.mark.skipif(
    not os.getenv("FIREBASE_STORAGE_EMULATOR_HOST"),
    reason="Firebase Storage emulator is not configured.",
)


JPEG_BYTES = (
    b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00"
    b"\xff\xd9"
)


class _StorageBlob(Protocol):
    metadata: dict[str, str] | None

    def upload_from_file(self, file_obj: BinaryIO, *, content_type: str | None = None) -> None: ...

    def patch(self) -> None: ...

    def exists(self) -> bool: ...

    def download_as_bytes(self) -> bytes: ...

    def reload(self) -> None: ...

    def delete(self) -> None: ...


class _StorageBucket(Protocol):
    name: str

    def blob(self, object_path: str) -> _StorageBlob: ...


class _PatchTrackingBlob:
    def __init__(self, blob: _StorageBlob) -> None:
        self._blob = blob
        self.patch_calls = 0

    @property
    def metadata(self) -> dict[str, str] | None:
        return cast(dict[str, str] | None, self._blob.metadata)

    @metadata.setter
    def metadata(self, value: dict[str, str] | None) -> None:
        self._blob.metadata = value

    def upload_from_file(self, file_obj: BinaryIO, *, content_type: str | None = None) -> None:
        self._blob.upload_from_file(file_obj, content_type=content_type)

    def patch(self) -> None:
        self.patch_calls += 1
        self._blob.patch()


class _PatchTrackingBucket:
    def __init__(self, bucket: _StorageBucket) -> None:
        self._bucket = bucket
        self.name = bucket.name
        self.blobs_by_path: dict[str, _PatchTrackingBlob] = {}

    def blob(self, object_path: str) -> _PatchTrackingBlob:
        blob = _PatchTrackingBlob(self._bucket.blob(object_path))
        self.blobs_by_path[object_path] = blob
        return blob


def _jpeg_upload(filename: str) -> UploadFile:
    return UploadFile(
        BytesIO(JPEG_BYTES),
        filename=filename,
        headers=Headers({"content-type": "image/jpeg"}),
    )


def _configure_storage_client_emulator(monkeypatch: pytest.MonkeyPatch) -> None:
    emulator_host = os.getenv("FIREBASE_STORAGE_EMULATOR_HOST", "").strip()
    if not emulator_host or os.getenv("STORAGE_EMULATOR_HOST"):
        return

    if emulator_host.startswith(("http://", "https://")):
        monkeypatch.setenv("STORAGE_EMULATOR_HOST", emulator_host)
    else:
        monkeypatch.setenv("STORAGE_EMULATOR_HOST", f"http://{emulator_host}")


def _object_path_from_download_url(photo_url: str, bucket_name: str) -> tuple[str, str]:
    parsed = urlparse(photo_url)
    assert parsed.scheme == "https"
    assert parsed.netloc == "firebasestorage.googleapis.com"
    path_prefix = f"/v0/b/{bucket_name}/o/"
    assert parsed.path.startswith(path_prefix)

    query = parse_qs(parsed.query)
    assert query["alt"] == ["media"]
    token = query["token"][0]
    assert token
    return unquote(parsed.path.removeprefix(path_prefix)), token


def _assert_download_url(
    photo_url: str,
    *,
    bucket_name: str,
    object_path: str,
    token: str,
) -> None:
    assert (
        photo_url
        == "https://firebasestorage.googleapis.com/v0/b/"
        f"{bucket_name}/o/{quote(object_path, safe='')}?alt=media&token={token}"
    )


async def test_pr3_photo_uploads_write_to_storage_emulator(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_storage_client_emulator(monkeypatch)
    bucket = cast(_StorageBucket, get_storage_bucket())
    tracking_bucket = _PatchTrackingBucket(bucket)
    monkeypatch.setattr(meal_storage, "get_storage_bucket", lambda: tracking_bucket)

    run_id = uuid4().hex
    user_id = f"l3-pr3-storage-user-{run_id}"
    saved_meal_id = f"saved-{run_id}"
    uploaded_paths: list[str] = []

    try:
        meal_payload = await meal_service.upload_photo(
            user_id,
            _jpeg_upload("meal.jpg"),
        )
        my_meal_payload = await my_meal_service.upload_photo(
            user_id,
            saved_meal_id,
            _jpeg_upload("saved.jpg"),
        )

        meal_object_path, meal_token = _object_path_from_download_url(
            meal_payload["photoUrl"],
            bucket.name,
        )
        my_meal_object_path, my_meal_token = _object_path_from_download_url(
            my_meal_payload["photoUrl"],
            bucket.name,
        )
        uploaded_paths.extend([meal_object_path, my_meal_object_path])

        assert meal_object_path.startswith(f"meals/{user_id}/")
        assert meal_object_path.endswith(".jpg")
        assert meal_payload["storagePath"] == meal_object_path
        assert meal_payload["imageId"] == meal_object_path.rsplit("/", 1)[-1].removesuffix(".jpg")

        assert my_meal_object_path.startswith(f"myMeals/{user_id}/{saved_meal_id}-")
        assert my_meal_object_path.endswith(".jpg")
        assert my_meal_payload["mealId"] == saved_meal_id
        assert my_meal_payload["storagePath"] == my_meal_object_path
        assert my_meal_payload["imageId"] == my_meal_object_path.removeprefix(
            f"myMeals/{user_id}/{saved_meal_id}-"
        ).removesuffix(".jpg")

        _assert_download_url(
            meal_payload["photoUrl"],
            bucket_name=bucket.name,
            object_path=meal_object_path,
            token=meal_token,
        )
        _assert_download_url(
            my_meal_payload["photoUrl"],
            bucket_name=bucket.name,
            object_path=my_meal_object_path,
            token=my_meal_token,
        )

        for object_path, token in (
            (meal_object_path, meal_token),
            (my_meal_object_path, my_meal_token),
        ):
            assert tracking_bucket.blobs_by_path[object_path].patch_calls == 0

            stored_blob = bucket.blob(object_path)
            assert stored_blob.exists()
            assert stored_blob.download_as_bytes() == JPEG_BYTES
            stored_blob.reload()
            metadata = stored_blob.metadata or {}
            assert metadata["firebaseStorageDownloadTokens"] == token
    finally:
        for object_path in uploaded_paths:
            with suppress(Exception):
                bucket.blob(object_path).delete()
