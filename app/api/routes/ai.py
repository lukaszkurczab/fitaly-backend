"""AI v1 analysis routes.

This module serves the remaining `/api/v1/ai/*` analysis endpoints.
Canonical AI chat runtime is exposed only by
`POST /api/v2/ai/chat/runs`.
"""

import asyncio
import logging
import uuid
from collections.abc import Awaitable, Callable
from time import perf_counter
from typing import Any, Literal, NoReturn

from fastapi import APIRouter, Depends, HTTPException, Request, status

from app.api.deps import (
    AuthenticatedUser,
    get_required_authenticated_user,
)
from app.core.config import settings
from app.core.errors import ConsentRequiredError
from app.core.exceptions import (
    AiCreditsExhaustedError,
    FirestoreServiceError,
    OpenAIServiceError,
)
from app.domain.users.services.consent_service import ConsentService
from app.domain.users.services.user_profile_service import UserProfileService
from app.schemas.ai_common import BACKEND_OWNED_PERSISTENCE
from app.schemas.ai_credits import AiCreditsStatus
from app.schemas.ai_photo import (
    AiPhotoAnalyzeRequest,
    AiPhotoAnalyzeResponse,
    AiPhotoIngredient,
)
from app.schemas.ai_text_meal import (
    AiTextMealAnalyzeRequest,
    AiTextMealAnalyzeResponse,
    AiTextMealIngredient,
)
from app.services import (
    ai_credits_service,
    ai_gateway_logger,
    ai_gateway_service,
    openai_service,
    text_meal_service,
)

router = APIRouter()
logger = logging.getLogger(__name__)

_OPENAI_TIMEOUT_MESSAGES = {
    "openai request timed out.",
    "openai photo analysis timed out.",
}
_AI_MEAL_ANALYSIS_IDEMPOTENCY_CONFLICT_CODE = "AI_MEAL_ANALYSIS_IDEMPOTENCY_CONFLICT"
_AI_MEAL_ANALYSIS_IDEMPOTENCY_CONFLICT_MESSAGE = (
    "Meal analysis request is already in progress or completed."
)


def _resolve_request_id(request: Request) -> str | None:
    return getattr(request.state, "request_id", None)


def _resolve_ai_operation_id(request: Request) -> str:
    idempotency_key = (request.headers.get("X-Idempotency-Key") or "").strip()
    if idempotency_key:
        return idempotency_key

    request_id = (_resolve_request_id(request) or "").strip()
    if request_id:
        return request_id

    return uuid.uuid4().hex


def _build_credit_idempotency_key(
    *,
    user_id: str,
    action: str,
    operation_id: str,
) -> str:
    return f"ai-credit:{user_id}:{action}:{operation_id}"


def _safe_photo_gateway_message(image_base64: str) -> str:
    return f"[photo-bytes:{len(image_base64.strip())}]"


def _with_gateway_runtime(
    result: ai_gateway_service.GatewayResult,
    *,
    latency_ms: float,
    outcome: Literal["FORWARDED", "REJECTED", "UPSTREAM_ERROR", "LOCAL"] | None = None,
    failure_reason: str | None = None,
    actual_tokens: int | None = None,
    retry_count: int | None = None,
    used_summary: bool | None = None,
    truncated: bool | None = None,
    cost_charged: float | None = None,
) -> ai_gateway_service.GatewayResult:
    enriched: ai_gateway_service.GatewayResult = {
        **result,
        "latency_ms": round(latency_ms, 2),
    }
    if outcome is not None:
        enriched["outcome"] = outcome
    if failure_reason is not None:
        enriched["failure_reason"] = failure_reason
    if actual_tokens is not None:
        enriched["actual_tokens"] = actual_tokens
    if retry_count is not None:
        enriched["retry_count"] = retry_count
    if used_summary is not None:
        enriched["used_summary"] = used_summary
    if truncated is not None:
        enriched["truncated"] = truncated
    if cost_charged is not None:
        enriched["cost_charged"] = round(cost_charged, 4)
    return enriched


async def _log_gateway_result(
    *,
    user_id: str,
    action_type: str,
    message: str,
    language: str,
    result: ai_gateway_service.GatewayResult,
    response_time_ms: float | None = None,
    execution_time_ms: float | None = None,
    tier: Literal["free", "premium"] | None = None,
    credit_cost: float | None = None,
) -> None:
    if result["reason"] == ai_gateway_service.FORWARD_REASON_GATEWAY_DISABLED:
        return

    try:
        ai_gateway_logger.log_gateway_decision(
            user_id,
            message,
            result,
            action_type,
            language=language,
            response_time_ms=response_time_ms,
            execution_time_ms=execution_time_ms,
            profile=tier,
            tier=tier,
            credit_cost=credit_cost,
        )
    except Exception:  # noqa: BLE001
        logger.exception(
            "Failed to emit AI gateway observability/analytics event.",
            extra={"user_id": user_id, "action_type": action_type},
        )


