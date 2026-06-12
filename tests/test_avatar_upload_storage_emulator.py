from __future__ import annotations

import hashlib
import os
from collections.abc import Iterable
from contextlib import suppress
from io import BytesIO
from pathlib import Path
from typing import Any, BinaryIO, Callable, Protocol, cast
from urllib.parse import parse_qs, quote, unquote, urlparse

import firebase_admin
import pytest
from fastapi import UploadFile
from google.cloud import firestore
from pytest import MonkeyPatch
from starlette.datastructures import Headers

from app.core.firestore_constants import USERS_COLLECTION
from app.db.firebase import get_storage_bucket
from app.services import user_account_service


pytestmark = pytest.mark.skipif(
    not os.getenv("FIREBASE_STORAGE_EMULATOR_HOST")
    or not os.getenv("FIRESTORE_EMULATOR_HOST"),
    reason="Firebase Storage and Firestore emulators are not configured.",
)


AVATAR_BYTES = (
    b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00"
    b"avatar-storage-emulator"
    b"\xff\xd9"
)


class _StorageBlob(Protocol):
    metadata: dict[str, str] | None

    def upload_from_file(self, file_obj: BinaryIO, *, content_type: str | None = None) -> None: ...

    def exists(self) -> bool: ...

    def download_as_bytes(self) -> bytes: ...

    def reload(self) -> None: ...

    def delete(self) -> None: ...


class _StorageBucket(Protocol):
    name: str

    def blob(self, object_path: str) -> _StorageBlob: ...

    def list_blobs(self, *, prefix: str) -> Iterable[_StorageBlob]: ...


def _configure_storage_client_emulator(monkeypatch: MonkeyPatch) -> None:
    emulator_host = os.getenv("FIREBASE_STORAGE_EMULATOR_HOST", "").strip()
    if not emulator_host or os.getenv("STORAGE_EMULATOR_HOST"):
        return

    if emulator_host.startswith(("http://", "https://")):
        monkeypatch.setenv("STORAGE_EMULATOR_HOST", emulator_host)
    else:
        monkeypatch.setenv("STORAGE_EMULATOR_HOST", f"http://{emulator_host}")


def _patch_emulator_firebase_settings(monkeypatch: MonkeyPatch) -> None:
    from app.core.config import settings

    project_id = os.getenv("FIREBASE_PROJECT_ID") or "demo-fitaly-local"
    database_id = os.getenv("FIRESTORE_DATABASE_ID") or "(default)"
    storage_bucket = os.getenv("FIREBASE_STORAGE_BUCKET") or f"{project_id}.appspot.com"
    credentials_path = os.getenv("GOOGLE_APPLICATION_CREDENTIALS", "").strip()
    if not credentials_path:
        local_credentials = Path("service-account.json")
        if local_credentials.exists():
            credentials_path = str(local_credentials)

    monkeypatch.setenv("FIREBASE_PROJECT_ID", project_id)
    monkeypatch.setenv("FIRESTORE_DATABASE_ID", database_id)
    monkeypatch.setenv("FIREBASE_STORAGE_BUCKET", storage_bucket)
    if credentials_path:
        monkeypatch.setenv("GOOGLE_APPLICATION_CREDENTIALS", credentials_path)

    monkeypatch.setattr(settings, "FIREBASE_PROJECT_ID", project_id)
    monkeypatch.setattr(settings, "FIRESTORE_DATABASE_ID", database_id)
    monkeypatch.setattr(settings, "FIREBASE_STORAGE_BUCKET", storage_bucket)
    monkeypatch.setattr(settings, "GOOGLE_APPLICATION_CREDENTIALS", credentials_path)


def _reset_firebase_singletons() -> None:
    from app.db import firebase as firebase_db

    firebase_db.get_firestore.cache_clear()
    firebase_db.get_storage_bucket.cache_clear()
    delete_app = cast(Callable[[firebase_admin.App], None], getattr(firebase_admin, "delete_app"))
    for firebase_app in list(firebase_admin._apps.values()):
        delete_app(firebase_app)


def _emulator_firestore_client() -> firestore.Client:
    project_id = os.getenv("FIREBASE_PROJECT_ID") or "demo-fitaly-local"
    database_id = os.getenv("FIRESTORE_DATABASE_ID") or "(default)"
    client_class = cast(Any, firestore.Client)
    return cast(firestore.Client, client_class(project=project_id, database=database_id))


