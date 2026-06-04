"""Route-level emulator evidence for weekly report premium access."""

from datetime import datetime, timezone
import json
import os
from pathlib import Path
from typing import Any, Callable, cast
from unittest.mock import MagicMock
from urllib import request
from uuid import uuid4

import firebase_admin
import pytest
from fastapi.testclient import TestClient
from google.cloud import firestore
from pytest import MonkeyPatch
from pytest_mock import MockerFixture

from app.core.config import settings
from app.core.firestore_constants import (
    AI_CREDITS_CURRENT_DOCUMENT_ID,
    AI_CREDITS_SUBCOLLECTION,
    BILLING_DOCUMENT_ID,
    BILLING_SUBCOLLECTION,
    USERS_COLLECTION,
)
from app.schemas.weekly_reports import WeeklyReportPeriod, WeeklyReportResponse


pytestmark = pytest.mark.skipif(
    not os.getenv("FIRESTORE_EMULATOR_HOST")
    or not os.getenv("FIREBASE_AUTH_EMULATOR_HOST")
    or not os.getenv("FIREBASE_PROJECT_ID"),
    reason="Firestore/Auth emulators and FIREBASE_PROJECT_ID are not configured.",
)


def _auth_emulator_url(path: str) -> str:
    host = os.environ["FIREBASE_AUTH_EMULATOR_HOST"].strip()
    return f"http://{host}/identitytoolkit.googleapis.com/v1/{path}?key=fake-api-key"


