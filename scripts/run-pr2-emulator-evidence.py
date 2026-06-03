#!/usr/bin/env python3
"""Run PR2 account export/delete evidence against local Firebase emulators."""

from __future__ import annotations

import json
import os
import sys
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib import error, request


REPO_ROOT = Path(__file__).resolve().parents[1]
SERVICE_ACCOUNT_PATH = REPO_ROOT / "service-account.json"
PROJECT_ID = "demo-fitaly-local"
BUCKET_NAME = f"{PROJECT_ID}.appspot.com"

sys.path.insert(0, str(REPO_ROOT))

os.environ["ENVIRONMENT"] = "local"
os.environ["FIREBASE_PROJECT_ID"] = PROJECT_ID
os.environ["FIREBASE_STORAGE_BUCKET"] = BUCKET_NAME
os.environ["FIRESTORE_DATABASE_ID"] = "(default)"
os.environ["EAGER_FIREBASE_INIT"] = "false"
# Keep emulator evidence isolated from checked-in/local .env credentials.
os.environ["FIREBASE_CLIENT_EMAIL"] = ""
os.environ["FIREBASE_PRIVATE_KEY"] = ""
if SERVICE_ACCOUNT_PATH.exists():
    os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = str(SERVICE_ACCOUNT_PATH)

from fastapi.testclient import TestClient  # noqa: E402
from google.cloud.firestore_v1.base_query import FieldFilter  # noqa: E402

from app.api.deps.auth import decode_firebase_token  # noqa: E402
from app.core.firestore_constants import (  # noqa: E402
    AI_CREDITS_SUBCOLLECTION,
    AI_CREDIT_IDEMPOTENCY_SUBCOLLECTION,
    AI_CREDIT_TRANSACTIONS_SUBCOLLECTION,
    AI_RUNS_COLLECTION,
    BADGES_SUBCOLLECTION,
    BILLING_DOCUMENT_ID,
    BILLING_SUBCOLLECTION,
    CHAT_THREADS_SUBCOLLECTION,
    FEEDBACK_SUBCOLLECTION,
    MEALS_SUBCOLLECTION,
    MEMORY_SUBCOLLECTION,
    MESSAGES_SUBCOLLECTION,
    MY_MEALS_SUBCOLLECTION,
    PREFS_SUBCOLLECTION,
    STREAK_SUBCOLLECTION,
    USERNAMES_COLLECTION,
    USERS_COLLECTION,
)
from app.db.firebase import get_firestore, get_storage_bucket  # noqa: E402
from app.main import app  # noqa: E402


ARTIFACTS_ROOT = REPO_ROOT / "evidence" / "runs"
RUN_ID = f"pr2-emulator-{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}"
RUN_DIR = ARTIFACTS_ROOT / RUN_ID


@dataclass(frozen=True)
class EmulatorUser:
    alias: str
    uid: str
    username: str
    id_token: str


def _json_default(value: object) -> str:
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def _write_json(filename: str, payload: dict[str, Any]) -> Path:
    RUN_DIR.mkdir(parents=True, exist_ok=True)
    path = RUN_DIR / filename
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True, default=_json_default) + "\n",
        encoding="utf-8",
    )
    return path


