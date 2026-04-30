from datetime import datetime, timezone
from typing import Literal

from fastapi.testclient import TestClient
from pytest_mock import MockerFixture

from app.core.exceptions import FirestoreServiceError
from app.main import app
from app.schemas.ai_credits import AiCreditsStatus, CreditCosts
from tests.types import AuthHeaders

client = TestClient(app)


def _credits_status(
    *,
    tier: Literal["free", "premium"],
    balance: int,
    allocation: int,
) -> AiCreditsStatus:
    return AiCreditsStatus(
        userId="user-1",
        tier=tier,
        balance=balance,
        allocation=allocation,
        periodStartAt=datetime(2026, 4, 1, tzinfo=timezone.utc),
        periodEndAt=datetime(2026, 5, 1, tzinfo=timezone.utc),
        costs=CreditCosts(chat=1, textMeal=1, photo=5),
        renewalAnchorSource="free_cycle_start",
    )


def test_access_state_requires_authentication() -> None:
    response = client.get("/api/v1/billing/access-state")

    assert response.status_code == 401
    assert response.json() == {"detail": "Authentication required"}


def test_access_state_returns_backend_owned_free_contract(
    mocker: MockerFixture,
    auth_headers: AuthHeaders,
) -> None:
    get_credits_status = mocker.patch(
        "app.services.access_state_service.ai_credits_service.get_credits_status",
        return_value=_credits_status(tier="free", balance=4, allocation=100),
    )
    mocker.patch(
        "app.services.access_state_service.utc_now",
        return_value=datetime(2026, 4, 30, 10, 0, tzinfo=timezone.utc),
    )

    response = client.get("/api/v1/billing/access-state", headers=auth_headers("user-1"))

    assert response.status_code == 200
    assert response.json() == {
        "tier": "free",
        "entitlementStatus": "inactive",
        "credits": {
            "userId": "user-1",
            "tier": "free",
            "balance": 4,
            "allocation": 100,
            "periodStartAt": "2026-04-01T00:00:00Z",
            "periodEndAt": "2026-05-01T00:00:00Z",
            "costs": {"chat": 1, "textMeal": 1, "photo": 5},
            "renewalAnchorSource": "free_cycle_start",
            "revenueCatEntitlementId": None,
            "revenueCatExpirationAt": None,
            "lastRevenueCatEventId": None,
        },
        "features": {
            "aiChat": {
                "enabled": True,
                "status": "enabled",
                "reason": None,
                "requiredCredits": 1,
                "remainingCredits": 3,
            },
            "photoAnalysis": {
                "enabled": False,
                "status": "disabled",
                "reason": "insufficient_credits",
                "requiredCredits": 5,
                "remainingCredits": 0,
            },
            "textMealAnalysis": {
                "enabled": True,
                "status": "enabled",
                "reason": None,
                "requiredCredits": 1,
                "remainingCredits": 3,
            },
            "weeklyReport": {
                "enabled": False,
                "status": "disabled",
                "reason": "requires_premium",
                "requiredCredits": None,
                "remainingCredits": None,
            },
            "fullHistory": {
                "enabled": False,
                "status": "disabled",
                "reason": "requires_premium",
                "requiredCredits": None,
                "remainingCredits": None,
            },
            "cloudBackup": {
                "enabled": False,
                "status": "disabled",
                "reason": "requires_premium",
                "requiredCredits": None,
                "remainingCredits": None,
            },
        },
        "refreshedAt": "2026-04-30T10:00:00Z",
    }
    get_credits_status.assert_called_once_with("user-1")


def test_access_state_alias_returns_same_contract(
    mocker: MockerFixture,
    auth_headers: AuthHeaders,
) -> None:
    mocker.patch(
        "app.services.access_state_service.ai_credits_service.get_credits_status",
        return_value=_credits_status(tier="premium", balance=800, allocation=800),
    )

    response = client.get("/api/v1/me/access", headers=auth_headers("user-1"))

    assert response.status_code == 200
    assert response.json()["tier"] == "premium"
    assert response.json()["entitlementStatus"] == "active"
    assert response.json()["features"]["weeklyReport"]["enabled"] is True
    assert response.json()["features"]["fullHistory"]["enabled"] is True
    assert response.json()["features"]["cloudBackup"]["enabled"] is True


def test_access_state_returns_explicit_degraded_state_on_firestore_error(
    mocker: MockerFixture,
    auth_headers: AuthHeaders,
) -> None:
    mocker.patch(
        "app.services.access_state_service.ai_credits_service.get_credits_status",
        side_effect=FirestoreServiceError("boom"),
    )
    mocker.patch(
        "app.services.access_state_service.utc_now",
        return_value=datetime(2026, 4, 30, 10, 0, tzinfo=timezone.utc),
    )

    response = client.get("/api/v1/billing/access-state", headers=auth_headers("user-1"))

    assert response.status_code == 200
    payload = response.json()
    assert payload["tier"] == "unknown"
    assert payload["entitlementStatus"] == "degraded"
    assert payload["credits"] is None
    assert payload["features"]["aiChat"] == {
        "enabled": False,
        "status": "unknown",
        "reason": "degraded",
        "requiredCredits": None,
        "remainingCredits": None,
    }
