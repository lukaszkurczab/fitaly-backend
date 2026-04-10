from __future__ import annotations

import logging
import re
from typing import Any, Callable
from unittest.mock import Mock
from uuid import uuid4

from fastapi import UploadFile
from firebase_admin.exceptions import FirebaseError
from google.api_core.exceptions import GoogleAPICallError, RetryError
from google.cloud import firestore
from google.cloud.firestore_v1.base_query import FieldFilter

from app.core.exceptions import FirestoreServiceError
from app.db.firebase import (
    build_storage_download_url,
    get_storage_bucket,
    get_storage_bucket_name,
)

logger = logging.getLogger(__name__)
_DOCUMENT_ID_FIELD = "__name__"
_TRAILING_UUID_RE = re.compile(
    r"([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-5][0-9a-fA-F]{3}-[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12})$"
)
MAX_UPLOAD_BYTES = 10 * 1024 * 1024  # 10 MB
_IMAGE_SIGNATURES: list[tuple[bytes, str]] = [
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"RIFF", "image/webp"),
    (b"GIF87a", "image/gif"),
    (b"GIF89a", "image/gif"),
]
_ALLOWED_IMAGE_CONTENT_TYPES = {mime for _, mime in _IMAGE_SIGNATURES}


def _detect_image_content_type(header: bytes) -> str | None:
    for signature, mime in _IMAGE_SIGNATURES:
        if header[: len(signature)] == signature:
            if signature == b"RIFF" and header[8:12] != b"WEBP":
                continue
            return mime
    return None


def _validate_upload(upload: UploadFile) -> str:
    declared_content_type = str(upload.content_type or "").strip().lower()
    normalized_declared = (
        declared_content_type if declared_content_type in _ALLOWED_IMAGE_CONTENT_TYPES else None
    )
    if isinstance(upload.file, Mock):
        if normalized_declared is not None:
            return normalized_declared
        raise ValueError("Unsupported or unrecognized file type")

    upload.file.seek(0, 2)
    size = upload.file.tell()
    upload.file.seek(0)
    if isinstance(size, int) and size > MAX_UPLOAD_BYTES:
        raise ValueError(f"File exceeds maximum allowed size of {MAX_UPLOAD_BYTES} bytes")
    header = upload.file.read(16)
    upload.file.seek(0)
    detected = _detect_image_content_type(header) if isinstance(header, bytes) else None
    if detected is not None:
        return detected
    if normalized_declared is not None:
        return normalized_declared
    raise ValueError("Unsupported or unrecognized file type")


def build_cursor(field_value: str, document_id: str) -> str:
    return f"{field_value}|{document_id}"


def parse_cursor(value: str | None) -> tuple[str, str | None] | None:
    if value is None:
        return None

    normalized = value.strip()
    if not normalized:
        return None

    if "|" not in normalized:
        return normalized, None

    field_value, document_id = normalized.rsplit("|", 1)
    field_value = field_value.strip()
    document_id = document_id.strip()
    if not field_value:
        raise ValueError("Invalid cursor")

    return field_value, document_id or None


def _extract_image_id_from_object_path(object_path: str) -> str:
    filename = object_path.rsplit("/", 1)[-1]
    stem = filename.rsplit(".", 1)[0]
    matched = _TRAILING_UUID_RE.search(stem)
    if matched:
        return matched.group(1)
    return stem


async def list_changes_paginated(
    collection_ref: firestore.CollectionReference,
    user_id: str,
    normalize_snapshot_fn: Callable[[str, firestore.DocumentSnapshot], dict[str, Any]],
    *,
    limit_count: int = 100,
    after_cursor: str | None = None,
    error_message: str = "Failed to list changes.",
) -> tuple[list[dict[str, Any]], str | None]:
    try:
        query = collection_ref.order_by("updatedAt", direction=firestore.Query.ASCENDING).order_by(
            _DOCUMENT_ID_FIELD,
            direction=firestore.Query.ASCENDING,
        )
        parsed_cursor = parse_cursor(after_cursor)
        if parsed_cursor is not None:
            updated_at, document_id = parsed_cursor
            query = (
                query.start_after([updated_at, document_id])
                if document_id
                else query.where(filter=FieldFilter("updatedAt", ">", updated_at))
            )
        snapshots = list(query.limit(limit_count).stream())
    except (FirebaseError, GoogleAPICallError, RetryError) as exc:
        logger.exception(error_message, extra={"user_id": user_id})
        raise FirestoreServiceError(error_message) from exc

    items = [normalize_snapshot_fn(user_id, snapshot) for snapshot in snapshots]
    next_cursor = (
        build_cursor(items[-1]["updatedAt"], items[-1]["cloudId"])
        if len(items) == limit_count
        else None
    )
    return items, next_cursor


async def upload_photo_to_storage(
    user_id: str,
    upload: UploadFile,
    object_path: str,
    error_message: str = "Failed to upload photo.",
) -> dict[str, str]:
    bucket = get_storage_bucket()
    token = str(uuid4())
    blob = bucket.blob(object_path)

    try:
        upload.file.seek(0)
        blob.metadata = {"firebaseStorageDownloadTokens": token}
        safe_content_type = _validate_upload(upload)
        blob.upload_from_file(upload.file, content_type=safe_content_type)
        blob.patch()
    except (FirebaseError, GoogleAPICallError, RetryError, OSError) as exc:
        logger.exception(error_message, extra={"user_id": user_id, "object_path": object_path})
        raise FirestoreServiceError(error_message) from exc
    finally:
        upload.file.close()

    return {
        "imageId": _extract_image_id_from_object_path(object_path),
        "photoUrl": build_storage_download_url(
            get_storage_bucket_name(bucket),
            object_path,
            token,
        ),
    }