def _expect(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def _auth_emulator_host() -> str:
    host = os.getenv("FIREBASE_AUTH_EMULATOR_HOST", "").strip()
    _expect(bool(host), "FIREBASE_AUTH_EMULATOR_HOST is not set.")
    if host.startswith(("http://", "https://")):
        return host
    return f"http://{host}"


def _request_json(
    *,
    method: str,
    url: str,
    body: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
) -> dict[str, Any]:
    payload = None
    req_headers = {"Accept": "application/json"}
    if headers:
        req_headers.update(headers)
    if body is not None:
        payload = json.dumps(body).encode("utf-8")
        req_headers["Content-Type"] = "application/json"

    req = request.Request(url=url, data=payload, method=method, headers=req_headers)
    try:
        with request.urlopen(req, timeout=20) as response:  # noqa: S310
            raw = response.read()
            if not raw:
                return {}
            return dict(json.loads(raw.decode("utf-8")))
    except error.HTTPError as exc:
        raw = exc.read().decode("utf-8")
        raise RuntimeError(f"HTTP {exc.code} from {url}: {raw}") from exc


def _create_auth_user(alias: str, username: str) -> EmulatorUser:
    auth_host = _auth_emulator_host()
    # Local emulator-only fixture. Do not write this email to evidence artifacts.
    email = f"{RUN_ID}-{alias}@example.test"
    password = "FitalyLocalEvidence123!"
    payload = _request_json(
        method="POST",
        url=(
            f"{auth_host}/identitytoolkit.googleapis.com/v1/"
            "accounts:signUp?key=local-evidence"
        ),
        body={
            "email": email,
            "password": password,
            "returnSecureToken": True,
        },
    )
    uid = str(payload.get("localId") or "").strip()
    id_token = str(payload.get("idToken") or "").strip()
    _expect(uid != "", f"Auth emulator did not return uid for {alias}.")
    _expect(id_token != "", f"Auth emulator did not return id token for {alias}.")
    _expect(
        len(id_token.split(".")) == 3,
        (
            f"Auth emulator returned a non-JWT id token for {alias}: "
            f"segments={len(id_token.split('.'))}, length={len(id_token)}."
        ),
    )
    return EmulatorUser(alias=alias, uid=uid, username=username, id_token=id_token)


def _put_document(path: tuple[str, ...], payload: dict[str, Any]) -> None:
    db = get_firestore()
    ref = db.collection(path[0]).document(path[1])
    for index in range(2, len(path), 2):
        ref = ref.collection(path[index]).document(path[index + 1])
    ref.set(payload)


def _seed_firestore(user: EmulatorUser) -> None:
    owner = user.alias
    db = get_firestore()
    user_ref = db.collection(USERS_COLLECTION).document(user.uid)
    user_ref.set(
        {
            "uid": user.uid,
            "username": user.username,
            "profile": {"language": "en", "evidenceOwner": owner},
            "plan": "free",
        }
    )
    db.collection(USERNAMES_COLLECTION).document(user.username).set({"uid": user.uid})

    _put_document(
        (USERS_COLLECTION, user.uid, MEALS_SUBCOLLECTION, f"meal-{owner}"),
        {"id": f"meal-{owner}", "evidenceOwner": owner, "deleted": False},
    )
    _put_document(
        (USERS_COLLECTION, user.uid, MY_MEALS_SUBCOLLECTION, f"saved-{owner}"),
        {"id": f"saved-{owner}", "evidenceOwner": owner, "deleted": False},
    )
    _put_document(
        (USERS_COLLECTION, user.uid, CHAT_THREADS_SUBCOLLECTION, f"thread-{owner}"),
        {"id": f"thread-{owner}", "title": f"fixture-thread-{owner}"},
    )
    _put_document(
        (
            USERS_COLLECTION,
            user.uid,
            CHAT_THREADS_SUBCOLLECTION,
            f"thread-{owner}",
            MESSAGES_SUBCOLLECTION,
            f"msg-{owner}",
        ),
        {
            "id": f"msg-{owner}",
            "role": "assistant",
            "content": f"fixture-message-{owner}",
            "evidenceOwner": owner,
            "deleted": False,
        },
    )
    _put_document(
        (
            USERS_COLLECTION,
            user.uid,
            CHAT_THREADS_SUBCOLLECTION,
            f"thread-{owner}",
            MEMORY_SUBCOLLECTION,
            "current",
        ),
        {"summary": f"fixture-memory-{owner}", "evidenceOwner": owner},
    )
    _put_document(
        (USERS_COLLECTION, user.uid, "notifications", f"notif-{owner}"),
        {"id": f"notif-{owner}", "evidenceOwner": owner, "enabled": True},
    )
    _put_document(
        (USERS_COLLECTION, user.uid, PREFS_SUBCOLLECTION, "notifications"),
        {"notifications": {"evidenceOwner": owner, "motivationEnabled": True}},
    )
    _put_document(
        (USERS_COLLECTION, user.uid, FEEDBACK_SUBCOLLECTION, f"feedback-{owner}"),
        {
            "id": f"feedback-{owner}",
            "evidenceOwner": owner,
            "attachmentPath": f"feedback/{user.uid}/feedback-{owner}/attachment.txt",
        },
    )
    _put_document(
        (USERS_COLLECTION, user.uid, BADGES_SUBCOLLECTION, f"badge-{owner}"),
        {"id": f"badge-{owner}", "evidenceOwner": owner},
    )
    _put_document(
        (USERS_COLLECTION, user.uid, STREAK_SUBCOLLECTION, "current"),
        {"current": 3, "lastDate": "2026-06-03"},
    )
    _put_document(
        (
            USERS_COLLECTION,
            user.uid,
            BILLING_SUBCOLLECTION,
            BILLING_DOCUMENT_ID,
            AI_CREDITS_SUBCOLLECTION,
            "current",
        ),
        {"balance": 10, "allocation": 100, "evidenceOwner": owner},
    )
    _put_document(
        (
            USERS_COLLECTION,
            user.uid,
            BILLING_SUBCOLLECTION,
            BILLING_DOCUMENT_ID,
            AI_CREDIT_TRANSACTIONS_SUBCOLLECTION,
            f"tx-{owner}",
        ),
        {"delta": -1, "evidenceOwner": owner},
    )
    _put_document(
        (
            USERS_COLLECTION,
            user.uid,
            BILLING_SUBCOLLECTION,
            BILLING_DOCUMENT_ID,
            AI_CREDIT_IDEMPOTENCY_SUBCOLLECTION,
            f"idem-{owner}",
        ),
        {"state": "deducted", "evidenceOwner": owner},
    )
    db.collection(AI_RUNS_COLLECTION).document(f"run-{owner}").set(
        {
            "runId": f"run-{owner}",
            "userId": user.uid,
            "status": "completed",
            "evidenceOwner": owner,
        }
    )


def _upload_blob(path: str, content: str) -> None:
    bucket = get_storage_bucket()
    bucket.blob(path).upload_from_string(content, content_type="text/plain")


def _seed_storage(user: EmulatorUser) -> None:
    owner = user.alias
    _upload_blob(f"avatars/{user.uid}/avatar.jpg", f"avatar-{owner}")
    _upload_blob(f"meals/{user.uid}/meal.jpg", f"meal-photo-{owner}")
    _upload_blob(f"myMeals/{user.uid}/saved.jpg", f"saved-photo-{owner}")
    _upload_blob(
        f"feedback/{user.uid}/feedback-{owner}/attachment.txt",
        f"feedback-attachment-{owner}",
    )


def _count_collection_documents(path: tuple[str, ...]) -> int:
    db = get_firestore()
    ref = db.collection(path[0]).document(path[1])
    if len(path) == 2:
        snapshot = ref.get()
        return 1 if snapshot.exists else 0
    for index in range(2, len(path) - 1, 2):
        ref = ref.collection(path[index]).document(path[index + 1])
    collection_name = path[-1]
    return len(list(ref.collection(collection_name).stream()))


def _count_query(collection_name: str, field: str, value: object) -> int:
    return len(
        list(
            get_firestore()
            .collection(collection_name)
            .where(filter=FieldFilter(field, "==", value))
            .stream()
        )
    )


def _count_storage_prefix(prefix: str) -> int:
    return len(list(get_storage_bucket().list_blobs(prefix=prefix)))


def _state_counts(user: EmulatorUser) -> dict[str, Any]:
    uid = user.uid
    username_snapshot = (
        get_firestore().collection(USERNAMES_COLLECTION).document(user.username).get()
    )
    return {
        "alias": user.alias,
        "profile": _count_collection_documents((USERS_COLLECTION, uid)),
        "usernameMapping": 1 if username_snapshot.exists else 0,
        "meals": _count_collection_documents(
            (USERS_COLLECTION, uid, MEALS_SUBCOLLECTION)
        ),
        "myMeals": _count_collection_documents(
            (USERS_COLLECTION, uid, MY_MEALS_SUBCOLLECTION)
        ),
        "chatThreads": _count_collection_documents(
            (USERS_COLLECTION, uid, CHAT_THREADS_SUBCOLLECTION)
        ),
        "chatMessages": _count_collection_documents(
            (
                USERS_COLLECTION,
                uid,
                CHAT_THREADS_SUBCOLLECTION,
                f"thread-{user.alias}",
                MESSAGES_SUBCOLLECTION,
            )
        ),
        "chatMemory": _count_collection_documents(
            (
                USERS_COLLECTION,
                uid,
                CHAT_THREADS_SUBCOLLECTION,
                f"thread-{user.alias}",
                MEMORY_SUBCOLLECTION,
            )
        ),
        "notifications": _count_collection_documents(
            (USERS_COLLECTION, uid, "notifications")
        ),
        "prefs": _count_collection_documents((USERS_COLLECTION, uid, PREFS_SUBCOLLECTION)),
        "feedback": _count_collection_documents(
            (USERS_COLLECTION, uid, FEEDBACK_SUBCOLLECTION)
        ),
        "badges": _count_collection_documents((USERS_COLLECTION, uid, BADGES_SUBCOLLECTION)),
        "streak": _count_collection_documents((USERS_COLLECTION, uid, STREAK_SUBCOLLECTION)),
        "billingDocs": _count_collection_documents(
            (USERS_COLLECTION, uid, BILLING_SUBCOLLECTION)
        ),
        "aiCredits": _count_collection_documents(
            (
                USERS_COLLECTION,
                uid,
                BILLING_SUBCOLLECTION,
                BILLING_DOCUMENT_ID,
                AI_CREDITS_SUBCOLLECTION,
            )
        ),
        "aiCreditTransactions": _count_collection_documents(
            (
                USERS_COLLECTION,
                uid,
                BILLING_SUBCOLLECTION,
                BILLING_DOCUMENT_ID,
                AI_CREDIT_TRANSACTIONS_SUBCOLLECTION,
            )
        ),
        "aiCreditIdempotency": _count_collection_documents(
            (
                USERS_COLLECTION,
                uid,
                BILLING_SUBCOLLECTION,
                BILLING_DOCUMENT_ID,
                AI_CREDIT_IDEMPOTENCY_SUBCOLLECTION,
            )
        ),
        "aiRuns": _count_query(AI_RUNS_COLLECTION, "userId", uid),
        "storage": {
            "avatars": _count_storage_prefix(f"avatars/{uid}/"),
            "meals": _count_storage_prefix(f"meals/{uid}/"),
            "myMeals": _count_storage_prefix(f"myMeals/{uid}/"),
            "feedback": _count_storage_prefix(f"feedback/{uid}/"),
        },
    }


def _extract_owners(items: Any) -> list[str]:
    if not isinstance(items, list):
        return []
    owners: list[str] = []
    for item in items:
        if isinstance(item, dict):
            owner = item.get("evidenceOwner")
            if isinstance(owner, str) and owner:
                owners.append(owner)
    return sorted(set(owners))


def _export_summary(payload: dict[str, Any]) -> dict[str, Any]:
    profile = payload.get("profile")
    profile_owner = None
    if isinstance(profile, dict):
        profile_document = profile.get("profile")
        if isinstance(profile_document, dict):
            profile_owner = profile_document.get("evidenceOwner")

    return {
        "status": "summarized",
        "profileOwner": profile_owner,
        "counts": {
            "meals": len(payload.get("meals", [])),
            "myMeals": len(payload.get("myMeals", [])),
            "chatMessages": len(payload.get("chatMessages", [])),
            "chatMemory": len(payload.get("chatMemory", [])),
            "aiRuns": len(payload.get("aiRuns", [])),
            "notifications": len(payload.get("notifications", [])),
            "notificationPrefs": 1 if payload.get("notificationPrefs") else 0,
            "feedback": len(payload.get("feedback", [])),
        },
        "owners": {
            "meals": _extract_owners(payload.get("meals")),
            "myMeals": _extract_owners(payload.get("myMeals")),
            "chatMessages": _extract_owners(payload.get("chatMessages")),
            "chatMemory": _extract_owners(payload.get("chatMemory")),
            "aiRuns": _extract_owners(payload.get("aiRuns")),
            "notifications": _extract_owners(payload.get("notifications")),
            "feedback": _extract_owners(payload.get("feedback")),
        },
    }


def _assert_export_isolated(export: dict[str, Any], expected_alias: str) -> None:
    summary = _export_summary(export)
    _expect(
        summary["profileOwner"] == expected_alias,
        f"Export profile owner mismatch: expected {expected_alias}.",
    )
    for section, owners in summary["owners"].items():
        _expect(
            owners == [expected_alias],
            f"Export section {section} leaked owners {owners}; expected {expected_alias}.",
        )
    expected_counts = {
        "meals": 1,
        "myMeals": 1,
        "chatMessages": 1,
        "chatMemory": 1,
        "aiRuns": 1,
        "notifications": 1,
        "notificationPrefs": 1,
        "feedback": 1,
    }
    _expect(
        summary["counts"] == expected_counts,
        f"Export counts mismatch: {summary['counts']}.",
    )


def _request_export(client: TestClient, user: EmulatorUser) -> tuple[int, dict[str, Any]]:
    response = client.get(
        "/api/v1/users/me/export",
        headers={"Authorization": f"Bearer {user.id_token}"},
    )
    payload = response.json()
    _expect(isinstance(payload, dict), "Export response is not a JSON object.")
    return response.status_code, payload


def _request_delete(client: TestClient, user: EmulatorUser) -> tuple[int, dict[str, Any]]:
    response = client.post(
        "/api/v1/users/me/delete",
        headers={"Authorization": f"Bearer {user.id_token}"},
    )
    payload = response.json()
    _expect(isinstance(payload, dict), "Delete response is not a JSON object.")
    return response.status_code, payload


def _assert_auth_token_verifies(user: EmulatorUser) -> None:
    claims = decode_firebase_token(user.id_token)
    uid = str(claims.get("uid") or "").strip()
    _expect(uid == user.uid, f"Auth token resolved uid {uid}; expected {user.uid}.")


def _assert_deleted(counts: dict[str, Any]) -> None:
    non_storage = {key: value for key, value in counts.items() if key not in {"alias", "storage"}}
    _expect(
        all(value == 0 for value in non_storage.values()),
        f"Deleted user still has Firestore/Auth-owned state: {non_storage}.",
    )
    storage = counts["storage"]
    _expect(
        all(value == 0 for value in storage.values()),
        f"Deleted user still has storage objects: {storage}.",
    )


def _assert_preserved(counts: dict[str, Any]) -> None:
    expected_positive = {
        "profile",
        "usernameMapping",
        "meals",
        "myMeals",
        "chatThreads",
        "chatMessages",
        "chatMemory",
        "notifications",
        "prefs",
        "feedback",
        "badges",
        "streak",
        "aiCredits",
        "aiCreditTransactions",
        "aiCreditIdempotency",
        "aiRuns",
    }
    missing = {
        key: counts[key]
        for key in expected_positive
        if counts.get(key) != 1
    }
    _expect(not missing, f"Preserved user counts changed: {missing}.")
    storage = counts["storage"]
    _expect(
        storage == {"avatars": 1, "meals": 1, "myMeals": 1, "feedback": 1},
        f"Preserved user storage changed: {storage}.",
    )


def main() -> int:
    started_at = datetime.now(UTC)
    started_perf = time.perf_counter()
    checks: list[dict[str, Any]] = []

    user_a = _create_auth_user("user_a", f"{RUN_ID.lower()}-user-a")
    user_b = _create_auth_user("user_b", f"{RUN_ID.lower()}-user-b")
    for user in (user_a, user_b):
        _seed_firestore(user)
        _seed_storage(user)

    before_counts = {
        "user_a": _state_counts(user_a),
        "user_b": _state_counts(user_b),
    }
    _assert_preserved(before_counts["user_a"])
    _assert_preserved(before_counts["user_b"])
    _write_json("01-seed-state.json", {"status": "passed", "counts": before_counts})
    checks.append({"name": "seed_user_a_user_b", "status": "passed"})

    client = TestClient(app)
    _assert_auth_token_verifies(user_a)
    _assert_auth_token_verifies(user_b)

    export_a_status, export_a = _request_export(client, user_a)
    _expect(
        export_a_status == 200,
        f"User A export returned HTTP {export_a_status}: {export_a}.",
    )
    _assert_export_isolated(export_a, "user_a")
    export_a_summary = _export_summary(export_a)
    _write_json(
        "02-export-user-a.json",
        {"status": "passed", "httpStatus": export_a_status, "summary": export_a_summary},
    )
    checks.append({"name": "export_user_a_is_isolated", "status": "passed"})

    export_b_status, export_b = _request_export(client, user_b)
    _expect(
        export_b_status == 200,
        f"User B export returned HTTP {export_b_status}: {export_b}.",
    )
    _assert_export_isolated(export_b, "user_b")
    export_b_summary = _export_summary(export_b)
    _write_json(
        "03-export-user-b.json",
        {"status": "passed", "httpStatus": export_b_status, "summary": export_b_summary},
    )
    checks.append({"name": "export_user_b_is_isolated", "status": "passed"})

    delete_status, delete_payload = _request_delete(client, user_a)
    _expect(
        delete_status == 200,
        f"User A delete returned HTTP {delete_status}: {delete_payload}.",
    )
    _expect(delete_payload == {"deleted": True}, f"Unexpected delete payload: {delete_payload}.")
    after_counts = {
        "user_a": _state_counts(user_a),
        "user_b": _state_counts(user_b),
    }
    _assert_deleted(after_counts["user_a"])
    _assert_preserved(after_counts["user_b"])
    _write_json(
        "04-delete-user-a.json",
        {
            "status": "passed",
            "httpStatus": delete_status,
            "response": delete_payload,
            "postDeleteCounts": after_counts,
        },
    )
    checks.append({"name": "delete_user_a_preserves_user_b", "status": "passed"})

    finished_at = datetime.now(UTC)
    summary = {
        "runId": RUN_ID,
        "status": "passed",
        "startedAt": started_at.isoformat(),
        "finishedAt": finished_at.isoformat(),
        "durationMs": round((time.perf_counter() - started_perf) * 1000),
        "projectId": PROJECT_ID,
        "emulators": {
            "auth": "configured" if os.getenv("FIREBASE_AUTH_EMULATOR_HOST") else "missing",
            "firestore": "configured" if os.getenv("FIRESTORE_EMULATOR_HOST") else "missing",
            "storage": "configured" if os.getenv("FIREBASE_STORAGE_EMULATOR_HOST") else "missing",
        },
        "checks": checks,
        "artifacts": [
            "01-seed-state.json",
            "02-export-user-a.json",
            "03-export-user-b.json",
            "04-delete-user-a.json",
        ],
    }
    _write_json("summary.json", summary)
    print(f"PR2 emulator evidence passed: {RUN_DIR.relative_to(REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        failure = {
            "runId": RUN_ID,
            "status": "failed",
            "error": str(exc),
            "finishedAt": datetime.now(UTC).isoformat(),
        }
        _write_json("summary.json", failure)
        print(f"PR2 emulator evidence failed: {exc}", file=sys.stderr)
        raise
