"""Integration tests for legacy v1 AI analysis routes and credits behavior."""
from tests.types import AuthHeaders

import asyncio
from collections import deque
from datetime import datetime, timezone
from time import monotonic
from typing import Literal
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient
from pytest_mock import MockerFixture

from app.core.errors import ConsentRequiredError
from app.core.exceptions import (
    AiCreditsExhaustedError,
    FirestoreServiceError,
    OpenAIServiceError,
)
import app.services.ai_gateway_service as _gw
from app.main import app
from app.schemas.ai_credits import AiCreditsStatus, CreditCosts
from app.services.ai_credits_service import IdempotentCreditResult

client = TestClient(app)

_MISSING_AI_CONSENT = object()


def _profile_with_ai_consent(ai_consent: object = _MISSING_AI_CONSENT) -> dict[str, object]:
    profile: dict[str, object] = {}
    if ai_consent is not _MISSING_AI_CONSENT:
        profile["aiConsent"] = ai_consent
    return {"uid": "abc", "profile": profile}


@pytest.fixture(autouse=True)
def _mock_active_ai_consent(mocker: MockerFixture) -> AsyncMock:
    return mocker.patch(
        "app.domain.users.services.user_profile_service.user_account_service.get_user_profile_data",
        return_value=_profile_with_ai_consent(
            {
                "status": "granted",
                "grantedAt": "2026-04-01T10:00:00+00:00",
                "revokedAt": None,
            }
        ),
    )


@pytest.fixture(autouse=True)
def _mock_rate_limit(mocker: MockerFixture) -> None:
    """Replace Firestore-backed rate limit with deterministic in-memory state."""
    buckets: dict[str, deque[float]] = {}

    async def _in_memory_slot(user_id: str) -> bool:
        now = monotonic()
        bucket = buckets.setdefault(user_id, deque())
        while bucket and now - bucket[0] >= _gw.RATE_LIMIT_WINDOW_SECONDS:
            bucket.popleft()
        if len(bucket) >= _gw.RATE_LIMIT_MAX_REQUESTS:
            return False
        bucket.append(now)
        return True

    mocker.patch(
        "app.services.ai_gateway_service._consume_rate_limit_slot",
        side_effect=_in_memory_slot,
    )


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
        renewalAnchorSource="rolling_monthly",
    )


def _idempotent_credit_result(
    *,
    user_id: str,
    tier: Literal["free", "premium"],
    balance: int,
    allocation: int,
    period_start_at: datetime,
    period_end_at: datetime,
    applied: bool = True,
    refunded: bool = False,
) -> IdempotentCreditResult:
    return IdempotentCreditResult(
        status=_credits_status(
            user_id=user_id,
            tier=tier,
            balance=balance,
            allocation=allocation,
            period_start_at=period_start_at,
            period_end_at=period_end_at,
        ),
        applied=applied,
        refunded=refunded,
    )


def _openai_timeout_error(message: str) -> OpenAIServiceError:
    try:
        raise asyncio.TimeoutError("provider request timed out")
    except asyncio.TimeoutError as exc:
        try:
            raise OpenAIServiceError(message) from exc
        except OpenAIServiceError as wrapped:
            return wrapped


@pytest.mark.parametrize(
    "ai_consent",
    [
        pytest.param(_MISSING_AI_CONSENT, id="missing"),
        pytest.param(
            {"status": "not_granted", "grantedAt": None, "revokedAt": None},
            id="not_granted",
        ),
        pytest.param(
            {
                "status": "revoked",
                "grantedAt": "2026-04-01T10:00:00+00:00",
                "revokedAt": "2026-04-02T10:00:00+00:00",
            },
            id="revoked",
        ),
        pytest.param(
            {"status": "granted", "grantedAt": None, "revokedAt": None},
            id="granted_missing_granted_at",
        ),
        pytest.param(
            {
                "status": "granted",
                "grantedAt": "2026-04-01T10:00:00+00:00",
                "revokedAt": "2026-04-02T10:00:00+00:00",
            },
            id="granted_with_revoked_at",
        ),
    ],
)
def test_post_ai_photo_analyze_requires_active_ai_consent_before_cost_work(
    mocker: MockerFixture,
    auth_headers: AuthHeaders,
    _mock_active_ai_consent: AsyncMock,
    ai_consent: object,
) -> None:
    _mock_active_ai_consent.return_value = _profile_with_ai_consent(ai_consent)
    evaluate_request = mocker.patch("app.api.routes.ai.ai_gateway_service.evaluate_request")
    deduct_credits = mocker.patch(
        "app.api.routes.ai.ai_credits_service.deduct_credits_idempotent"
    )
    refund_credits = mocker.patch(
        "app.api.routes.ai.ai_credits_service.refund_credits_idempotent"
    )
    complete_credits = mocker.patch(
        "app.api.routes.ai.ai_credits_service.complete_credits_idempotent"
    )
    analyze_photo = mocker.patch("app.api.routes.ai._execute_photo_completion")
    log_gateway_decision = mocker.patch(
        "app.api.routes.ai.ai_gateway_logger.log_gateway_decision"
    )

    response = client.post(
        "/api/v1/ai/photo/analyze",
        json={"imageBase64": "base64-image"},
        headers=auth_headers("abc"),
    )

    assert response.status_code == 403
    assert response.json() == {
        "detail": {
            "code": ConsentRequiredError.code,
            "message": "AI health data consent required.",
            "aiConsent": {
                "required": True,
                "scope": "global_ai_health_data",
            },
        }
    }
    detail = response.json()["detail"]
    assert "readiness" not in detail
    assert "needs_ai_consent" not in str(detail)
    evaluate_request.assert_not_called()
    deduct_credits.assert_not_called()
    refund_credits.assert_not_called()
    complete_credits.assert_not_called()
    analyze_photo.assert_not_called()
    log_gateway_decision.assert_not_called()


@pytest.mark.parametrize(
    "ai_consent",
    [
        pytest.param(_MISSING_AI_CONSENT, id="missing"),
        pytest.param(
            {"status": "not_granted", "grantedAt": None, "revokedAt": None},
            id="not_granted",
        ),
        pytest.param(
            {
                "status": "revoked",
                "grantedAt": "2026-04-01T10:00:00+00:00",
                "revokedAt": "2026-04-02T10:00:00+00:00",
            },
            id="revoked",
        ),
        pytest.param(
            {"status": "granted", "grantedAt": None, "revokedAt": None},
            id="granted_missing_granted_at",
        ),
        pytest.param(
            {
                "status": "granted",
                "grantedAt": "2026-04-01T10:00:00+00:00",
                "revokedAt": "2026-04-02T10:00:00+00:00",
            },
            id="granted_with_revoked_at",
        ),
    ],
)
def test_post_ai_text_meal_analyze_requires_active_ai_consent_before_cost_work(
    mocker: MockerFixture,
    auth_headers: AuthHeaders,
    _mock_active_ai_consent: AsyncMock,
    ai_consent: object,
) -> None:
    _mock_active_ai_consent.return_value = _profile_with_ai_consent(ai_consent)
    evaluate_request = mocker.patch("app.api.routes.ai.ai_gateway_service.evaluate_request")
    deduct_credits = mocker.patch(
        "app.api.routes.ai.ai_credits_service.deduct_credits_idempotent"
    )
    refund_credits = mocker.patch(
        "app.api.routes.ai.ai_credits_service.refund_credits_idempotent"
    )
    complete_credits = mocker.patch(
        "app.api.routes.ai.ai_credits_service.complete_credits_idempotent"
    )
    analyze_text_meal = mocker.patch("app.api.routes.ai._execute_text_meal_completion")
    log_gateway_decision = mocker.patch(
        "app.api.routes.ai.ai_gateway_logger.log_gateway_decision"
    )

    response = client.post(
        "/api/v1/ai/text-meal/analyze",
        json={"payload": {"name": "owsianka"}},
        headers=auth_headers("abc"),
    )

    assert response.status_code == 403
    assert response.json() == {
        "detail": {
            "code": ConsentRequiredError.code,
            "message": "AI health data consent required.",
            "aiConsent": {
                "required": True,
                "scope": "global_ai_health_data",
            },
        }
    }
    detail = response.json()["detail"]
    assert "readiness" not in detail
    assert "needs_ai_consent" not in str(detail)
    evaluate_request.assert_not_called()
    deduct_credits.assert_not_called()
    refund_credits.assert_not_called()
    complete_credits.assert_not_called()
    analyze_text_meal.assert_not_called()
    log_gateway_decision.assert_not_called()