def _post_auth_emulator(path: str, payload: dict[str, object]) -> dict[str, Any]:
    body = json.dumps(payload).encode("utf-8")
    req = request.Request(
        _auth_emulator_url(path),
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with request.urlopen(req, timeout=10) as response:
        return dict(json.loads(response.read().decode("utf-8")))


def _sign_up_auth_emulator_user(email: str, password: str) -> tuple[str, str]:
    payload = _post_auth_emulator(
        "accounts:signUp",
        {"email": email, "password": password, "returnSecureToken": True},
    )
    uid = str(payload["localId"])
    id_token = str(payload["idToken"])
    return uid, id_token


def _delete_auth_emulator_user(id_token: str) -> None:
    try:
        _post_auth_emulator("accounts:delete", {"idToken": id_token})
    except Exception:
        return


def _reset_firebase_singletons() -> None:
    from app.db import firebase as firebase_db

    firebase_db.get_firestore.cache_clear()
    firebase_db.get_storage_bucket.cache_clear()
    delete_app = cast(Callable[[firebase_admin.App], None], getattr(firebase_admin, "delete_app"))
    for firebase_app in list(firebase_admin._apps.values()):
        delete_app(firebase_app)


def _patch_emulator_firebase_settings(monkeypatch: MonkeyPatch) -> None:
    project_id = os.environ["FIREBASE_PROJECT_ID"].strip()
    database_id = os.getenv("FIRESTORE_DATABASE_ID") or "(default)"
    credentials_path = os.getenv("GOOGLE_APPLICATION_CREDENTIALS", "").strip()
    if not credentials_path:
        local_credentials = Path("service-account.json")
        if local_credentials.exists():
            credentials_path = str(local_credentials)

    monkeypatch.setenv("FIREBASE_PROJECT_ID", project_id)
    monkeypatch.setenv("FIRESTORE_DATABASE_ID", database_id)
    if credentials_path:
        monkeypatch.setenv("GOOGLE_APPLICATION_CREDENTIALS", credentials_path)

    monkeypatch.setattr(settings, "FIREBASE_PROJECT_ID", project_id)
    monkeypatch.setattr(settings, "FIRESTORE_DATABASE_ID", database_id)
    monkeypatch.setattr(settings, "GOOGLE_APPLICATION_CREDENTIALS", credentials_path)
    monkeypatch.setattr(settings, "WEEKLY_REPORTS_ENABLED", True)


def _emulator_firestore_client() -> firestore.Client:
    from app.db.firebase import get_firestore

    return get_firestore()


def _billing_ref(
    client: firestore.Client,
    user_id: str,
) -> firestore.DocumentReference:
    return (
        client.collection(USERS_COLLECTION)
        .document(user_id)
        .collection(BILLING_SUBCOLLECTION)
        .document(BILLING_DOCUMENT_ID)
    )


def _credits_ref(
    client: firestore.Client,
    user_id: str,
) -> firestore.DocumentReference:
    return (
        _billing_ref(client, user_id)
        .collection(AI_CREDITS_SUBCOLLECTION)
        .document(AI_CREDITS_CURRENT_DOCUMENT_ID)
    )


def _auth_headers(id_token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {id_token}"}


def _seed_canonical_credits_snapshot(
    client: firestore.Client,
    user_id: str,
    *,
    tier: str,
    period_start_at: datetime,
    period_end_at: datetime,
    revenuecat_entitlement_id: str | None = None,
    revenuecat_expiration_at: datetime | None = None,
    last_revenuecat_event_id: str | None = None,
) -> None:
    now = datetime(2026, 6, 4, 12, 0, tzinfo=timezone.utc)
    allocation = (
        settings.AI_CREDITS_PREMIUM if tier == "premium" else settings.AI_CREDITS_FREE
    )
    _credits_ref(client, user_id).set(
        {
            "tier": tier,
            "balance": allocation,
            "allocation": allocation,
            "periodStartAt": period_start_at,
            "periodEndAt": period_end_at,
            "renewalAnchorSource": (
                "premium_activation" if tier == "premium" else "free_cycle_start"
            ),
            "revenueCatEntitlementId": revenuecat_entitlement_id,
            "revenueCatExpirationAt": revenuecat_expiration_at,
            "lastRevenueCatEventId": last_revenuecat_event_id,
            "createdAt": now,
            "updatedAt": now,
        }
    )


def test_weekly_report_route_uses_firestore_credits_snapshot_for_premium_boundary(
    mock_auth_token_decoder: MagicMock,
    monkeypatch: MonkeyPatch,
    mocker: MockerFixture,
) -> None:
    mocker.stop(mock_auth_token_decoder)
    _patch_emulator_firebase_settings(monkeypatch)
    _reset_firebase_singletons()

    from app.main import app

    api_client = TestClient(app)
    firestore_client = _emulator_firestore_client()
    run_id = uuid4().hex
    password = "emulator-password-123"
    free_uid = ""
    free_token = ""
    premium_uid = ""
    premium_token = ""
    period_start_at = datetime(2026, 6, 1, 0, 0, tzinfo=timezone.utc)
    period_end_at = datetime(2026, 7, 1, 0, 0, tzinfo=timezone.utc)
    weekly_report_response = WeeklyReportResponse(
        status="insufficient_data",
        period=WeeklyReportPeriod(
            startDay="2026-03-09",
            endDay="2026-03-15",
        ),
        summary="Log a few complete days to unlock a weekly report.",
        insights=[],
        priorities=[],
    )
    get_weekly_report = mocker.patch(
        "app.api.routes.weekly_reports.get_weekly_report",
        return_value=weekly_report_response,
    )

    try:
        free_uid, free_token = _sign_up_auth_emulator_user(
            f"weekly-report-free-{run_id}@example.invalid",
            password,
        )
        premium_uid, premium_token = _sign_up_auth_emulator_user(
            f"weekly-report-premium-{run_id}@example.invalid",
            password,
        )
        assert free_uid != premium_uid

        _seed_canonical_credits_snapshot(
            firestore_client,
            free_uid,
            tier="free",
            period_start_at=period_start_at,
            period_end_at=period_end_at,
        )
        _seed_canonical_credits_snapshot(
            firestore_client,
            premium_uid,
            tier="premium",
            period_start_at=period_start_at,
            period_end_at=period_end_at,
            revenuecat_entitlement_id="premium",
            revenuecat_expiration_at=period_end_at,
            last_revenuecat_event_id=f"evt-weekly-report-{run_id}",
        )

        free_response = api_client.get(
            "/api/v2/users/me/reports/weekly?weekEnd=2026-03-15",
            headers=_auth_headers(free_token),
        )

        assert free_response.status_code == 403
        assert free_response.json() == {"detail": "WEEKLY_REPORT_PREMIUM_REQUIRED"}
        get_weekly_report.assert_not_called()

        premium_response = api_client.get(
            "/api/v2/users/me/reports/weekly?weekEnd=2026-03-15",
            headers=_auth_headers(premium_token),
        )

        assert premium_response.status_code == 200
        assert premium_response.json()["period"] == {
            "startDay": "2026-03-09",
            "endDay": "2026-03-15",
        }
        get_weekly_report.assert_awaited_once_with(
            premium_uid,
            week_end="2026-03-15",
        )
    finally:
        for uid in (free_uid, premium_uid):
            if not uid:
                continue
            _credits_ref(firestore_client, uid).delete()
            _billing_ref(firestore_client, uid).delete()
            firestore_client.collection(USERS_COLLECTION).document(uid).delete()
        if free_token:
            _delete_auth_emulator_user(free_token)
        if premium_token:
            _delete_auth_emulator_user(premium_token)
        _reset_firebase_singletons()