async def _ensure_active_ai_consent(*, user_id: str) -> None:
    consent_service = ConsentService(UserProfileService())
    try:
        await consent_service.ensure_ai_consent(user_id=user_id)
    except ConsentRequiredError as exc:
        detail_message = str(exc).strip() or exc.code
        raise HTTPException(
            status_code=exc.status_code,
            detail={
                "code": exc.code,
                "message": detail_message,
                "aiConsent": {
                    "required": True,
                    "scope": "global_ai_health_data",
                },
            },
        ) from exc


def _ensure_meal_analysis_enabled() -> None:
    if settings.AI_MEAL_ANALYSIS_ENABLED:
        return

    raise HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail={
            "code": "AI_MEAL_ANALYSIS_DISABLED",
            "message": "Meal analysis AI is temporarily disabled.",
        },
    )


def _is_openai_timeout_error(exc: OpenAIServiceError) -> bool:
    cause: BaseException | None = exc.__cause__
    for _ in range(4):
        if cause is None:
            break
        if isinstance(cause, (asyncio.TimeoutError, TimeoutError)):
            return True
        cause = cause.__cause__

    return str(exc).strip().lower() in _OPENAI_TIMEOUT_MESSAGES


def _raise_ai_provider_error(exc: OpenAIServiceError) -> NoReturn:
    if _is_openai_timeout_error(exc):
        raise HTTPException(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
            detail={
                "code": "AI_CHAT_TIMEOUT",
                "message": "AI provider timed out before a response was generated.",
            },
        ) from exc

    raise HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail={
            "code": "AI_CHAT_PROVIDER_UNAVAILABLE",
            "message": "AI provider is temporarily unavailable.",
        },
    ) from exc


async def _reject_gateway_request(
    *,
    user_id: str,
    action_type: str,
    message: str,
    language: str,
    gateway_result: ai_gateway_service.GatewayResult,
    started_at: float,
) -> None:
    tier: Literal["free", "premium"] | None = None
    try:
        tier = (await ai_credits_service.get_credits_status(user_id)).tier
    except FirestoreServiceError:
        logger.exception(
            "Failed to resolve AI tier for gateway reject log.",
            extra={"user_id": user_id, "action_type": action_type},
        )
    elapsed_ms = (perf_counter() - started_at) * 1000
    await _log_gateway_result(
        user_id=user_id,
        action_type=action_type,
        message=message,
        language=language,
        result=_with_gateway_runtime(
            gateway_result,
            latency_ms=elapsed_ms,
            outcome="REJECTED",
            cost_charged=0.0,
        ),
        response_time_ms=elapsed_ms,
        execution_time_ms=elapsed_ms,
        tier=tier,
        credit_cost=0.0,
    )
    status_code = status.HTTP_400_BAD_REQUEST
    detail_code = "AI_GATEWAY_BLOCKED"
    detail_message = "AI request blocked by gateway"
    if gateway_result["reason"] == ai_gateway_service.GUARD_REASON_RATE_LIMITED:
        status_code = status.HTTP_429_TOO_MANY_REQUESTS
        detail_code = "AI_GATEWAY_RATE_LIMITED"
        detail_message = "AI request rate limited by gateway"
    elif gateway_result["reason"] in {
        ai_gateway_service.GUARD_REASON_MESSAGE_TOO_LONG,
        ai_gateway_service.GUARD_REASON_PAYLOAD_TOO_LARGE,
    }:
        status_code = status.HTTP_413_CONTENT_TOO_LARGE
        detail_code = "AI_GATEWAY_PAYLOAD_TOO_LARGE"
        detail_message = "AI request payload too large"

    raise HTTPException(
        status_code=status_code,
        detail={
            "message": detail_message,
            "code": detail_code,
            "reason": gateway_result["reason"],
            "score": gateway_result["score"],
        },
    )


async def _deduct_credits_or_raise(
    *,
    user_id: str,
    cost: int,
    action: str,
    idempotency_key: str,
) -> tuple[AiCreditsStatus, bool]:
    try:
        result = await ai_credits_service.deduct_credits_idempotent(
            user_id,
            cost=cost,
            action=action,
            idempotency_key=idempotency_key,
        )
        return result.status, result.applied
    except AiCreditsExhaustedError:
        credits_status = await ai_credits_service.get_credits_status(user_id)
        logger.warning(
            "AI credits exhausted for requested action.",
            extra={
                "user_id": user_id,
                "action": action,
                "credit_cost": cost,
                "tier": credits_status.tier,
                "balance": credits_status.balance,
                "allocation": credits_status.allocation,
                "period_end_at": credits_status.periodEndAt.isoformat(),
            },
        )
        raise HTTPException(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            detail={
                "message": "AI credits exhausted",
                "code": "AI_CREDITS_EXHAUSTED",
                "credits": credits_status.model_dump(mode="json"),
            },
        )


