"""Firebase rules emulator evidence for meal-template namespace isolation."""

from __future__ import annotations

import base64
import json
import os
from typing import Any, Callable, cast
from urllib import error, parse, request
from uuid import uuid4

import firebase_admin
import firebase_admin.auth as firebase_auth
import pytest


pytestmark = pytest.mark.skipif(
    not os.getenv("FIREBASE_AUTH_EMULATOR_HOST")
    or not os.getenv("FIREBASE_STORAGE_EMULATOR_HOST")
    or not os.getenv("FIRESTORE_EMULATOR_HOST"),
    reason="Firebase Auth, Storage, and Firestore emulators are not configured.",
)


TEMPLATE_BYTES = b"meal-template-rules-emulator-image"
PASSWORD = "emulator-password-123"


def _project_id() -> str:
    return os.getenv("FIREBASE_PROJECT_ID") or "demo-fitaly-local"


def _database_id() -> str:
    return os.getenv("FIRESTORE_DATABASE_ID") or "(default)"


def _bucket_name() -> str:
    return os.getenv("FIREBASE_STORAGE_BUCKET") or f"{_project_id()}.appspot.com"


def _emulator_origin(env_name: str) -> str:
    host = os.environ[env_name].strip()
    if host.startswith(("http://", "https://")):
        return host.rstrip("/")
    return f"http://{host}"


def _auth_emulator_url(path: str) -> str:
    return (
        f"{_emulator_origin('FIREBASE_AUTH_EMULATOR_HOST')}"
        f"/identitytoolkit.googleapis.com/v1/{path}?key=fake-api-key"
    )