@pytest.mark.parametrize(
    ("endpoint", "payload", "completion_patch_path"),
    [
        pytest.param(
            "/api/v1/ai/photo/analyze",
            {"imageBase64": "base64-image"},
            "app.api.routes.ai._execute_photo_completion",
            id="photo",
        ),
        pytest.param(
            "/api/v1/ai/text-meal/analyze",
            {"payload": {"name": "owsianka"}},
            "app.api.routes.ai._execute_text_meal_completion",
            id="text",
        ),
    ],
)
def test_post_ai_meal_analysis_consent_profile_firestore_failure_stops_before_cost_work(
    mocker: MockerFixture,
    auth_headers: AuthHeaders,
    _mock_active_ai_consent: AsyncMock,
    endpoint: str,
    payload: dict[str, object],
    completion_patch_path: str,
) -> None:
    _mock_active_ai_consent.side_effect = FirestoreServiceError("boom")
    evaluate_request = mocker.patch("app.api.routes.ai.ai_gateway_service.evaluate_request")
    get_credits_status = mocker.patch(
        "app.api.routes.ai.ai_credits_service.get_credits_status"
    )
    deduct_credits = mocker.patch(
        "app.api.routes.ai.ai_credits_service.deduct_credits_idempotent"
    )
    refund_credits = mocker.patch(
        "app.api.routes.ai.ai_credits_service.refund_credits_idempotent"
    )
    complete_credits = mocker.patch(
        "app.api.routes.ai.ai_credits_service.complete_credits_idempotent"
    )
    analyze_meal = mocker.patch(completion_patch_path)
    log_gateway_decision = mocker.patch(
        "app.api.routes.ai.ai_gateway_logger.log_gateway_decision"
    )

    response = client.post(
        endpoint,
        json=payload,
        headers=auth_headers("abc"),
    )

    assert response.status_code == 500
    assert response.json() == {"detail": "Database error"}
    evaluate_request.assert_not_called()
    get_credits_status.assert_not_called()
    deduct_credits.assert_not_called()
    refund_credits.assert_not_called()
    complete_credits.assert_not_called()
    analyze_meal.assert_not_called()
    log_gateway_decision.assert_not_called()


def test_post_ai_photo_analyze_disabled_returns_503_without_cost_work(
    mocker: MockerFixture,
    auth_headers: AuthHeaders,
) -> None:
    mocker.patch("app.api.routes.ai.settings.AI_MEAL_ANALYSIS_ENABLED", False)
    evaluate_request = mocker.patch("app.api.routes.ai.ai_gateway_service.evaluate_request")
    get_credits_status = mocker.patch(
        "app.api.routes.ai.ai_credits_service.get_credits_status"
    )
    deduct_credits = mocker.patch(
        "app.api.routes.ai.ai_credits_service.deduct_credits_idempotent"
    )
    refund_credits = mocker.patch(
        "app.api.routes.ai.ai_credits_service.refund_credits_idempotent"
    )
    complete_credits = mocker.patch(
        "app.api.routes.ai.ai_credits_service.complete_credits_idempotent"
    )
    analyze_photo = mocker.patch("app.api.routes.ai._execute_photo_completion")
    log_gateway_decision = mocker.patch(
        "app.api.routes.ai.ai_gateway_logger.log_gateway_decision"
    )

    response = client.post(
        "/api/v1/ai/photo/analyze",
        json={"imageBase64": "base64-image"},
        headers=auth_headers("abc"),
    )

    assert response.status_code == 503
    assert response.json() == {
        "detail": {
            "code": "AI_MEAL_ANALYSIS_DISABLED",
            "message": "Meal analysis AI is temporarily disabled.",
        }
    }
    evaluate_request.assert_not_called()
    get_credits_status.assert_not_called()
    deduct_credits.assert_not_called()
    refund_credits.assert_not_called()
    complete_credits.assert_not_called()
    analyze_photo.assert_not_called()
    log_gateway_decision.assert_not_called()


def test_post_ai_text_meal_analyze_disabled_returns_503_without_cost_work(
    mocker: MockerFixture,
    auth_headers: AuthHeaders,
) -> None:
    mocker.patch("app.api.routes.ai.settings.AI_MEAL_ANALYSIS_ENABLED", False)
    evaluate_request = mocker.patch("app.api.routes.ai.ai_gateway_service.evaluate_request")
    get_credits_status = mocker.patch(
        "app.api.routes.ai.ai_credits_service.get_credits_status"
    )
    deduct_credits = mocker.patch(
        "app.api.routes.ai.ai_credits_service.deduct_credits_idempotent"
    )
    refund_credits = mocker.patch(
        "app.api.routes.ai.ai_credits_service.refund_credits_idempotent"
    )
    complete_credits = mocker.patch(
        "app.api.routes.ai.ai_credits_service.complete_credits_idempotent"
    )
    analyze_text_meal = mocker.patch("app.api.routes.ai._execute_text_meal_completion")
    log_gateway_decision = mocker.patch(
        "app.api.routes.ai.ai_gateway_logger.log_gateway_decision"
    )

    response = client.post(
        "/api/v1/ai/text-meal/analyze",
        json={"payload": {"name": "owsianka"}},
        headers=auth_headers("abc"),
    )

    assert response.status_code == 503
    assert response.json() == {
        "detail": {
            "code": "AI_MEAL_ANALYSIS_DISABLED",
            "message": "Meal analysis AI is temporarily disabled.",
        }
    }
    evaluate_request.assert_not_called()
    get_credits_status.assert_not_called()
    deduct_credits.assert_not_called()
    refund_credits.assert_not_called()
    complete_credits.assert_not_called()
    analyze_text_meal.assert_not_called()
    log_gateway_decision.assert_not_called()