def _raise_ai_meal_analysis_idempotency_conflict() -> NoReturn:
    raise HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail={
            "code": _AI_MEAL_ANALYSIS_IDEMPOTENCY_CONFLICT_CODE,
            "message": _AI_MEAL_ANALYSIS_IDEMPOTENCY_CONFLICT_MESSAGE,
        },
    )


async def _refund_credits_after_ai_failure(
    *,
    user_id: str,
    cost: int,
    action: str,
    endpoint: str,
    idempotency_key: str,
) -> None:
    max_attempts = 3
    for attempt in range(1, max_attempts + 1):
        try:
            result = await ai_credits_service.refund_credits_idempotent(
                user_id,
                cost=cost,
                action=action,
                idempotency_key=idempotency_key,
            )
            credits_status = result.status
            logger.info(
                "Refunded AI credits after upstream failure.",
                extra={
                    "user_id": user_id,
                    "endpoint": endpoint,
                    "cost": cost,
                    "idempotency_key": idempotency_key,
                    "applied": result.applied,
                    "balance": credits_status.balance,
                    "allocation": credits_status.allocation,
                    "tier": credits_status.tier,
                    "attempt": attempt,
                },
            )
            return
        except (FirestoreServiceError, ValueError):
            if attempt < max_attempts:
                await asyncio.sleep(0.5)
                continue
            logger.exception(
                "Failed to refund AI credits after upstream failure — all retries exhausted. Credits lost.",
                extra={
                    "user_id": user_id,
                    "endpoint": endpoint,
                    "cost": cost,
                    "idempotency_key": idempotency_key,
                    "attempts": max_attempts,
                },
            )


def _build_ai_response_fields(
    *,
    credits_status: AiCreditsStatus,
    warnings: list[str],
    gateway_result: ai_gateway_service.GatewayResult | None = None,
) -> dict[str, Any]:
    return {
        "balance": credits_status.balance,
        "allocation": credits_status.allocation,
        "tier": credits_status.tier,
        "periodStartAt": credits_status.periodStartAt,
        "periodEndAt": credits_status.periodEndAt,
        "costs": credits_status.costs,
        "version": settings.VERSION,
        "persistence": BACKEND_OWNED_PERSISTENCE,
        "model": gateway_result.get("model") if gateway_result is not None else None,
        "runId": gateway_result.get("request_id") if gateway_result is not None else None,
        "confidence": None,
        "warnings": warnings,
    }


async def _execute_ai_request(
    *,
    user_id: str,
    action_type: str,
    gateway_message: str,
    language: str,
    gateway_result: ai_gateway_service.GatewayResult,
    credit_cost: int,
    endpoint: str,
    ai_call: Callable[[], Awaitable[tuple[Any, int | None]]],
    started_at: float,
    credit_idempotency_key: str,
) -> tuple[Any, AiCreditsStatus, int | None]:
    if gateway_result["decision"] != "FORWARD":
        await _reject_gateway_request(
            user_id=user_id,
            action_type=action_type,
            message=gateway_message,
            language=language,
            gateway_result=gateway_result,
            started_at=started_at,
        )

    credits_status, credit_deduction_applied = await _deduct_credits_or_raise(
        user_id=user_id,
        cost=credit_cost,
        action=action_type,
        idempotency_key=credit_idempotency_key,
    )
    if not credit_deduction_applied:
        _raise_ai_meal_analysis_idempotency_conflict()

    try:
        result, actual_tokens = await ai_call()
    except OpenAIServiceError as exc:
        elapsed_ms = (perf_counter() - started_at) * 1000
        await _log_gateway_result(
            user_id=user_id,
            action_type=action_type,
            message=gateway_message,
            language=language,
            result=_with_gateway_runtime(
                gateway_result,
                latency_ms=elapsed_ms,
                outcome="UPSTREAM_ERROR",
                failure_reason=exc.__class__.__name__,
                actual_tokens=None,
                cost_charged=float(credit_cost),
            ),
            response_time_ms=elapsed_ms,
            execution_time_ms=elapsed_ms,
            tier=credits_status.tier,
            credit_cost=float(credit_cost),
        )
        await _refund_credits_after_ai_failure(
            user_id=user_id,
            cost=credit_cost,
            action=f"{action_type}_failure_refund",
            endpoint=endpoint,
            idempotency_key=credit_idempotency_key,
        )
        _raise_ai_provider_error(exc)

    elapsed_ms = (perf_counter() - started_at) * 1000
    await _log_gateway_result(
        user_id=user_id,
        action_type=action_type,
        message=gateway_message,
        language=language,
        result=_with_gateway_runtime(
            gateway_result,
            latency_ms=elapsed_ms,
            outcome="FORWARDED",
            actual_tokens=actual_tokens,
            cost_charged=float(credit_cost),
        ),
        response_time_ms=elapsed_ms,
        execution_time_ms=elapsed_ms,
        tier=credits_status.tier,
        credit_cost=float(credit_cost),
    )

    completed_credits = await ai_credits_service.complete_credits_idempotent(
        user_id,
        idempotency_key=credit_idempotency_key,
    )

    return result, completed_credits.status, actual_tokens