def _avatar_upload(
    filename: str,
    content: bytes = AVATAR_BYTES,
    *,
    content_type: str = "image/jpeg",
) -> UploadFile:
    return UploadFile(
        BytesIO(content),
        filename=filename,
        headers=Headers({"content-type": content_type}),
    )


def _object_path_from_download_url(avatar_url: str, bucket_name: str) -> tuple[str, str]:
    parsed = urlparse(avatar_url)
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
    avatar_url: str,
    *,
    bucket_name: str,
    object_path: str,
    token: str,
) -> None:
    assert (
        avatar_url
        == "https://firebasestorage.googleapis.com/v0/b/"
        f"{bucket_name}/o/{quote(object_path, safe='')}?alt=media&token={token}"
    )


def _user_document(client: firestore.Client, user_id: str) -> firestore.DocumentReference:
    return client.collection(USERS_COLLECTION).document(user_id)


def _document_payload(document_ref: firestore.DocumentReference) -> dict[str, Any]:
    snapshot = document_ref.get()
    assert snapshot.exists is True
    return dict(snapshot.to_dict() or {})


def _delete_user_document(client: firestore.Client, user_id: str) -> None:
    _user_document(client, user_id).delete()


def _delete_storage_object(bucket: _StorageBucket, object_path: str) -> None:
    with suppress(Exception):
        blob = bucket.blob(object_path)
        if blob.exists():
            blob.delete()


async def test_avatar_upload_storage_ref_and_account_delete_use_storage_emulator(
    monkeypatch: MonkeyPatch,
) -> None:
    _patch_emulator_firebase_settings(monkeypatch)
    _configure_storage_client_emulator(monkeypatch)
    _reset_firebase_singletons()

    client = _emulator_firestore_client()
    bucket = cast(_StorageBucket, get_storage_bucket())
    user_id = "ch-05-004c-avatar-user"
    other_user_id = "ch-05-004c-avatar-other-user"
    uploaded_paths: list[str] = []

    tokens = iter(
        [
            "00000000-0000-4000-8000-000000000101",
            "00000000-0000-4000-8000-000000000102",
        ]
    )

    def next_token() -> str:
        return next(tokens)

    monkeypatch.setattr(user_account_service, "get_firestore", lambda: client)
    monkeypatch.setattr(user_account_service, "get_storage_bucket", lambda: bucket)
    monkeypatch.setattr(user_account_service, "uuid4", next_token)

    _delete_user_document(client, user_id)
    _delete_user_document(client, other_user_id)

    client_mutation_id = "avatar-upload-storage-emulator-mutation"
    other_client_mutation_id = "other-avatar-upload-storage-emulator-mutation"
    expected_hash = hashlib.sha256(client_mutation_id.encode("utf-8")).hexdigest()
    expected_path = f"avatars/{user_id}/avatar.{expected_hash}"
    expected_other_hash = hashlib.sha256(other_client_mutation_id.encode("utf-8")).hexdigest()
    expected_other_path = f"avatars/{other_user_id}/avatar.{expected_other_hash}"
    legacy_path = f"avatars/{user_id}/avatar.jpg"

    try:
        avatar_url, synced_at, avatar_ref = await user_account_service.upload_avatar(
            user_id,
            _avatar_upload("avatar.jpg"),
            client_mutation_id=client_mutation_id,
        )
        other_avatar_url, _other_synced_at, other_avatar_ref = (
            await user_account_service.upload_avatar(
                other_user_id,
                _avatar_upload("other-avatar.jpg"),
                client_mutation_id=other_client_mutation_id,
            )
        )
        uploaded_paths.extend([expected_path, expected_other_path])

        assert avatar_ref == {"storagePath": expected_path}
        assert other_avatar_ref == {"storagePath": expected_other_path}
        assert synced_at.endswith("Z")

        url_storage_path, token = _object_path_from_download_url(avatar_url, bucket.name)
        assert url_storage_path == expected_path
        assert token == "00000000-0000-4000-8000-000000000101"
        _assert_download_url(
            avatar_url,
            bucket_name=bucket.name,
            object_path=expected_path,
            token=token,
        )

        other_url_storage_path, other_token = _object_path_from_download_url(
            other_avatar_url,
            bucket.name,
        )
        assert other_url_storage_path == expected_other_path
        assert other_token == "00000000-0000-4000-8000-000000000102"

        stored_blob = bucket.blob(expected_path)
        assert stored_blob.exists()
        assert stored_blob.download_as_bytes() == AVATAR_BYTES
        stored_blob.reload()
        metadata = stored_blob.metadata or {}
        assert metadata["firebaseStorageDownloadTokens"] == token

        other_blob = bucket.blob(expected_other_path)
        assert other_blob.exists()
        assert other_blob.download_as_bytes() == AVATAR_BYTES

        assert bucket.blob(legacy_path).exists() is False
        assert list(bucket.list_blobs(prefix=f"avatars/{user_id}/avatar.jpg")) == []

        stored_payload = _document_payload(_user_document(client, user_id))
        assert stored_payload["avatarRef"] == {"storagePath": expected_path}
        assert stored_payload["avatarUrl"] == avatar_url
        assert stored_payload["avatarlastSyncedAt"] == synced_at
        for field_name in (
            "avatarLocalPath",
            "clientMutationId",
            "avatarClientMutationId",
            "avatarUploadState",
            "avatarSyncState",
            "avatarRemotePath",
            "storagePath",
        ):
            assert field_name not in stored_payload

        await user_account_service.delete_account_data(user_id)

        assert stored_blob.exists() is False
        assert _user_document(client, user_id).get().exists is False
        assert other_blob.exists()
        assert other_blob.download_as_bytes() == AVATAR_BYTES
        other_payload = _document_payload(_user_document(client, other_user_id))
        assert other_payload["avatarRef"] == {"storagePath": expected_other_path}
    finally:
        for object_path in uploaded_paths:
            _delete_storage_object(bucket, object_path)
        _delete_user_document(client, user_id)
        _delete_user_document(client, other_user_id)
        _reset_firebase_singletons()