@pytest.mark.parametrize(
    ("endpoint", "payload", "completion_patch_path"),
    [
        pytest.param(
            "/api/v1/ai/photo/analyze",
            {"imageBase64": "base64-image"},
            "app.api.routes.ai._execute_photo_completion",
            id="photo",
        ),
        pytest.param(
            "/api/v1/ai/text-meal/analyze",
            {"payload": {"name": "owsianka"}},
            "app.api.routes.ai._execute_text_meal_completion",
            id="text",
        ),
    ],
)
def test_post_ai_meal_analysis_disabled_preserves_consent_order(
    mocker: MockerFixture,
    auth_headers: AuthHeaders,
    _mock_active_ai_consent: AsyncMock,
    endpoint: str,
    payload: dict[str, object],
    completion_patch_path: str,
) -> None:
    mocker.patch("app.api.routes.ai.settings.AI_MEAL_ANALYSIS_ENABLED", False)
    _mock_active_ai_consent.return_value = _profile_with_ai_consent(
        {
            "status": "revoked",
            "grantedAt": "2026-04-01T10:00:00+00:00",
            "revokedAt": "2026-04-02T10:00:00+00:00",
        }
    )
    evaluate_request = mocker.patch("app.api.routes.ai.ai_gateway_service.evaluate_request")
    get_credits_status = mocker.patch(
        "app.api.routes.ai.ai_credits_service.get_credits_status"
    )
    deduct_credits = mocker.patch(
        "app.api.routes.ai.ai_credits_service.deduct_credits_idempotent"
    )
    refund_credits = mocker.patch(
        "app.api.routes.ai.ai_credits_service.refund_credits_idempotent"
    )
    complete_credits = mocker.patch(
        "app.api.routes.ai.ai_credits_service.complete_credits_idempotent"
    )
    analyze_meal = mocker.patch(completion_patch_path)
    log_gateway_decision = mocker.patch(
        "app.api.routes.ai.ai_gateway_logger.log_gateway_decision"
    )

    response = client.post(
        endpoint,
        json=payload,
        headers=auth_headers("abc"),
    )

    assert response.status_code == 403
    assert response.json()["detail"]["code"] == ConsentRequiredError.code
    evaluate_request.assert_not_called()
    get_credits_status.assert_not_called()
    deduct_credits.assert_not_called()
    refund_credits.assert_not_called()
    complete_credits.assert_not_called()
    analyze_meal.assert_not_called()
    log_gateway_decision.assert_not_called()


def test_post_ai_photo_analyze_returns_413_for_payload_guard(
    mocker: MockerFixture,
    auth_headers: AuthHeaders,
) -> None:
    log_gateway_decision = mocker.patch(
        "app.api.routes.ai.ai_gateway_logger.log_gateway_decision"
    )
    mocker.patch(
        "app.api.routes.ai.ai_credits_service.get_credits_status",
        return_value=_credits_status(
            user_id="abc",
            tier="free",
            balance=100,
            allocation=100,
            period_start_at=datetime(2026, 3, 23, tzinfo=timezone.utc),
            period_end_at=datetime(2026, 4, 23, tzinfo=timezone.utc),
        ),
    )
    mocker.patch("app.api.routes.ai.ai_gateway_service.MAX_PHOTO_PAYLOAD_CHARS", 5)

    response = client.post(
        "/api/v1/ai/photo/analyze",
        json={"imageBase64": "base64-image"},
        headers=auth_headers("abc"),
    )

    assert response.status_code == 413
    assert response.json()["detail"]["code"] == "AI_GATEWAY_PAYLOAD_TOO_LARGE"
    assert log_gateway_decision.call_args.args[2]["outcome"] == "REJECTED"


def test_post_ai_photo_analyze_deducts_five_credits(
    mocker: MockerFixture,
    auth_headers: AuthHeaders,
) -> None:
    log_gateway_decision = mocker.patch("app.api.routes.ai.ai_gateway_logger.log_gateway_decision")
    deduct_credits = mocker.patch(
        "app.api.routes.ai.ai_credits_service.deduct_credits_idempotent",
        return_value=_idempotent_credit_result(
            user_id="abc",
            tier="free",
            balance=95,
            allocation=100,
            period_start_at=datetime(2026, 3, 23, tzinfo=timezone.utc),
            period_end_at=datetime(2026, 4, 23, tzinfo=timezone.utc),
        ),
    )
    complete_credits = mocker.patch(
        "app.api.routes.ai.ai_credits_service.complete_credits_idempotent",
        return_value=_idempotent_credit_result(
            user_id="abc",
            tier="free",
            balance=95,
            allocation=100,
            period_start_at=datetime(2026, 3, 23, tzinfo=timezone.utc),
            period_end_at=datetime(2026, 4, 23, tzinfo=timezone.utc),
        ),
    )
    plain_deduct_credits = mocker.patch("app.api.routes.ai.ai_credits_service.deduct_credits")
    plain_refund_credits = mocker.patch("app.api.routes.ai.ai_credits_service.refund_credits")
    mocker.patch(
        "app.api.routes.ai._execute_photo_completion",
        return_value=(
            [
                {
                    "name": "Owsianka",
                    "amount": 120,
                    "protein": 6,
                    "fat": 4,
                    "carbs": 20,
                    "kcal": 148,
                }
            ],
            145,
        ),
    )

    response = client.post(
        "/api/v1/ai/photo/analyze",
        json={"imageBase64": "base64-image"},
        headers={**auth_headers("abc"), "X-Idempotency-Key": "photo-op-1"},
    )

    assert response.status_code == 200
    assert response.json()["balance"] == 95
    assert response.json()["costs"] == {"chat": 1, "textMeal": 1, "photo": 5}
    assert response.json()["model"] == "gpt-4o"
    assert response.json()["runId"] == "photo-op-1"
    assert response.json()["warnings"] == []
    expected_key = "ai-credit:abc:photo_analysis:photo-op-1"
    deduct_credits.assert_called_once_with(
        "abc",
        cost=5,
        action="photo_analysis",
        idempotency_key=expected_key,
    )
    complete_credits.assert_called_once_with("abc", idempotency_key=expected_key)
    plain_deduct_credits.assert_not_called()
    plain_refund_credits.assert_not_called()
    log_gateway_decision.assert_called_once()
    gateway_result = log_gateway_decision.call_args.args[2]
    assert gateway_result["task_type"] == "photo_meal_analysis"
    assert gateway_result["outcome"] == "FORWARDED"
    assert gateway_result["request_id"] == "photo-op-1"


def test_post_ai_photo_analyze_uses_safe_gateway_message(
    mocker: MockerFixture,
    auth_headers: AuthHeaders,
) -> None:
    raw_image_base64 = "  distinctive-raw-base64-payload==  "
    stripped_length = len(raw_image_base64.strip())
    safe_gateway_message = f"[photo-bytes:{stripped_length}]"

    def _evaluate_gateway_request(*_: object, request_id: str, **__: object) -> dict[str, object]:
        return {
            "decision": "FORWARD",
            "reason": "TEST_FORWARD",
            "score": 0.0,
            "request_id": request_id,
        }

    evaluate_request = mocker.patch(
        "app.api.routes.ai.ai_gateway_service.evaluate_request",
        side_effect=_evaluate_gateway_request,
    )
    log_gateway_decision = mocker.patch(
        "app.api.routes.ai.ai_gateway_logger.log_gateway_decision"
    )
    deduct_credits = mocker.patch(
        "app.api.routes.ai.ai_credits_service.deduct_credits_idempotent",
        return_value=_idempotent_credit_result(
            user_id="abc",
            tier="free",
            balance=95,
            allocation=100,
            period_start_at=datetime(2026, 3, 23, tzinfo=timezone.utc),
            period_end_at=datetime(2026, 4, 23, tzinfo=timezone.utc),
        ),
    )
    complete_credits = mocker.patch(
        "app.api.routes.ai.ai_credits_service.complete_credits_idempotent",
        return_value=_idempotent_credit_result(
            user_id="abc",
            tier="free",
            balance=95,
            allocation=100,
            period_start_at=datetime(2026, 3, 23, tzinfo=timezone.utc),
            period_end_at=datetime(2026, 4, 23, tzinfo=timezone.utc),
        ),
    )
    mocker.patch(
        "app.api.routes.ai._execute_photo_completion",
        return_value=(
            [
                {
                    "name": "Owsianka",
                    "amount": 120,
                    "protein": 6,
                    "fat": 4,
                    "carbs": 20,
                    "kcal": 148,
                }
            ],
            145,
        ),
    )

    response = client.post(
        "/api/v1/ai/photo/analyze",
        json={"imageBase64": raw_image_base64},
        headers=auth_headers("abc"),
    )

    assert response.status_code == 200
    evaluate_request.assert_called_once()
    fallback_operation_id = evaluate_request.call_args.kwargs["request_id"]
    assert response.json()["runId"] == fallback_operation_id
    assert evaluate_request.call_args.args[:3] == (
        "abc",
        "photo_analysis",
        safe_gateway_message,
    )
    assert evaluate_request.call_args.kwargs["raw_payload_chars"] == stripped_length
    assert raw_image_base64.strip() not in evaluate_request.call_args.args
    expected_key = f"ai-credit:abc:photo_analysis:{fallback_operation_id}"
    deduct_credits.assert_called_once_with(
        "abc",
        cost=5,
        action="photo_analysis",
        idempotency_key=expected_key,
    )
    complete_credits.assert_called_once_with("abc", idempotency_key=expected_key)

    log_gateway_decision.assert_called_once()
    assert log_gateway_decision.call_args.args[1] == safe_gateway_message
    assert raw_image_base64.strip() not in log_gateway_decision.call_args.args


