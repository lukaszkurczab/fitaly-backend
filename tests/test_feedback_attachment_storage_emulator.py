from __future__ import annotations

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

from app.core.firestore_constants import FEEDBACK_SUBCOLLECTION, USERS_COLLECTION
from app.db.firebase import get_storage_bucket
from app.services import feedback_service, user_account_service


pytestmark = pytest.mark.skipif(
    not os.getenv("FIREBASE_STORAGE_EMULATOR_HOST")
    or not os.getenv("FIRESTORE_EMULATOR_HOST"),
    reason="Firebase Storage and Firestore emulators are not configured.",
)


ATTACHMENT_BYTES = (
    b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00"
    b"feedback-attachment"
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


def _feedback_collection(
    client: firestore.Client,
    user_id: str,
) -> firestore.CollectionReference:
    return client.collection(USERS_COLLECTION).document(user_id).collection(FEEDBACK_SUBCOLLECTION)


def _feedback_upload(filename: str) -> UploadFile:
    return UploadFile(
        BytesIO(ATTACHMENT_BYTES),
        filename=filename,
        headers=Headers({"content-type": "image/jpeg"}),
    )


def _object_path_from_download_url(attachment_url: str, bucket_name: str) -> tuple[str, str]:
    parsed = urlparse(attachment_url)
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
    attachment_url: str,
    *,
    bucket_name: str,
    object_path: str,
    token: str,
) -> None:
    assert (
        attachment_url
        == "https://firebasestorage.googleapis.com/v0/b/"
        f"{bucket_name}/o/{quote(object_path, safe='')}?alt=media&token={token}"
    )


def _attachment_storage_path(payload: dict[str, Any]) -> str:
    attachment_ref = payload.get("attachmentRef")
    assert isinstance(attachment_ref, dict)
    attachment_ref_payload = cast(dict[str, Any], attachment_ref)
    storage_path = attachment_ref_payload.get("storagePath")
    assert isinstance(storage_path, str)
    assert storage_path
    return storage_path


def _assert_feedback_storage_path_is_cleanup_safe(
    storage_path: str,
    *,
    user_id: str,
) -> None:
    parts = storage_path.split("/")
    assert len(parts) == 4
    assert parts[0] == "feedback"
    assert parts[1] == user_id
    assert all(part.strip() and part not in {".", ".."} and "\\" not in part for part in parts)


def _document_payload(document_ref: firestore.DocumentReference) -> dict[str, Any]:
    snapshot = document_ref.get()
    assert snapshot.exists is True
    return dict(snapshot.to_dict() or {})


def _delete_user_feedback_tree(client: firestore.Client, user_id: str) -> None:
    for snapshot in _feedback_collection(client, user_id).stream():
        snapshot.reference.delete()
    client.collection(USERS_COLLECTION).document(user_id).delete()


def _delete_storage_object(bucket: _StorageBucket, object_path: str) -> None:
    with suppress(Exception):
        blob = bucket.blob(object_path)
        if blob.exists():
            blob.delete()


