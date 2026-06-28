"""Firestore rules emulator evidence for feedback path isolation."""

from __future__ import annotations

import json
import os
from typing import Any, cast
from urllib import error, parse, request
from uuid import uuid4

import pytest
from google.cloud import firestore


pytestmark = pytest.mark.skipif(
    not os.getenv("FIREBASE_AUTH_EMULATOR_HOST")
    or not os.getenv("FIRESTORE_EMULATOR_HOST"),
    reason="Firebase Auth and Firestore emulators are not configured.",
)


PASSWORD = "emulator-password-123"


def _project_id() -> str:
    return os.getenv("FIREBASE_PROJECT_ID") or "demo-fitaly-local"


def _database_id() -> str:
    return os.getenv("FIRESTORE_DATABASE_ID") or "(default)"


def _emulator_firestore_client() -> firestore.Client:
    client_class = cast(Any, firestore.Client)
    return cast(
        firestore.Client,
        client_class(project=_project_id(), database=_database_id()),
    )


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


def _delete_auth_emulator_user(id_token: str) -> None:
    try:
        _post_auth_emulator("accounts:delete", {"idToken": id_token})
    except Exception:
        return


def _delete_feedback_document_with_admin_emulator(
    *,
    owner_uid: str,
    feedback_id: str,
    run_id: str,
) -> None:
    if not owner_uid or not run_id or feedback_id != f"feedback-{run_id}":
        return
    (
        _emulator_firestore_client()
        .collection("users")
        .document(owner_uid)
        .collection("feedback")
        .document(feedback_id)
        .delete()
    )


def _delete_meal_document_with_admin_emulator(
    *,
    owner_uid: str,
    meal_id: str,
    run_id: str,
) -> None:
    if not owner_uid or not run_id or meal_id != f"meal-{run_id}":
        return
    (
        _emulator_firestore_client()
        .collection("users")
        .document(owner_uid)
        .collection("meals")
        .document(meal_id)
        .delete()
    )


def _firestore_document_path(document_path: str) -> str:
    quoted_segments = [
        parse.quote(segment, safe="")
        for segment in document_path.strip("/").split("/")
    ]
    return (
        f"/v1/projects/{_project_id()}/databases/"
        f"{parse.quote(_database_id(), safe='')}/documents/"
        f"{'/'.join(quoted_segments)}"
    )


def _feedback_payload(message: str) -> dict[str, Any]:
    return {
        "fields": {
            "message": {"stringValue": message},
            "createdAt": {"stringValue": "2026-06-12T10:00:00.000Z"},
            "source": {"stringValue": "rules-emulator"},
        }
    }


def _ingredient_product_payload(run_id: str) -> dict[str, Any]:
    return {
        "fields": {
            "ingredientProductId": {"stringValue": f"ingredient-product-{run_id}"},
            "recordScope": {"stringValue": "user_scoped"},
            "displayName": {"stringValue": "Owsianka"},
        }
    }


def _meal_payload(run_id: str, *, include_planning_source: bool = False) -> dict[str, Any]:
    fields: dict[str, Any] = {
        "mealId": {"stringValue": f"meal-{run_id}"},
        "name": {"stringValue": "Rules emulator meal"},
        "updatedAt": {"stringValue": "2026-06-28T10:00:00.000Z"},
    }
    if include_planning_source:
        fields["planningSource"] = {
            "mapValue": {
                "fields": {
                    "plannedMealId": {"stringValue": f"planned-{run_id}"},
                    "plannedMealVersion": {"integerValue": "1"},
                    "sourceType": {"stringValue": "manual"},
                    "nutritionEstimateState": {"stringValue": "known"},
                }
            }
        }
    return {"fields": fields}


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


def _read_firestore_document(
    document_path: str,
    *,
    id_token: str,
) -> tuple[int, dict[str, Any]]:
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