async def test_avatar_upload_rejections_do_not_create_storage_or_profile_in_emulator(
    monkeypatch: MonkeyPatch,
) -> None:
    _patch_emulator_firebase_settings(monkeypatch)
    _configure_storage_client_emulator(monkeypatch)
    _reset_firebase_singletons()

    client = _emulator_firestore_client()
    bucket = cast(_StorageBucket, get_storage_bucket())
    user_id = "ch-05-004d-avatar-reject-user"
    oversized_payload = b"\xff\xd8\xff" + (b"x" * (10 * 1024 * 1024 - 2))
    cases = [
        (
            "invalid_declared_mime",
            "ch-05-004e-avatar-invalid-declared-mime-mutation",
            _avatar_upload("invalid-declared-mime.jpg", AVATAR_BYTES, content_type="text/plain"),
            "Unsupported or unrecognized file type",
        ),
        (
            "spoofed",
            "ch-05-004d-avatar-spoofed-mutation",
            _avatar_upload("spoofed.jpg", b"not an actual image"),
            "Unsupported or unrecognized file type",
        ),
        (
            "oversized",
            "ch-05-004d-avatar-oversized-mutation",
            _avatar_upload("oversized.jpg", oversized_payload),
            "File exceeds maximum allowed size",
        ),
    ]

    monkeypatch.setattr(user_account_service, "get_firestore", lambda: client)
    monkeypatch.setattr(user_account_service, "get_storage_bucket", lambda: bucket)

    _delete_user_document(client, user_id)
    expected_paths = [
        (
            f"avatars/{user_id}/avatar."
            f"{hashlib.sha256(client_mutation_id.encode('utf-8')).hexdigest()}"
        )
        for _case_name, client_mutation_id, _upload, _message in cases
    ]

    try:
        for (_case_name, client_mutation_id, upload, message), expected_path in zip(
            cases,
            expected_paths,
            strict=True,
        ):
            _delete_storage_object(bucket, expected_path)
            with pytest.raises(ValueError, match=message):
                await user_account_service.upload_avatar(
                    user_id,
                    upload,
                    client_mutation_id=client_mutation_id,
                )

            assert bucket.blob(expected_path).exists() is False
            assert list(bucket.list_blobs(prefix=expected_path)) == []
            assert _user_document(client, user_id).get().exists is False
    finally:
        for object_path in expected_paths:
            _delete_storage_object(bucket, object_path)
        _delete_user_document(client, user_id)
        _reset_firebase_singletons()