def test_post_ai_text_meal_analyze_deducts_one_credit(
    mocker: MockerFixture,
    auth_headers: AuthHeaders,
) -> None:
    log_gateway_decision = mocker.patch("app.api.routes.ai.ai_gateway_logger.log_gateway_decision")
    deduct_credits = mocker.patch(
        "app.api.routes.ai.ai_credits_service.deduct_credits_idempotent",
        return_value=_idempotent_credit_result(
            user_id="abc",
            tier="premium",
            balance=799,
            allocation=800,
            period_start_at=datetime(2026, 4, 14, tzinfo=timezone.utc),
            period_end_at=datetime(2026, 5, 14, tzinfo=timezone.utc),
        ),
    )
    complete_credits = mocker.patch(
        "app.api.routes.ai.ai_credits_service.complete_credits_idempotent",
        return_value=_idempotent_credit_result(
            user_id="abc",
            tier="premium",
            balance=799,
            allocation=800,
            period_start_at=datetime(2026, 4, 14, tzinfo=timezone.utc),
            period_end_at=datetime(2026, 5, 14, tzinfo=timezone.utc),
        ),
    )
    plain_deduct_credits = mocker.patch("app.api.routes.ai.ai_credits_service.deduct_credits")
    plain_refund_credits = mocker.patch("app.api.routes.ai.ai_credits_service.refund_credits")
    mocker.patch(
        "app.api.routes.ai._execute_text_meal_completion",
        return_value=(
            [
                {
                    "name": "Owsianka",
                    "amount": 120,
                    "protein": 6,
                    "fat": 4,
                    "carbs": 20,
                    "kcal": 148,
                }
            ],
            88,
        ),
    )

    response = client.post(
        "/api/v1/ai/text-meal/analyze",
        json={"payload": {"name": "owsianka"}},
        headers={**auth_headers("abc"), "X-Idempotency-Key": "text-op-1"},
    )

    assert response.status_code == 200
    assert response.json()["balance"] == 799
    assert response.json()["model"] == "gpt-4o-mini"
    assert response.json()["runId"] == "text-op-1"
    assert response.json()["warnings"] == []
    expected_key = "ai-credit:abc:text_meal_analysis:text-op-1"
    deduct_credits.assert_called_once_with(
        "abc",
        cost=1,
        action="text_meal_analysis",
        idempotency_key=expected_key,
    )
    complete_credits.assert_called_once_with("abc", idempotency_key=expected_key)
    plain_deduct_credits.assert_not_called()
    plain_refund_credits.assert_not_called()
    log_gateway_decision.assert_called_once()
    gateway_result = log_gateway_decision.call_args.args[2]
    assert gateway_result["task_type"] == "text_meal_analysis"
    assert gateway_result["outcome"] == "FORWARDED"
    assert gateway_result["request_id"] == "text-op-1"


@pytest.mark.parametrize(
    (
        "endpoint",
        "payload",
        "action",
        "cost",
        "operation_id",
        "completion_patch_path",
    ),
    [
        pytest.param(
            "/api/v1/ai/photo/analyze",
            {"imageBase64": "base64-image"},
            "photo_analysis",
            5,
            "photo-duplicate-op",
            "app.api.routes.ai._execute_photo_completion",
            id="photo",
        ),
        pytest.param(
            "/api/v1/ai/text-meal/analyze",
            {"payload": {"name": "owsianka"}},
            "text_meal_analysis",
            1,
            "text-duplicate-op",
            "app.api.routes.ai._execute_text_meal_completion",
            id="text",
        ),
    ],
)
def test_post_ai_meal_analyze_duplicate_idempotency_returns_409_without_provider(
    mocker: MockerFixture,
    auth_headers: AuthHeaders,
    endpoint: str,
    payload: dict[str, object],
    action: str,
    cost: int,
    operation_id: str,
    completion_patch_path: str,
) -> None:
    mocker.patch(
        "app.api.routes.ai.ai_gateway_service.evaluate_request",
        return_value={"decision": "FORWARD", "reason": "TEST_FORWARD", "score": 0.0},
    )
    deduct_credits = mocker.patch(
        "app.api.routes.ai.ai_credits_service.deduct_credits_idempotent",
        return_value=_idempotent_credit_result(
            user_id="abc",
            tier="free",
            balance=95,
            allocation=100,
            period_start_at=datetime(2026, 3, 23, tzinfo=timezone.utc),
            period_end_at=datetime(2026, 4, 23, tzinfo=timezone.utc),
            applied=False,
        ),
    )
    refund_credits = mocker.patch(
        "app.api.routes.ai.ai_credits_service.refund_credits_idempotent"
    )
    complete_credits = mocker.patch(
        "app.api.routes.ai.ai_credits_service.complete_credits_idempotent"
    )
    analyze_meal = mocker.patch(completion_patch_path)
    log_gateway_decision = mocker.patch(
        "app.api.routes.ai.ai_gateway_logger.log_gateway_decision"
    )
    plain_deduct_credits = mocker.patch("app.api.routes.ai.ai_credits_service.deduct_credits")
    plain_refund_credits = mocker.patch("app.api.routes.ai.ai_credits_service.refund_credits")

    response = client.post(
        endpoint,
        json=payload,
        headers={**auth_headers("abc"), "X-Idempotency-Key": operation_id},
    )

    assert response.status_code == 409
    assert response.json() == {
        "detail": {
            "code": "AI_MEAL_ANALYSIS_IDEMPOTENCY_CONFLICT",
            "message": "Meal analysis request is already in progress or completed.",
        }
    }
    deduct_credits.assert_called_once_with(
        "abc",
        cost=cost,
        action=action,
        idempotency_key=f"ai-credit:abc:{action}:{operation_id}",
    )
    analyze_meal.assert_not_called()
    refund_credits.assert_not_called()
    complete_credits.assert_not_called()
    log_gateway_decision.assert_not_called()
    plain_deduct_credits.assert_not_called()
    plain_refund_credits.assert_not_called()


