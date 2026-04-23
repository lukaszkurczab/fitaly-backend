"""Integration tests for GET /ai/credits endpoint."""
from tests.types import AuthHeaders

from datetime import datetime, timezone
from typing import Literal

from fastapi.testclient import TestClient
from pytest_mock import MockerFixture

from app.core.exceptions import FirestoreServiceError
from app.main import app
from app.schemas.ai_credits import AiCreditTransactionItem, AiCreditsStatus, CreditCosts

client = TestClient(app)


def _credits_status(
    *,
    user_id: str,
    tier: Literal["free", "premium"],
    balance: int,
    allocation: int,
    period_start_at: datetime,
    period_end_at: datetime,
) -> AiCreditsStatus:
    return AiCreditsStatus(
        userId=user_id,
        tier=tier,
        balance=balance,
        allocation=allocation,
        periodStartAt=period_start_at,
        periodEndAt=period_end_at,
        costs=CreditCosts(chat=1, textMeal=1, photo=5),
        renewalAnchorSource="free_cycle_start",
    )


def test_get_ai_credits_requires_authentication() -> None:
    response = client.get("/api/v1/ai/credits")

    assert response.status_code == 401
    assert response.json() == {"detail": "Authentication required"}


def test_get_ai_credits_returns_backend_truth(mocker: MockerFixture, auth_headers: AuthHeaders) -> None:
    mocker.patch(
        "app.api.routes.ai_credits.ai_credits_service.get_credits_status",
        return_value=_credits_status(
            user_id="abc",
            tier="free",
            balance=91,
            allocation=100,
            period_start_at=datetime(2026, 3, 23, tzinfo=timezone.utc),
            period_end_at=datetime(2026, 4, 23, tzinfo=timezone.utc),
        ),
    )

    response = client.get("/api/v1/ai/credits", headers=auth_headers("abc"))

    assert response.status_code == 200
    assert response.json() == {
        "userId": "abc",
        "tier": "free",
        "balance": 91,
        "allocation": 100,
        "periodStartAt": "2026-03-23T00:00:00Z",
        "periodEndAt": "2026-04-23T00:00:00Z",
        "costs": {"chat": 1, "textMeal": 1, "photo": 5},
        "renewalAnchorSource": "free_cycle_start",
        "revenueCatEntitlementId": None,
        "revenueCatExpirationAt": None,
        "lastRevenueCatEventId": None,
    }


def test_get_ai_credits_uses_uid_from_token(mocker: MockerFixture, auth_headers: AuthHeaders) -> None:
    get_credits_status = mocker.patch(
        "app.api.routes.ai_credits.ai_credits_service.get_credits_status",
        return_value=_credits_status(
            user_id="other-user",
            tier="premium",
            balance=700,
            allocation=800,
            period_start_at=datetime(2026, 4, 14, tzinfo=timezone.utc),
            period_end_at=datetime(2026, 5, 14, tzinfo=timezone.utc),
        ),
    )

    response = client.get("/api/v1/ai/credits", headers=auth_headers("other-user"))

    assert response.status_code == 200
    get_credits_status.assert_called_once_with("other-user")


def test_get_ai_credits_returns_500_for_firestore_errors(
    mocker: MockerFixture,
    auth_headers: AuthHeaders,
) -> None:
    mocker.patch(
        "app.api.routes.ai_credits.ai_credits_service.get_credits_status",
        side_effect=FirestoreServiceError("boom"),
    )

    response = client.get("/api/v1/ai/credits", headers=auth_headers("abc"))

    assert response.status_code == 500
    assert response.json() == {"detail": "Database error"}


def test_get_ai_credit_transactions_requires_authentication() -> None:
    response = client.get("/api/v1/ai/credits/transactions")

    assert response.status_code == 401
    assert response.json() == {"detail": "Authentication required"}


def test_get_ai_credit_transactions_returns_ledger_items(
    mocker: MockerFixture,
    auth_headers: AuthHeaders,
) -> None:
    mocker.patch(
        "app.api.routes.ai_credits.ai_credits_service.list_credit_transactions",
        return_value=[
            AiCreditTransactionItem(
                id="tx-2",
                type="deduct",
                action="photo_analysis",
                cost=5,
                balanceBefore=50,
                balanceAfter=45,
                tier="premium",
                periodStartAt=datetime(2026, 3, 1, tzinfo=timezone.utc),
                periodEndAt=datetime(2026, 4, 1, tzinfo=timezone.utc),
                createdAt=datetime(2026, 3, 25, tzinfo=timezone.utc),
            ),
            AiCreditTransactionItem(
                id="tx-1",
                type="refund",
                action="photo_analysis_failure_refund",
                cost=5,
                balanceBefore=45,
                balanceAfter=50,
                tier="premium",
                periodStartAt=datetime(2026, 3, 1, tzinfo=timezone.utc),
                periodEndAt=datetime(2026, 4, 1, tzinfo=timezone.utc),
                createdAt=datetime(2026, 3, 24, tzinfo=timezone.utc),
            ),
        ],
    )

    response = client.get(
        "/api/v1/ai/credits/transactions?limit=2",
        headers=auth_headers("abc"),
    )

    assert response.status_code == 200
    assert response.json() == {
        "items": [
            {
                "id": "tx-2",
                "type": "deduct",
                "action": "photo_analysis",
                "cost": 5,
                "balanceBefore": 50,
                "balanceAfter": 45,
                "tier": "premium",
                "periodStartAt": "2026-03-01T00:00:00Z",
                "periodEndAt": "2026-04-01T00:00:00Z",
                "createdAt": "2026-03-25T00:00:00Z",
            },
            {
                "id": "tx-1",
                "type": "refund",
                "action": "photo_analysis_failure_refund",
                "cost": 5,
                "balanceBefore": 45,
                "balanceAfter": 50,
                "tier": "premium",
                "periodStartAt": "2026-03-01T00:00:00Z",
                "periodEndAt": "2026-04-01T00:00:00Z",
                "createdAt": "2026-03-24T00:00:00Z",
            },
        ]
    }
