"""Storage rules emulator evidence for feedback attachment path isolation."""

from __future__ import annotations

import base64
import json
import os
from typing import Any, Callable, cast
from urllib import error, parse, request
from uuid import uuid4

import firebase_admin
import pytest
import firebase_admin.auth as firebase_auth


pytestmark = pytest.mark.skipif(
    not os.getenv("FIREBASE_AUTH_EMULATOR_HOST")
    or not os.getenv("FIREBASE_STORAGE_EMULATOR_HOST"),
    reason="Firebase Auth and Storage emulators are not configured.",
)


ATTACHMENT_BYTES = b"feedback-rules-emulator-attachment"
PASSWORD = "emulator-password-123"


def _project_id() -> str:
    return os.getenv("FIREBASE_PROJECT_ID") or "demo-fitaly-local"


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


def test_feedback_storage_rules_enforce_owner_admin_and_denied_paths() -> None:
    run_id = uuid4().hex
    owner_email = f"ch-05-003d-owner-{run_id}@example.test"
    other_email = f"ch-05-003d-other-{run_id}@example.test"
    admin_email = f"ch-05-003d-admin-{run_id}@example.test"
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

        canonical_path = f"feedback/{owner_uid}/feedback-{run_id}/attachment.jpg"
        legacy_path = f"feedbacks/{owner_uid}/feedback-{run_id}/attachment.jpg"
        unknown_path = f"unexpected/{owner_uid}/feedback-{run_id}/attachment.jpg"

        assert (
            _upload_storage_object(
                canonical_path,
                id_token=owner_token,
                body=ATTACHMENT_BYTES,
            )
            == 200
        )
        owner_read_status, owner_read_body = _read_storage_object(
            canonical_path,
            id_token=owner_token,
        )
        assert owner_read_status == 200
        assert owner_read_body == ATTACHMENT_BYTES

        other_read_status, _ = _read_storage_object(canonical_path, id_token=other_token)
        assert other_read_status == 403
        assert (
            _upload_storage_object(
                canonical_path,
                id_token=other_token,
                body=b"non-owner-overwrite",
            )
            == 403
        )

        admin_read_status, admin_read_body = _read_storage_object(
            canonical_path,
            id_token=admin_token,
        )
        assert admin_read_status == 200
        assert admin_read_body == ATTACHMENT_BYTES

        for token in (owner_token, admin_token):
            legacy_read_status, _ = _read_storage_object(legacy_path, id_token=token)
            assert legacy_read_status == 403
            assert (
                _upload_storage_object(
                    legacy_path,
                    id_token=token,
                    body=b"legacy-feedback-attachment",
                )
                == 403
            )

        unknown_read_status, _ = _read_storage_object(unknown_path, id_token=owner_token)
        assert unknown_read_status == 403
        assert (
            _upload_storage_object(
                unknown_path,
                id_token=owner_token,
                body=b"unknown-feedback-attachment",
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