def test_post_ai_photo_analyze_exhausted_credits_returns_402_without_provider_or_refund(
    mocker: MockerFixture,
    auth_headers: AuthHeaders,
) -> None:
    exhausted_credits = _credits_status(
        user_id="abc",
        tier="free",
        balance=0,
        allocation=100,
        period_start_at=datetime(2026, 3, 23, tzinfo=timezone.utc),
        period_end_at=datetime(2026, 4, 23, tzinfo=timezone.utc),
    )
    mocker.patch(
        "app.api.routes.ai.ai_gateway_service.evaluate_request",
        return_value={"decision": "FORWARD", "reason": "TEST_FORWARD", "score": 0.0},
    )
    deduct_credits = mocker.patch(
        "app.api.routes.ai.ai_credits_service.deduct_credits_idempotent",
        side_effect=AiCreditsExhaustedError("AI credits exhausted."),
    )
    mocker.patch(
        "app.api.routes.ai.ai_credits_service.get_credits_status",
        return_value=exhausted_credits,
    )
    analyze_photo = mocker.patch("app.api.routes.ai._execute_photo_completion")
    refund_credits = mocker.patch("app.api.routes.ai.ai_credits_service.refund_credits_idempotent")
    complete_credits = mocker.patch("app.api.routes.ai.ai_credits_service.complete_credits_idempotent")

    response = client.post(
        "/api/v1/ai/photo/analyze",
        json={"imageBase64": "base64-image"},
        headers={**auth_headers("abc"), "X-Idempotency-Key": "photo-no-credits"},
    )

    assert response.status_code == 402
    assert response.json()["detail"]["code"] == "AI_CREDITS_EXHAUSTED"
    assert response.json()["detail"]["credits"]["balance"] == 0
    assert response.json()["detail"]["credits"]["costs"] == {
        "chat": 1,
        "textMeal": 1,
        "photo": 5,
    }
    deduct_credits.assert_called_once_with(
        "abc",
        cost=5,
        action="photo_analysis",
        idempotency_key="ai-credit:abc:photo_analysis:photo-no-credits",
    )
    analyze_photo.assert_not_called()
    refund_credits.assert_not_called()
    complete_credits.assert_not_called()


def test_post_ai_text_meal_analyze_exhausted_credits_returns_402_without_provider_or_refund(
    mocker: MockerFixture,
    auth_headers: AuthHeaders,
) -> None:
    exhausted_credits = _credits_status(
        user_id="abc",
        tier="premium",
        balance=0,
        allocation=800,
        period_start_at=datetime(2026, 4, 14, tzinfo=timezone.utc),
        period_end_at=datetime(2026, 5, 14, tzinfo=timezone.utc),
    )
    mocker.patch(
        "app.api.routes.ai.ai_gateway_service.evaluate_request",
        return_value={"decision": "FORWARD", "reason": "TEST_FORWARD", "score": 0.0},
    )
    deduct_credits = mocker.patch(
        "app.api.routes.ai.ai_credits_service.deduct_credits_idempotent",
        side_effect=AiCreditsExhaustedError("AI credits exhausted."),
    )
    mocker.patch(
        "app.api.routes.ai.ai_credits_service.get_credits_status",
        return_value=exhausted_credits,
    )
    analyze_text_meal = mocker.patch("app.api.routes.ai._execute_text_meal_completion")
    refund_credits = mocker.patch("app.api.routes.ai.ai_credits_service.refund_credits_idempotent")
    complete_credits = mocker.patch("app.api.routes.ai.ai_credits_service.complete_credits_idempotent")

    response = client.post(
        "/api/v1/ai/text-meal/analyze",
        json={"payload": {"name": "owsianka"}},
        headers={**auth_headers("abc"), "X-Idempotency-Key": "text-no-credits"},
    )

    assert response.status_code == 402
    assert response.json()["detail"]["code"] == "AI_CREDITS_EXHAUSTED"
    assert response.json()["detail"]["credits"]["balance"] == 0
    assert response.json()["detail"]["credits"]["costs"] == {
        "chat": 1,
        "textMeal": 1,
        "photo": 5,
    }
    deduct_credits.assert_called_once_with(
        "abc",
        cost=1,
        action="text_meal_analysis",
        idempotency_key="ai-credit:abc:text_meal_analysis:text-no-credits",
    )
    analyze_text_meal.assert_not_called()
    refund_credits.assert_not_called()
    complete_credits.assert_not_called()


def test_post_ai_text_meal_analyze_bypasses_gateway_when_disabled(
    mocker: MockerFixture,
    auth_headers: AuthHeaders,
) -> None:
    mocker.patch("app.services.ai_gateway_service.settings.AI_GATEWAY_ENABLED", False)
    mocker.patch("app.services.ai_gateway_service.MAX_TEXT_PAYLOAD_CHARS", 1)
    log_gateway_decision = mocker.patch("app.api.routes.ai.ai_gateway_logger.log_gateway_decision")
    deduct_credits = mocker.patch(
        "app.api.routes.ai.ai_credits_service.deduct_credits_idempotent",
        return_value=_idempotent_credit_result(
            user_id="abc",
            tier="premium",
            balance=799,
            allocation=800,
            period_start_at=datetime(2026, 4, 14, tzinfo=timezone.utc),
            period_end_at=datetime(2026, 5, 14, tzinfo=timezone.utc),
        ),
    )
    complete_credits = mocker.patch(
        "app.api.routes.ai.ai_credits_service.complete_credits_idempotent",
        return_value=_idempotent_credit_result(
            user_id="abc",
            tier="premium",
            balance=799,
            allocation=800,
            period_start_at=datetime(2026, 4, 14, tzinfo=timezone.utc),
            period_end_at=datetime(2026, 5, 14, tzinfo=timezone.utc),
        ),
    )
    analyze_text_meal = mocker.patch(
        "app.api.routes.ai._execute_text_meal_completion",
        return_value=(
            [
                {
                    "name": "Owsianka",
                    "amount": 120,
                    "protein": 6,
                    "fat": 4,
                    "carbs": 20,
                    "kcal": 148,
                }
            ],
            88,
        ),
    )

    response = client.post(
        "/api/v1/ai/text-meal/analyze",
        json={"payload": {"name": "owsianka poza limitem bramki"}},
        headers=auth_headers("abc"),
    )

    assert response.status_code == 200
    assert response.json()["balance"] == 799
    deduct_credits.assert_called_once()
    complete_credits.assert_called_once()
    analyze_text_meal.assert_called_once()
    log_gateway_decision.assert_not_called()


def test_post_ai_photo_analyze_respects_gateway_reject(
    mocker: MockerFixture,
    auth_headers: AuthHeaders,
) -> None:
    log_gateway_decision = mocker.patch("app.api.routes.ai.ai_gateway_logger.log_gateway_decision")
    mocker.patch(
        "app.api.routes.ai.ai_credits_service.get_credits_status",
        return_value=_credits_status(
            user_id="abc",
            tier="free",
            balance=100,
            allocation=100,
            period_start_at=datetime(2026, 3, 23, tzinfo=timezone.utc),
            period_end_at=datetime(2026, 4, 23, tzinfo=timezone.utc),
        ),
    )
    mocker.patch(
        "app.api.routes.ai.ai_gateway_service.evaluate_request",
        return_value={
            "decision": "REJECT",
            "reason": "TEST_BLOCK",
            "score": 0.8,
            "credit_cost": 0.0,
        },
    )
    deduct_credits = mocker.patch(
        "app.api.routes.ai.ai_credits_service.deduct_credits_idempotent"
    )
    refund_credits = mocker.patch(
        "app.api.routes.ai.ai_credits_service.refund_credits_idempotent"
    )
    complete_credits = mocker.patch(
        "app.api.routes.ai.ai_credits_service.complete_credits_idempotent"
    )
    analyze_photo = mocker.patch("app.api.routes.ai._execute_photo_completion")

    response = client.post(
        "/api/v1/ai/photo/analyze",
        json={"imageBase64": "base64-image"},
        headers=auth_headers("abc"),
    )

    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "AI_GATEWAY_BLOCKED"
    deduct_credits.assert_not_called()
    refund_credits.assert_not_called()
    complete_credits.assert_not_called()
    analyze_photo.assert_not_called()
    log_gateway_decision.assert_called_once()
    assert log_gateway_decision.call_args.args[2]["outcome"] == "REJECTED"