def test_feedback_firestore_rules_enforce_canonical_owner_create_read_only() -> None:
    run_id = uuid4().hex
    owner_email = f"ch-07-005-feedback-firestore-owner-{run_id}@example.test"
    other_email = f"ch-07-005-feedback-firestore-other-{run_id}@example.test"
    owner_uid = ""
    feedback_id = f"feedback-{run_id}"
    owner_token = ""
    other_token = ""

    try:
        owner_uid, owner_token = _sign_up_auth_emulator_user(owner_email)
        _, other_token = _sign_up_auth_emulator_user(other_email)

        canonical_path = f"users/{owner_uid}/feedback/{feedback_id}"

        assert (
            _write_firestore_document(
                canonical_path,
                id_token=owner_token,
                payload=_feedback_payload("Owner feedback"),
            )
            == 200
        )

        owner_read_status, owner_read_body = _read_firestore_document(
            canonical_path,
            id_token=owner_token,
        )
        assert owner_read_status == 200
        assert owner_read_body["fields"]["message"]["stringValue"] == "Owner feedback"

        assert (
            _write_firestore_document(
                canonical_path,
                id_token=owner_token,
                payload=_feedback_payload("Updated feedback"),
            )
            == 403
        )
        assert _delete_firestore_document(canonical_path, id_token=owner_token) == 403

        non_owner_create_path = (
            f"users/{owner_uid}/feedback/non-owner-create-{run_id}"
        )
        assert (
            _write_firestore_document(
                non_owner_create_path,
                id_token=other_token,
                payload=_feedback_payload("Non-owner feedback"),
            )
            == 403
        )
        non_owner_read_status, _ = _read_firestore_document(
            canonical_path,
            id_token=other_token,
        )
        assert non_owner_read_status == 403

        legacy_path = f"feedbacks/{feedback_id}"
        for denied_token in (owner_token, other_token):
            assert (
                _write_firestore_document(
                    legacy_path,
                    id_token=denied_token,
                    payload=_feedback_payload("Legacy feedback"),
                )
                == 403
            )
            legacy_read_status, _ = _read_firestore_document(
                legacy_path,
                id_token=denied_token,
            )
            assert legacy_read_status == 403
    finally:
        try:
            _delete_feedback_document_with_admin_emulator(
                owner_uid=owner_uid,
                feedback_id=feedback_id,
                run_id=run_id,
            )
        except Exception:
            pass
        for token in (owner_token, other_token):
            if token:
                _delete_auth_emulator_user(token)


def test_product_ingredient_firestore_rules_deny_client_sdk_access() -> None:
    run_id = uuid4().hex
    owner_email = f"product-ingredient-rules-owner-{run_id}@example.test"
    other_email = f"product-ingredient-rules-other-{run_id}@example.test"
    owner_uid = ""
    owner_token = ""
    other_token = ""

    try:
        owner_uid, owner_token = _sign_up_auth_emulator_user(owner_email)
        _, other_token = _sign_up_auth_emulator_user(other_email)

        paths = (
            f"ingredientProducts/global-{run_id}",
            f"users/{owner_uid}/ingredientProducts/user-{run_id}",
        )
        for document_path in paths:
            for denied_token in (owner_token, other_token):
                assert (
                    _write_firestore_document(
                        document_path,
                        id_token=denied_token,
                        payload=_ingredient_product_payload(run_id),
                    )
                    == 403
                )
                read_status, _ = _read_firestore_document(
                    document_path,
                    id_token=denied_token,
                )
                assert read_status == 403
                assert _delete_firestore_document(
                    document_path,
                    id_token=denied_token,
                ) == 403
    finally:
        for token in (owner_token, other_token):
            if token:
                _delete_auth_emulator_user(token)


def test_meal_firestore_rules_block_backend_side_effect_writes() -> None:
    run_id = uuid4().hex
    owner_email = f"meal-side-effect-rules-owner-{run_id}@example.test"
    owner_uid = ""
    owner_token = ""
    meal_id = f"meal-{run_id}"

    try:
        owner_uid, owner_token = _sign_up_auth_emulator_user(owner_email)
        meal_path = f"users/{owner_uid}/meals/{meal_id}"

        assert (
            _write_firestore_document(
                meal_path,
                id_token=owner_token,
                payload=_meal_payload(run_id),
            )
            == 200
        )
        assert (
            _write_firestore_document(
                meal_path,
                id_token=owner_token,
                payload=_meal_payload(run_id, include_planning_source=True),
            )
            == 403
        )
        assert _delete_firestore_document(meal_path, id_token=owner_token) == 403
    finally:
        try:
            _delete_meal_document_with_admin_emulator(
                owner_uid=owner_uid,
                meal_id=meal_id,
                run_id=run_id,
            )
        except Exception:
            pass
        if owner_token:
            _delete_auth_emulator_user(owner_token)