async def test_feedback_attachment_storage_ref_and_account_delete_use_storage_emulator(
    monkeypatch: MonkeyPatch,
) -> None:
    _patch_emulator_firebase_settings(monkeypatch)
    _configure_storage_client_emulator(monkeypatch)
    _reset_firebase_singletons()

    client = _emulator_firestore_client()
    bucket = cast(_StorageBucket, get_storage_bucket())
    user_id = "ch-05-003b-fix2-feedback-user"
    other_user_id = "ch-05-003b-fix2-feedback-other-user"
    uploaded_paths: list[str] = []

    tokens = iter(
        [
            "00000000-0000-4000-8000-000000000001",
            "00000000-0000-4000-8000-000000000002",
            "00000000-0000-4000-8000-000000000003",
        ]
    )
    timestamps = iter(
        [
            1_900_000_001_000,
            1_900_000_002_000,
            1_900_000_003_000,
            1_900_000_004_000,
            1_900_000_005_000,
            1_900_000_006_000,
        ]
    )

    def next_token() -> str:
        return next(tokens)

    def next_timestamp_ms() -> int:
        return next(timestamps)

    monkeypatch.setattr(feedback_service, "get_firestore", lambda: client)
    monkeypatch.setattr(feedback_service, "get_storage_bucket", lambda: bucket)
    monkeypatch.setattr(feedback_service, "uuid4", next_token)
    monkeypatch.setattr(feedback_service, "_utc_timestamp_ms", next_timestamp_ms)
    monkeypatch.setattr(user_account_service, "get_firestore", lambda: client)
    monkeypatch.setattr(user_account_service, "get_storage_bucket", lambda: bucket)

    _delete_user_feedback_tree(client, user_id)
    _delete_user_feedback_tree(client, other_user_id)

    try:
        result = await feedback_service.create_feedback(
            user_id=user_id,
            message="Storage emulator feedback",
            email="user@example.com",
            device_info={"modelName": "iPhone", "osName": "iOS", "osVersion": "18"},
            attachment=_feedback_upload("feedback.jpg"),
        )
        dangerous_result = await feedback_service.create_feedback(
            user_id=user_id,
            message="Storage emulator dangerous filename feedback",
            attachment=_feedback_upload("../.."),
        )
        other_result = await feedback_service.create_feedback(
            user_id=other_user_id,
            message="Other user feedback",
            attachment=_feedback_upload("other-feedback.jpg"),
        )

        storage_path = _attachment_storage_path(result)
        dangerous_storage_path = _attachment_storage_path(dangerous_result)
        other_storage_path = _attachment_storage_path(other_result)
        uploaded_paths.extend([storage_path, dangerous_storage_path, other_storage_path])

        feedback_id = result["id"]
        assert isinstance(feedback_id, str)
        assert storage_path == f"feedback/{user_id}/{feedback_id}/feedback.jpg"
        _assert_feedback_storage_path_is_cleanup_safe(storage_path, user_id=user_id)
        assert result["attachmentRef"] == {"storagePath": storage_path}
        assert "attachmentPath" not in result

        document_ref = _feedback_collection(client, user_id).document(feedback_id)
        stored_payload = _document_payload(document_ref)
        assert stored_payload["attachmentRef"] == {"storagePath": storage_path}
        assert stored_payload["attachmentUrl"] == result["attachmentUrl"]
        assert "attachmentPath" not in stored_payload

        attachment_url = result["attachmentUrl"]
        assert isinstance(attachment_url, str)
        url_storage_path, token = _object_path_from_download_url(attachment_url, bucket.name)
        assert url_storage_path == storage_path
        assert token == "00000000-0000-4000-8000-000000000001"
        _assert_download_url(
            attachment_url,
            bucket_name=bucket.name,
            object_path=storage_path,
            token=token,
        )

        stored_blob = bucket.blob(storage_path)
        assert stored_blob.exists()
        assert stored_blob.download_as_bytes() == ATTACHMENT_BYTES
        stored_blob.reload()
        metadata = stored_blob.metadata or {}
        assert metadata["firebaseStorageDownloadTokens"] == token

        legacy_path = storage_path.replace("feedback/", "feedbacks/", 1)
        assert bucket.blob(legacy_path).exists() is False
        assert list(bucket.list_blobs(prefix=f"feedbacks/{user_id}/{feedback_id}/")) == []

        dangerous_feedback_id = dangerous_result["id"]
        assert isinstance(dangerous_feedback_id, str)
        assert (
            dangerous_storage_path
            == f"feedback/{user_id}/{dangerous_feedback_id}/attachment.jpg"
        )
        _assert_feedback_storage_path_is_cleanup_safe(
            dangerous_storage_path,
            user_id=user_id,
        )
        assert dangerous_result["attachmentRef"] == {"storagePath": dangerous_storage_path}
        assert "attachmentPath" not in dangerous_result

        dangerous_document_ref = _feedback_collection(client, user_id).document(
            dangerous_feedback_id
        )
        dangerous_stored_payload = _document_payload(dangerous_document_ref)
        assert dangerous_stored_payload["attachmentRef"] == {
            "storagePath": dangerous_storage_path
        }
        assert "attachmentPath" not in dangerous_stored_payload

        dangerous_blob = bucket.blob(dangerous_storage_path)
        assert dangerous_blob.exists()
        assert dangerous_blob.download_as_bytes() == ATTACHMENT_BYTES

        _feedback_collection(client, user_id).document("foreign-feedback-ref").set(
            {"attachmentRef": {"storagePath": other_storage_path}},
            merge=True,
        )

        await user_account_service.delete_account_data(user_id)

        assert stored_blob.exists() is False
        assert document_ref.get().exists is False
        assert dangerous_blob.exists() is False
        assert dangerous_document_ref.get().exists is False

        other_blob = bucket.blob(other_storage_path)
        assert other_blob.exists()
        assert other_blob.download_as_bytes() == ATTACHMENT_BYTES
        other_payload = _document_payload(
            _feedback_collection(client, other_user_id).document(str(other_result["id"]))
        )
        assert other_payload["attachmentRef"] == {"storagePath": other_storage_path}
    finally:
        for object_path in uploaded_paths:
            _delete_storage_object(bucket, object_path)
        _delete_user_feedback_tree(client, user_id)
        _delete_user_feedback_tree(client, other_user_id)
        _reset_firebase_singletons()