def test_post_ai_text_meal_analyze_respects_gateway_reject(
    mocker: MockerFixture,
    auth_headers: AuthHeaders,
) -> None:
    log_gateway_decision = mocker.patch("app.api.routes.ai.ai_gateway_logger.log_gateway_decision")
    mocker.patch(
        "app.api.routes.ai.ai_credits_service.get_credits_status",
        return_value=_credits_status(
            user_id="abc",
            tier="premium",
            balance=800,
            allocation=800,
            period_start_at=datetime(2026, 4, 14, tzinfo=timezone.utc),
            period_end_at=datetime(2026, 5, 14, tzinfo=timezone.utc),
        ),
    )
    mocker.patch(
        "app.api.routes.ai.ai_gateway_service.evaluate_request",
        return_value={
            "decision": "REJECT",
            "reason": "TEST_BLOCK",
            "score": 0.7,
            "credit_cost": 0.0,
        },
    )
    deduct_credits = mocker.patch(
        "app.api.routes.ai.ai_credits_service.deduct_credits_idempotent"
    )
    refund_credits = mocker.patch(
        "app.api.routes.ai.ai_credits_service.refund_credits_idempotent"
    )
    complete_credits = mocker.patch(
        "app.api.routes.ai.ai_credits_service.complete_credits_idempotent"
    )
    analyze_text_meal = mocker.patch("app.api.routes.ai._execute_text_meal_completion")

    response = client.post(
        "/api/v1/ai/text-meal/analyze",
        json={"payload": {"name": "owsianka"}},
        headers=auth_headers("abc"),
    )

    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "AI_GATEWAY_BLOCKED"
    deduct_credits.assert_not_called()
    refund_credits.assert_not_called()
    complete_credits.assert_not_called()
    analyze_text_meal.assert_not_called()
    log_gateway_decision.assert_called_once()
    assert log_gateway_decision.call_args.args[2]["outcome"] == "REJECTED"


def test_post_ai_photo_analyze_logs_upstream_failure(
    mocker: MockerFixture,
    auth_headers: AuthHeaders,
) -> None:
    log_gateway_decision = mocker.patch("app.api.routes.ai.ai_gateway_logger.log_gateway_decision")
    deduct_credits = mocker.patch(
        "app.api.routes.ai.ai_credits_service.deduct_credits_idempotent",
        return_value=_idempotent_credit_result(
            user_id="abc",
            tier="free",
            balance=95,
            allocation=100,
            period_start_at=datetime(2026, 3, 23, tzinfo=timezone.utc),
            period_end_at=datetime(2026, 4, 23, tzinfo=timezone.utc),
        ),
    )
    mocker.patch(
        "app.api.routes.ai._execute_photo_completion",
        side_effect=OpenAIServiceError("raw provider stack unavailable"),
    )
    refund_credits = mocker.patch(
        "app.api.routes.ai.ai_credits_service.refund_credits_idempotent",
        return_value=_idempotent_credit_result(
            user_id="abc",
            tier="free",
            balance=100,
            allocation=100,
            period_start_at=datetime(2026, 3, 23, tzinfo=timezone.utc),
            period_end_at=datetime(2026, 4, 23, tzinfo=timezone.utc),
            refunded=True,
        ),
    )
    complete_credits = mocker.patch(
        "app.api.routes.ai.ai_credits_service.complete_credits_idempotent"
    )
    plain_deduct_credits = mocker.patch("app.api.routes.ai.ai_credits_service.deduct_credits")
    plain_refund_credits = mocker.patch("app.api.routes.ai.ai_credits_service.refund_credits")

    response = client.post(
        "/api/v1/ai/photo/analyze",
        json={"imageBase64": "base64-image"},
        headers={**auth_headers("abc"), "X-Idempotency-Key": "photo-provider-failure"},
    )

    assert response.status_code == 503
    assert response.json() == {
        "detail": {
            "code": "AI_CHAT_PROVIDER_UNAVAILABLE",
            "message": "AI provider is temporarily unavailable.",
        }
    }
    assert "raw provider stack unavailable" not in response.text
    log_gateway_decision.assert_called_once()
    gateway_result = log_gateway_decision.call_args.args[2]
    assert gateway_result["outcome"] == "UPSTREAM_ERROR"
    assert gateway_result["failure_reason"] == "OpenAIServiceError"
    expected_key = "ai-credit:abc:photo_analysis:photo-provider-failure"
    deduct_credits.assert_called_once_with(
        "abc",
        cost=5,
        action="photo_analysis",
        idempotency_key=expected_key,
    )
    refund_credits.assert_called_once_with(
        "abc",
        cost=5,
        action="photo_analysis_failure_refund",
        idempotency_key=expected_key,
    )
    complete_credits.assert_not_called()
    plain_deduct_credits.assert_not_called()
    plain_refund_credits.assert_not_called()


def test_post_ai_text_meal_analyze_logs_upstream_failure_and_refunds(
    mocker: MockerFixture,
    auth_headers: AuthHeaders,
) -> None:
    log_gateway_decision = mocker.patch("app.api.routes.ai.ai_gateway_logger.log_gateway_decision")
    deduct_credits = mocker.patch(
        "app.api.routes.ai.ai_credits_service.deduct_credits_idempotent",
        return_value=_idempotent_credit_result(
            user_id="abc",
            tier="premium",
            balance=799,
            allocation=800,
            period_start_at=datetime(2026, 4, 14, tzinfo=timezone.utc),
            period_end_at=datetime(2026, 5, 14, tzinfo=timezone.utc),
        ),
    )
    mocker.patch(
        "app.api.routes.ai._execute_text_meal_completion",
        side_effect=OpenAIServiceError("raw text provider unavailable"),
    )
    refund_credits = mocker.patch(
        "app.api.routes.ai.ai_credits_service.refund_credits_idempotent",
        return_value=_idempotent_credit_result(
            user_id="abc",
            tier="premium",
            balance=800,
            allocation=800,
            period_start_at=datetime(2026, 4, 14, tzinfo=timezone.utc),
            period_end_at=datetime(2026, 5, 14, tzinfo=timezone.utc),
            refunded=True,
        ),
    )
    complete_credits = mocker.patch(
        "app.api.routes.ai.ai_credits_service.complete_credits_idempotent"
    )
    plain_deduct_credits = mocker.patch("app.api.routes.ai.ai_credits_service.deduct_credits")
    plain_refund_credits = mocker.patch("app.api.routes.ai.ai_credits_service.refund_credits")

    response = client.post(
        "/api/v1/ai/text-meal/analyze",
        json={
            "payload": {
                "name": "owsianka",
                "notes": "private preference should not be logged as raw content",
            }
        },
        headers={**auth_headers("abc"), "X-Idempotency-Key": "text-provider-failure"},
    )

    assert response.status_code == 503
    assert response.json() == {
        "detail": {
            "code": "AI_CHAT_PROVIDER_UNAVAILABLE",
            "message": "AI provider is temporarily unavailable.",
        }
    }
    assert "private preference" not in response.text
    assert "raw text provider unavailable" not in response.text
    log_gateway_decision.assert_called_once()
    gateway_result = log_gateway_decision.call_args.args[2]
    assert gateway_result["outcome"] == "UPSTREAM_ERROR"
    assert gateway_result["failure_reason"] == "OpenAIServiceError"
    expected_key = "ai-credit:abc:text_meal_analysis:text-provider-failure"
    deduct_credits.assert_called_once_with(
        "abc",
        cost=1,
        action="text_meal_analysis",
        idempotency_key=expected_key,
    )
    refund_credits.assert_called_once_with(
        "abc",
        cost=1,
        action="text_meal_analysis_failure_refund",
        idempotency_key=expected_key,
    )
    complete_credits.assert_not_called()
    plain_deduct_credits.assert_not_called()
    plain_refund_credits.assert_not_called()


