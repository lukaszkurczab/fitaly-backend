from __future__ import annotations

import os
from collections.abc import Iterable
from contextlib import suppress
from pathlib import Path
from typing import Any, Callable, Protocol, cast
from uuid import uuid4

import firebase_admin
import pytest
from google.cloud import firestore
from pytest import MonkeyPatch

from app.core.firestore_constants import USERS_COLLECTION
from app.db import firebase as firebase_db
from app.db.firebase import get_storage_bucket
from app.services import user_account_service


pytestmark = pytest.mark.skipif(
    not os.getenv("FIREBASE_STORAGE_EMULATOR_HOST")
    or not os.getenv("FIRESTORE_EMULATOR_HOST"),
    reason="Firebase Storage and Firestore emulators are not configured.",
)


OBJECT_BYTES = b"account-delete-storage-emulator"


class _StorageBlob(Protocol):
    def upload_from_string(
        self,
        data: bytes,
        *,
        content_type: str | None = None,
    ) -> None: ...

    def exists(self) -> bool: ...

    def download_as_bytes(self) -> bytes: ...

    def delete(self) -> None: ...


class _StorageBucket(Protocol):
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


def _user_document(client: firestore.Client, user_id: str) -> firestore.DocumentReference:
    return client.collection(USERS_COLLECTION).document(user_id)


def _upload_storage_object(bucket: _StorageBucket, object_path: str) -> None:
    bucket.blob(object_path).upload_from_string(
        OBJECT_BYTES,
        content_type="image/jpeg",
    )


def _delete_storage_object(bucket: _StorageBucket, object_path: str) -> None:
    with suppress(Exception):
        blob = bucket.blob(object_path)
        if blob.exists():
            blob.delete()


async def test_delete_account_data_deletes_current_user_storage_prefixes_in_emulator(
    monkeypatch: MonkeyPatch,
) -> None:
    _patch_emulator_firebase_settings(monkeypatch)
    _configure_storage_client_emulator(monkeypatch)
    _reset_firebase_singletons()

    client = _emulator_firestore_client()
    bucket = cast(_StorageBucket, get_storage_bucket())
    run_id = uuid4().hex
    current_user_id = f"ch-07-004-storage-current-{run_id}"
    other_user_id = f"ch-07-004-storage-other-{run_id}"
    current_paths = [
        f"avatars/{current_user_id}/avatar.{'a' * 64}",
        f"meals/{current_user_id}/meal-{run_id}.jpg",
        f"mealTemplates/{current_user_id}/saved-{run_id}.jpg",
    ]
    other_paths = [
        f"avatars/{other_user_id}/avatar.{'b' * 64}",
        f"meals/{other_user_id}/meal-{run_id}.jpg",
        f"mealTemplates/{other_user_id}/saved-{run_id}.jpg",
    ]
    all_paths = [*current_paths, *other_paths]

    monkeypatch.setattr(user_account_service, "get_firestore", lambda: client)
    monkeypatch.setattr(user_account_service, "get_storage_bucket", lambda: bucket)

    _user_document(client, current_user_id).set(
        {"uid": current_user_id, "username": f"current-{run_id}"}
    )
    _user_document(client, other_user_id).set(
        {"uid": other_user_id, "username": f"other-{run_id}"}
    )

    try:
        for object_path in all_paths:
            _delete_storage_object(bucket, object_path)
            _upload_storage_object(bucket, object_path)
            assert bucket.blob(object_path).exists() is True

        await user_account_service.delete_account_data(current_user_id)

        assert _user_document(client, current_user_id).get().exists is False
        assert _user_document(client, other_user_id).get().exists is True
        for object_path in current_paths:
            assert bucket.blob(object_path).exists() is False
        for object_path in other_paths:
            other_blob = bucket.blob(object_path)
            assert other_blob.exists() is True
            assert other_blob.download_as_bytes() == OBJECT_BYTES
    finally:
        for object_path in all_paths:
            _delete_storage_object(bucket, object_path)
        _user_document(client, current_user_id).delete()
        _user_document(client, other_user_id).delete()
        _reset_firebase_singletons()