def _post_auth_emulator(path: str, payload: dict[str, object]) -> dict[str, Any]:
    body = json.dumps(payload).encode("utf-8")
    req = request.Request(
        _auth_emulator_url(path),
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with request.urlopen(req, timeout=10) as response:
        return cast(dict[str, Any], json.loads(response.read().decode("utf-8")))


def _sign_up_auth_emulator_user(email: str) -> tuple[str, str]:
    payload = _post_auth_emulator(
        "accounts:signUp",
        {"email": email, "password": PASSWORD, "returnSecureToken": True},
    )
    return str(payload["localId"]), str(payload["idToken"])


def _sign_in_auth_emulator_user(email: str) -> str:
    payload = _post_auth_emulator(
        "accounts:signInWithPassword",
        {"email": email, "password": PASSWORD, "returnSecureToken": True},
    )
    return str(payload["idToken"])


def _delete_auth_emulator_user(id_token: str) -> None:
    try:
        _post_auth_emulator("accounts:delete", {"idToken": id_token})
    except Exception:
        return


def _reset_firebase_admin_apps() -> None:
    delete_app = cast(
        Callable[[firebase_admin.App], None],
        getattr(firebase_admin, "delete_app"),
    )
    for firebase_app in list(firebase_admin._apps.values()):
        delete_app(firebase_app)


def _configure_admin_claims(uid: str) -> None:
    _reset_firebase_admin_apps()
    firebase_admin.initialize_app(options={"projectId": _project_id()})
    set_custom_user_claims = cast(
        Callable[[str, dict[str, bool]], None],
        getattr(firebase_auth, "set_custom_user_claims"),
    )
    set_custom_user_claims(uid, {"admin": True})


def _decode_token_payload(id_token: str) -> dict[str, Any]:
    payload = id_token.split(".")[1]
    payload += "=" * (-len(payload) % 4)
    decoded = base64.urlsafe_b64decode(payload.encode("ascii"))
    return cast(dict[str, Any], json.loads(decoded.decode("utf-8")))


def _storage_object_path(object_path: str) -> str:
    return f"/v0/b/{_bucket_name()}/o/{parse.quote(object_path, safe='')}"


def _upload_storage_object(object_path: str, *, id_token: str, body: bytes) -> int:
    path = (
        f"/v0/b/{_bucket_name()}/o"
        f"?uploadType=media&name={parse.quote(object_path, safe='')}"
    )
    return _storage_request(
        "POST",
        path,
        id_token=id_token,
        body=body,
        content_type="image/jpeg",
    )[0]


def _read_storage_object(object_path: str, *, id_token: str) -> tuple[int, bytes]:
    return _storage_request(
        "GET",
        f"{_storage_object_path(object_path)}?alt=media",
        id_token=id_token,
    )


def _delete_storage_object(object_path: str, *, id_token: str) -> int:
    return _storage_request(
        "DELETE",
        _storage_object_path(object_path),
        id_token=id_token,
    )[0]


def _storage_request(
    method: str,
    path: str,
    *,
    id_token: str,
    body: bytes | None = None,
    content_type: str | None = None,
) -> tuple[int, bytes]:
    headers = {"Authorization": f"Bearer {id_token}"}
    if content_type is not None:
        headers["Content-Type"] = content_type
    req = request.Request(
        f"{_emulator_origin('FIREBASE_STORAGE_EMULATOR_HOST')}{path}",
        data=body,
        headers=headers,
        method=method,
    )
    try:
        with request.urlopen(req, timeout=10) as response:
            return response.status, response.read()
    except error.HTTPError as exc:
        return exc.code, exc.read()


def _firestore_document_path(collection_path: str) -> str:
    quoted_segments = [
        parse.quote(segment, safe="")
        for segment in collection_path.strip("/").split("/")
    ]
    return (
        f"/v1/projects/{_project_id()}/databases/"
        f"{parse.quote(_database_id(), safe='')}/documents/"
        f"{'/'.join(quoted_segments)}"
    )


def _firestore_template_payload(name: str, updated_at: str) -> dict[str, Any]:
    return {
        "fields": {
            "name": {"stringValue": name},
            "updatedAt": {"stringValue": updated_at},
            "deleted": {"booleanValue": False},
        }
    }


def _write_firestore_document(
    document_path: str,
    *,
    id_token: str,
    payload: dict[str, Any],
) -> int:
    return _firestore_request(
        "PATCH",
        _firestore_document_path(document_path),
        id_token=id_token,
        payload=payload,
    )[0]


def _read_firestore_document(document_path: str, *, id_token: str) -> tuple[int, dict[str, Any]]:
    status, body = _firestore_request(
        "GET",
        _firestore_document_path(document_path),
        id_token=id_token,
    )
    return status, json.loads(body.decode("utf-8")) if body else {}


def _delete_firestore_document(document_path: str, *, id_token: str) -> int:
    return _firestore_request(
        "DELETE",
        _firestore_document_path(document_path),
        id_token=id_token,
    )[0]


def _firestore_request(
    method: str,
    path: str,
    *,
    id_token: str,
    payload: dict[str, Any] | None = None,
) -> tuple[int, bytes]:
    headers = {"Authorization": f"Bearer {id_token}"}
    body = None
    if payload is not None:
        headers["Content-Type"] = "application/json"
        body = json.dumps(payload).encode("utf-8")
    req = request.Request(
        f"{_emulator_origin('FIRESTORE_EMULATOR_HOST')}{path}",
        data=body,
        headers=headers,
        method=method,
    )
    try:
        with request.urlopen(req, timeout=10) as response:
            return response.status, response.read()
    except error.HTTPError as exc:
        return exc.code, exc.read()


def test_meal_template_storage_rules_enforce_owner_only_canonical_path() -> None:
    run_id = uuid4().hex
    owner_email = f"ch-06-003b-template-storage-owner-{run_id}@example.test"
    other_email = f"ch-06-003b-template-storage-other-{run_id}@example.test"
    admin_email = f"ch-06-003b-template-storage-admin-{run_id}@example.test"
    owner_token = ""
    other_token = ""
    admin_initial_token = ""
    admin_token = ""
    canonical_path = ""

    try:
        owner_uid, owner_token = _sign_up_auth_emulator_user(owner_email)
        _, other_token = _sign_up_auth_emulator_user(other_email)
        admin_uid, admin_initial_token = _sign_up_auth_emulator_user(admin_email)

        _configure_admin_claims(admin_uid)
        admin_token = _sign_in_auth_emulator_user(admin_email)
        assert _decode_token_payload(admin_token).get("admin") is True

        template_id = f"template-{run_id}"
        canonical_path = f"mealTemplates/{owner_uid}/{template_id}-image.jpg"
        legacy_path = f"myMeals/{owner_uid}/{template_id}-image.jpg"

        assert (
            _upload_storage_object(
                canonical_path,
                id_token=owner_token,
                body=TEMPLATE_BYTES,
            )
            == 200
        )
        owner_read_status, owner_read_body = _read_storage_object(
            canonical_path,
            id_token=owner_token,
        )
        assert owner_read_status == 200
        assert owner_read_body == TEMPLATE_BYTES
        assert _delete_storage_object(canonical_path, id_token=owner_token) == 204

        assert (
            _upload_storage_object(
                canonical_path,
                id_token=owner_token,
                body=TEMPLATE_BYTES,
            )
            == 200
        )
        other_read_status, _ = _read_storage_object(canonical_path, id_token=other_token)
        assert other_read_status == 403
        assert (
            _upload_storage_object(
                canonical_path,
                id_token=other_token,
                body=b"non-owner-template-image",
            )
            == 403
        )

        for denied_token in (owner_token, other_token, admin_token):
            legacy_read_status, _ = _read_storage_object(
                legacy_path,
                id_token=denied_token,
            )
            assert legacy_read_status == 403
            assert (
                _upload_storage_object(
                    legacy_path,
                    id_token=denied_token,
                    body=b"legacy-template-image",
                )
                == 403
            )
    finally:
        if canonical_path and owner_token:
            _delete_storage_object(canonical_path, id_token=owner_token)
        for token in (owner_token, other_token, admin_token):
            if token:
                _delete_auth_emulator_user(token)
        if admin_initial_token:
            _delete_auth_emulator_user(admin_initial_token)
        _reset_firebase_admin_apps()


def test_meal_template_firestore_rules_enforce_owner_only_canonical_path() -> None:
    run_id = uuid4().hex
    owner_email = f"ch-06-003b-template-firestore-owner-{run_id}@example.test"
    other_email = f"ch-06-003b-template-firestore-other-{run_id}@example.test"
    owner_token = ""
    other_token = ""
    canonical_document_path = ""

    try:
        owner_uid, owner_token = _sign_up_auth_emulator_user(owner_email)
        _, other_token = _sign_up_auth_emulator_user(other_email)

        template_id = f"template-{run_id}"
        canonical_document_path = f"users/{owner_uid}/mealTemplates/{template_id}"
        legacy_document_path = f"users/{owner_uid}/myMeals/{template_id}"

        assert (
            _write_firestore_document(
                canonical_document_path,
                id_token=owner_token,
                payload=_firestore_template_payload(
                    "Owner template",
                    "2026-06-11T10:00:00.000Z",
                ),
            )
            == 200
        )

        owner_read_status, owner_read_body = _read_firestore_document(
            canonical_document_path,
            id_token=owner_token,
        )
        assert owner_read_status == 200
        assert (
            owner_read_body["fields"]["name"]["stringValue"] == "Owner template"
        )

        assert (
            _write_firestore_document(
                canonical_document_path,
                id_token=owner_token,
                payload=_firestore_template_payload(
                    "Updated owner template",
                    "2026-06-11T10:05:00.000Z",
                ),
            )
            == 200
        )
        updated_read_status, updated_read_body = _read_firestore_document(
            canonical_document_path,
            id_token=owner_token,
        )
        assert updated_read_status == 200
        assert (
            updated_read_body["fields"]["name"]["stringValue"]
            == "Updated owner template"
        )

        other_read_status, _ = _read_firestore_document(
            canonical_document_path,
            id_token=other_token,
        )
        assert other_read_status == 403
        assert (
            _write_firestore_document(
                canonical_document_path,
                id_token=other_token,
                payload=_firestore_template_payload(
                    "Non-owner template",
                    "2026-06-11T10:10:00.000Z",
                ),
            )
            == 403
        )

        assert (
            _write_firestore_document(
                legacy_document_path,
                id_token=owner_token,
                payload=_firestore_template_payload(
                    "Legacy owner template",
                    "2026-06-11T10:15:00.000Z",
                ),
            )
            == 403
        )
        legacy_owner_read_status, _ = _read_firestore_document(
            legacy_document_path,
            id_token=owner_token,
        )
        assert legacy_owner_read_status == 403
        assert (
            _write_firestore_document(
                legacy_document_path,
                id_token=other_token,
                payload=_firestore_template_payload(
                    "Legacy non-owner template",
                    "2026-06-11T10:20:00.000Z",
                ),
            )
            == 403
        )

        assert _delete_firestore_document(canonical_document_path, id_token=owner_token) == 200
        deleted_read_status, _ = _read_firestore_document(
            canonical_document_path,
            id_token=owner_token,
        )
        assert deleted_read_status == 404
    finally:
        if canonical_document_path and owner_token:
            _delete_firestore_document(canonical_document_path, id_token=owner_token)
        for token in (owner_token, other_token):
            if token:
                _delete_auth_emulator_user(token)