@pytest.mark.parametrize(
    (
        "endpoint",
        "payload",
        "action",
        "cost",
        "operation_id",
        "completion_patch_path",
    ),
    [
        pytest.param(
            "/api/v1/ai/photo/analyze",
            {"imageBase64": "base64-image"},
            "photo_analysis",
            5,
            "photo-refund-failure",
            "app.api.routes.ai._execute_photo_completion",
            id="photo",
        ),
        pytest.param(
            "/api/v1/ai/text-meal/analyze",
            {"payload": {"name": "owsianka"}},
            "text_meal_analysis",
            1,
            "text-refund-failure",
            "app.api.routes.ai._execute_text_meal_completion",
            id="text",
        ),
    ],
)
def test_post_ai_meal_analyze_logs_failed_refund_attempts_after_provider_failure(
    mocker: MockerFixture,
    auth_headers: AuthHeaders,
    endpoint: str,
    payload: dict[str, object],
    action: str,
    cost: int,
    operation_id: str,
    completion_patch_path: str,
) -> None:
    log_gateway_decision = mocker.patch("app.api.routes.ai.ai_gateway_logger.log_gateway_decision")
    deduct_credits = mocker.patch(
        "app.api.routes.ai.ai_credits_service.deduct_credits_idempotent",
        return_value=_idempotent_credit_result(
            user_id="abc",
            tier="free",
            balance=95,
            allocation=100,
            period_start_at=datetime(2026, 3, 23, tzinfo=timezone.utc),
            period_end_at=datetime(2026, 4, 23, tzinfo=timezone.utc),
        ),
    )
    analyze_meal = mocker.patch(
        completion_patch_path,
        side_effect=OpenAIServiceError("raw provider failure should not leak"),
    )
    refund_credits = mocker.patch(
        "app.api.routes.ai.ai_credits_service.refund_credits_idempotent",
        side_effect=FirestoreServiceError("raw refund failure should not leak"),
    )
    sleep = mocker.patch("app.api.routes.ai.asyncio.sleep", new_callable=AsyncMock)
    logger_exception = mocker.patch("app.api.routes.ai.logger.exception")
    complete_credits = mocker.patch(
        "app.api.routes.ai.ai_credits_service.complete_credits_idempotent"
    )
    plain_deduct_credits = mocker.patch("app.api.routes.ai.ai_credits_service.deduct_credits")
    plain_refund_credits = mocker.patch("app.api.routes.ai.ai_credits_service.refund_credits")

    response = client.post(
        endpoint,
        json=payload,
        headers={**auth_headers("abc"), "X-Idempotency-Key": operation_id},
    )

    expected_key = f"ai-credit:abc:{action}:{operation_id}"
    assert response.status_code == 503
    assert response.json() == {
        "detail": {
            "code": "AI_CHAT_PROVIDER_UNAVAILABLE",
            "message": "AI provider is temporarily unavailable.",
        }
    }
    assert "raw provider failure should not leak" not in response.text
    assert "raw refund failure should not leak" not in response.text
    deduct_credits.assert_called_once_with(
        "abc",
        cost=cost,
        action=action,
        idempotency_key=expected_key,
    )
    assert refund_credits.call_count == 3
    for refund_call in refund_credits.call_args_list:
        assert refund_call.args == ("abc",)
        assert refund_call.kwargs == {
            "cost": cost,
            "action": f"{action}_failure_refund",
            "idempotency_key": expected_key,
        }
    sleep.assert_awaited()
    assert sleep.await_count == 2
    assert [call.args for call in sleep.await_args_list] == [(0.5,), (0.5,)]
    logger_exception.assert_called_once_with(
        "Failed to refund AI credits after upstream failure — all retries exhausted. Credits lost.",
        extra={
            "user_id": "abc",
            "endpoint": endpoint.removeprefix("/api/v1"),
            "cost": cost,
            "idempotency_key": expected_key,
            "attempts": 3,
        },
    )
    analyze_meal.assert_called_once()
    log_gateway_decision.assert_called_once()
    assert log_gateway_decision.call_args.args[2]["outcome"] == "UPSTREAM_ERROR"
    complete_credits.assert_not_called()
    plain_deduct_credits.assert_not_called()
    plain_refund_credits.assert_not_called()


def test_post_ai_photo_analyze_provider_timeout_returns_structured_504_and_refunds(
    mocker: MockerFixture,
    auth_headers: AuthHeaders,
) -> None:
    log_gateway_decision = mocker.patch("app.api.routes.ai.ai_gateway_logger.log_gateway_decision")
    deduct_credits = mocker.patch(
        "app.api.routes.ai.ai_credits_service.deduct_credits_idempotent",
        return_value=_idempotent_credit_result(
            user_id="abc",
            tier="free",
            balance=95,
            allocation=100,
            period_start_at=datetime(2026, 3, 23, tzinfo=timezone.utc),
            period_end_at=datetime(2026, 4, 23, tzinfo=timezone.utc),
        ),
    )
    mocker.patch(
        "app.api.routes.ai._execute_photo_completion",
        side_effect=_openai_timeout_error("OpenAI photo analysis timed out."),
    )
    refund_credits = mocker.patch(
        "app.api.routes.ai.ai_credits_service.refund_credits_idempotent",
        return_value=_idempotent_credit_result(
            user_id="abc",
            tier="free",
            balance=100,
            allocation=100,
            period_start_at=datetime(2026, 3, 23, tzinfo=timezone.utc),
            period_end_at=datetime(2026, 4, 23, tzinfo=timezone.utc),
            refunded=True,
        ),
    )
    complete_credits = mocker.patch(
        "app.api.routes.ai.ai_credits_service.complete_credits_idempotent"
    )
    plain_deduct_credits = mocker.patch("app.api.routes.ai.ai_credits_service.deduct_credits")
    plain_refund_credits = mocker.patch("app.api.routes.ai.ai_credits_service.refund_credits")

    response = client.post(
        "/api/v1/ai/photo/analyze",
        json={"imageBase64": "base64-image"},
        headers={**auth_headers("abc"), "X-Idempotency-Key": "photo-provider-timeout"},
    )

    assert response.status_code == 504
    assert response.json() == {
        "detail": {
            "code": "AI_CHAT_TIMEOUT",
            "message": "AI provider timed out before a response was generated.",
        }
    }
    assert "OpenAI photo analysis timed out" not in response.text
    log_gateway_decision.assert_called_once()
    assert log_gateway_decision.call_args.args[2]["outcome"] == "UPSTREAM_ERROR"
    expected_key = "ai-credit:abc:photo_analysis:photo-provider-timeout"
    deduct_credits.assert_called_once_with(
        "abc",
        cost=5,
        action="photo_analysis",
        idempotency_key=expected_key,
    )
    refund_credits.assert_called_once_with(
        "abc",
        cost=5,
        action="photo_analysis_failure_refund",
        idempotency_key=expected_key,
    )
    complete_credits.assert_not_called()
    plain_deduct_credits.assert_not_called()
    plain_refund_credits.assert_not_called()