@router.post("/ai/photo/analyze", response_model=AiPhotoAnalyzeResponse)
async def analyze_photo_ai(
    http_request: Request,
    request: AiPhotoAnalyzeRequest,
    current_user: AuthenticatedUser = Depends(get_required_authenticated_user),
) -> AiPhotoAnalyzeResponse:
    user_id = current_user.uid
    await _ensure_active_ai_consent(user_id=user_id)
    _ensure_meal_analysis_enabled()

    started_at = perf_counter()
    operation_id = _resolve_ai_operation_id(http_request)
    gateway_message = _safe_photo_gateway_message(request.imageBase64)
    gateway_result = await ai_gateway_service.evaluate_request(
        user_id,
        "photo_analysis",
        gateway_message,
        language=request.lang,
        request_id=operation_id,
        raw_payload_chars=len(request.imageBase64.strip()),
    )

    ingredients, credits_status, _ = await _execute_ai_request(
        user_id=user_id,
        action_type="photo_analysis",
        gateway_message=gateway_message,
        language=request.lang,
        gateway_result=gateway_result,
        credit_cost=settings.AI_CREDIT_COST_PHOTO,
        endpoint="/ai/photo/analyze",
        ai_call=lambda: _execute_photo_completion(
            request.imageBase64,
            lang=request.lang,
        ),
        started_at=started_at,
        credit_idempotency_key=_build_credit_idempotency_key(
            user_id=user_id,
            action="photo_analysis",
            operation_id=operation_id,
        ),
    )

    return AiPhotoAnalyzeResponse(
        ingredients=[AiPhotoIngredient(**ingredient) for ingredient in ingredients],
        **_build_ai_response_fields(
            credits_status=credits_status,
            warnings=[],
            gateway_result=gateway_result,
        ),
    )


@router.post("/ai/text-meal/analyze", response_model=AiTextMealAnalyzeResponse)
async def analyze_text_meal_ai(
    http_request: Request,
    request: AiTextMealAnalyzeRequest,
    current_user: AuthenticatedUser = Depends(get_required_authenticated_user),
) -> AiTextMealAnalyzeResponse:
    user_id = current_user.uid
    await _ensure_active_ai_consent(user_id=user_id)
    _ensure_meal_analysis_enabled()

    started_at = perf_counter()
    operation_id = _resolve_ai_operation_id(http_request)
    gateway_message = request.payload.model_dump_json(exclude_none=True)
    gateway_result = await ai_gateway_service.evaluate_request(
        user_id,
        "text_meal_analysis",
        gateway_message,
        language=request.lang,
        request_id=operation_id,
        raw_payload_chars=len(gateway_message),
    )

    ingredients, credits_status, _ = await _execute_ai_request(
        user_id=user_id,
        action_type="text_meal_analysis",
        gateway_message=gateway_message,
        language=request.lang,
        gateway_result=gateway_result,
        credit_cost=settings.AI_CREDIT_COST_TEXT_MEAL,
        endpoint="/ai/text-meal/analyze",
        ai_call=lambda: _execute_text_meal_completion(
            request.payload,
            lang=request.lang,
        ),
        started_at=started_at,
        credit_idempotency_key=_build_credit_idempotency_key(
            user_id=user_id,
            action="text_meal_analysis",
            operation_id=operation_id,
        ),
    )

    return AiTextMealAnalyzeResponse(
        ingredients=[AiTextMealIngredient(**ingredient) for ingredient in ingredients],
        **_build_ai_response_fields(
            credits_status=credits_status,
            warnings=[],
            gateway_result=gateway_result,
        ),
    )


async def _execute_photo_completion(
    image_base64: str,
    *,
    lang: str,
) -> tuple[list[openai_service.AnalyzedIngredient], int | None]:
    completion = await openai_service.analyze_photo_completion(
        image_base64,
        lang=lang,
    )
    return completion["ingredients"], completion["usage"]["total_tokens"]


async def _execute_text_meal_completion(
    payload: Any,
    *,
    lang: str,
) -> tuple[list[openai_service.AnalyzedIngredient], int | None]:
    completion = await text_meal_service.analyze_text_meal_with_usage(
        payload,
        lang=lang,
    )
    return completion["ingredients"], completion["total_tokens"]