def test_post_ai_text_meal_analyze_provider_timeout_returns_structured_504_and_refunds(
    mocker: MockerFixture,
    auth_headers: AuthHeaders,
) -> None:
    log_gateway_decision = mocker.patch("app.api.routes.ai.ai_gateway_logger.log_gateway_decision")
    deduct_credits = mocker.patch(
        "app.api.routes.ai.ai_credits_service.deduct_credits_idempotent",
        return_value=_idempotent_credit_result(
            user_id="abc",
            tier="premium",
            balance=799,
            allocation=800,
            period_start_at=datetime(2026, 4, 14, tzinfo=timezone.utc),
            period_end_at=datetime(2026, 5, 14, tzinfo=timezone.utc),
        ),
    )
    mocker.patch(
        "app.api.routes.ai._execute_text_meal_completion",
        side_effect=OpenAIServiceError("OpenAI request timed out."),
    )
    refund_credits = mocker.patch(
        "app.api.routes.ai.ai_credits_service.refund_credits_idempotent",
        return_value=_idempotent_credit_result(
            user_id="abc",
            tier="premium",
            balance=800,
            allocation=800,
            period_start_at=datetime(2026, 4, 14, tzinfo=timezone.utc),
            period_end_at=datetime(2026, 5, 14, tzinfo=timezone.utc),
            refunded=True,
        ),
    )
    complete_credits = mocker.patch(
        "app.api.routes.ai.ai_credits_service.complete_credits_idempotent"
    )
    plain_deduct_credits = mocker.patch("app.api.routes.ai.ai_credits_service.deduct_credits")
    plain_refund_credits = mocker.patch("app.api.routes.ai.ai_credits_service.refund_credits")

    response = client.post(
        "/api/v1/ai/text-meal/analyze",
        json={"payload": {"name": "owsianka"}},
        headers={**auth_headers("abc"), "X-Idempotency-Key": "text-provider-timeout"},
    )

    assert response.status_code == 504
    assert response.json() == {
        "detail": {
            "code": "AI_CHAT_TIMEOUT",
            "message": "AI provider timed out before a response was generated.",
        }
    }
    assert "OpenAI request timed out" not in response.text
    log_gateway_decision.assert_called_once()
    assert log_gateway_decision.call_args.args[2]["outcome"] == "UPSTREAM_ERROR"
    expected_key = "ai-credit:abc:text_meal_analysis:text-provider-timeout"
    deduct_credits.assert_called_once_with(
        "abc",
        cost=1,
        action="text_meal_analysis",
        idempotency_key=expected_key,
    )
    refund_credits.assert_called_once_with(
        "abc",
        cost=1,
        action="text_meal_analysis_failure_refund",
        idempotency_key=expected_key,
    )
    complete_credits.assert_not_called()
    plain_deduct_credits.assert_not_called()
    plain_refund_credits.assert_not_called()


def test_post_ai_photo_malformed_payload_rejects_before_gateway_or_credits(
    mocker: MockerFixture,
    auth_headers: AuthHeaders,
) -> None:
    evaluate_request = mocker.patch("app.api.routes.ai.ai_gateway_service.evaluate_request")
    deduct_credits = mocker.patch(
        "app.api.routes.ai.ai_credits_service.deduct_credits_idempotent"
    )
    analyze_photo = mocker.patch("app.api.routes.ai._execute_photo_completion")
    refund_credits = mocker.patch(
        "app.api.routes.ai.ai_credits_service.refund_credits_idempotent"
    )
    complete_credits = mocker.patch(
        "app.api.routes.ai.ai_credits_service.complete_credits_idempotent"
    )

    response = client.post(
        "/api/v1/ai/photo/analyze",
        json={},
        headers=auth_headers("abc"),
    )

    assert response.status_code == 422
    evaluate_request.assert_not_called()
    deduct_credits.assert_not_called()
    analyze_photo.assert_not_called()
    refund_credits.assert_not_called()
    complete_credits.assert_not_called()


def test_post_ai_text_meal_malformed_payload_rejects_before_gateway_or_credits(
    mocker: MockerFixture,
    auth_headers: AuthHeaders,
) -> None:
    evaluate_request = mocker.patch("app.api.routes.ai.ai_gateway_service.evaluate_request")
    deduct_credits = mocker.patch(
        "app.api.routes.ai.ai_credits_service.deduct_credits_idempotent"
    )
    analyze_text_meal = mocker.patch("app.api.routes.ai._execute_text_meal_completion")
    refund_credits = mocker.patch(
        "app.api.routes.ai.ai_credits_service.refund_credits_idempotent"
    )
    complete_credits = mocker.patch(
        "app.api.routes.ai.ai_credits_service.complete_credits_idempotent"
    )

    response = client.post(
        "/api/v1/ai/text-meal/analyze",
        json={},
        headers=auth_headers("abc"),
    )

    assert response.status_code == 422
    evaluate_request.assert_not_called()
    deduct_credits.assert_not_called()
    analyze_text_meal.assert_not_called()
    refund_credits.assert_not_called()
    complete_credits.assert_not_called()


def test_post_ai_text_meal_analyze_succeeds_when_log_sink_fails(
    mocker: MockerFixture,
    auth_headers: AuthHeaders,
) -> None:
    mocker.patch(
        "app.api.routes.ai.ai_gateway_logger.log_gateway_decision",
        side_effect=RuntimeError("sink unavailable"),
    )
    deduct_credits = mocker.patch(
        "app.api.routes.ai.ai_credits_service.deduct_credits_idempotent",
        return_value=_idempotent_credit_result(
            user_id="abc",
            tier="premium",
            balance=799,
            allocation=800,
            period_start_at=datetime(2026, 4, 14, tzinfo=timezone.utc),
            period_end_at=datetime(2026, 5, 14, tzinfo=timezone.utc),
        ),
    )
    complete_credits = mocker.patch(
        "app.api.routes.ai.ai_credits_service.complete_credits_idempotent",
        return_value=_idempotent_credit_result(
            user_id="abc",
            tier="premium",
            balance=799,
            allocation=800,
            period_start_at=datetime(2026, 4, 14, tzinfo=timezone.utc),
            period_end_at=datetime(2026, 5, 14, tzinfo=timezone.utc),
        ),
    )
    plain_deduct_credits = mocker.patch("app.api.routes.ai.ai_credits_service.deduct_credits")
    plain_refund_credits = mocker.patch("app.api.routes.ai.ai_credits_service.refund_credits")
    mocker.patch(
        "app.api.routes.ai._execute_text_meal_completion",
        return_value=(
            [
                {
                    "name": "Owsianka",
                    "amount": 120,
                    "protein": 6,
                    "fat": 4,
                    "carbs": 20,
                    "kcal": 148,
                }
            ],
            88,
        ),
    )

    response = client.post(
        "/api/v1/ai/text-meal/analyze",
        json={"payload": {"name": "owsianka"}},
        headers={**auth_headers("abc"), "X-Idempotency-Key": "text-log-sink-failure"},
    )

    assert response.status_code == 200
    assert response.json()["balance"] == 799
    expected_key = "ai-credit:abc:text_meal_analysis:text-log-sink-failure"
    deduct_credits.assert_called_once_with(
        "abc",
        cost=1,
        action="text_meal_analysis",
        idempotency_key=expected_key,
    )
    complete_credits.assert_called_once_with("abc", idempotency_key=expected_key)
    plain_deduct_credits.assert_not_called()
    plain_refund_credits.assert_not_called()
