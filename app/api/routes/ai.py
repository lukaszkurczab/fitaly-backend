"""Legacy AI v1 routes.

This module serves `/api/v1/ai/*` compatibility endpoints and intentionally keeps
the pre-v2 flow (`app/services/*`). Canonical AI Chat v2 lives under
`app/api/v2/endpoints/ai_chat.py` and `app/domain/chat/*`.
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from time import perf_counter
from typing import Any, Literal, cast
import uuid
import time

from fastapi import APIRouter, Depends, HTTPException, Request, status

from app.api.deps import (
    AuthenticatedUser,
    get_required_authenticated_user,
)
from app.api.http_errors import (
    raise_service_unavailable,
)
from app.core.config import settings
from app.core.exceptions import (
    AiCreditsExhaustedError,
    FirestoreServiceError,
    OpenAIServiceError,
)
from app.schemas.ai_ask import (
    AiAskContextStats,
    AiAskRequest,
    AiAskResponse,
    AiAskUsage,
)
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
    ai_chat_prompt_service as legacy_ai_chat_prompt_service,  # Backward-compatible symbol for legacy tests/mocks.
    ai_context_service as legacy_ai_context_service,
    ai_credits_service,
    ai_gateway_logger as legacy_ai_gateway_logger,
    ai_gateway_service as legacy_ai_gateway_service,
    ai_run_service as legacy_ai_run_service,
    ai_token_budget_service as legacy_ai_token_budget_service,
    chat_thread_service,
    conversation_memory_service as legacy_conversation_memory_service,
    openai_service as legacy_openai_service,
    sanitization_service,
    text_meal_service,
)

router = APIRouter()
logger = logging.getLogger(__name__)

# Backward-compatibility exports for existing v1 tests/mocks patching
# `app.api.routes.ai.<service_symbol>`.
ai_chat_prompt_service = legacy_ai_chat_prompt_service
ai_context_service = legacy_ai_context_service
ai_gateway_logger = legacy_ai_gateway_logger
ai_gateway_service = legacy_ai_gateway_service
ai_run_service = legacy_ai_run_service
ai_token_budget_service = legacy_ai_token_budget_service
conversation_memory_service = legacy_conversation_memory_service
openai_service = legacy_openai_service


def _resolve_request_id(request: Request) -> str | None:
    return getattr(request.state, "request_id", None)


def _safe_photo_gateway_message(image_base64: str) -> str:
    return f"[photo-bytes:{len(image_base64.strip())}]"


def _build_thread_title(message: str) -> str:
    normalized = message.strip()
    if len(normalized) <= 42:
        return normalized
    return f"{normalized[:42].rstrip()}…"


def _with_gateway_runtime(
    result: legacy_ai_gateway_service.GatewayResult,
    *,
    latency_ms: float,
    outcome: Literal["FORWARDED", "REJECTED", "UPSTREAM_ERROR", "LOCAL"] | None = None,
    failure_reason: str | None = None,
    actual_tokens: int | None = None,
    retry_count: int | None = None,
    used_summary: bool | None = None,
    truncated: bool | None = None,
    cost_charged: float | None = None,
) -> legacy_ai_gateway_service.GatewayResult:
    enriched: legacy_ai_gateway_service.GatewayResult = {
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
    result: legacy_ai_gateway_service.GatewayResult,
    response_time_ms: float | None = None,
    execution_time_ms: float | None = None,
    tier: Literal["free", "premium"] | None = None,
    credit_cost: float | None = None,
) -> None:
    if result["reason"] == legacy_ai_gateway_service.FORWARD_REASON_GATEWAY_DISABLED:
        return

    try:
        legacy_ai_gateway_logger.log_gateway_decision(
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
            "Failed to persist AI gateway decision.",
            extra={"user_id": user_id, "action_type": action_type},
        )


async def _log_ai_run_safe(run_id: str, payload: dict[str, Any]) -> None:
    try:
        await legacy_ai_run_service.log_ai_run(run_id, payload)
    except FirestoreServiceError:
        logger.exception(
            "Failed to persist AI run telemetry.",
            extra={"run_id": run_id},
        )


async def _reject_gateway_request(
    *,
    user_id: str,
    action_type: str,
    message: str,
    language: str,
    gateway_result: legacy_ai_gateway_service.GatewayResult,
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
    if gateway_result["reason"] == legacy_ai_gateway_service.GUARD_REASON_RATE_LIMITED:
        status_code = status.HTTP_429_TOO_MANY_REQUESTS
        detail_code = "AI_GATEWAY_RATE_LIMITED"
        detail_message = "AI request rate limited by gateway"
    elif gateway_result["reason"] in {
        legacy_ai_gateway_service.GUARD_REASON_MESSAGE_TOO_LONG,
        legacy_ai_gateway_service.GUARD_REASON_PAYLOAD_TOO_LARGE,
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
) -> AiCreditsStatus:
    try:
        return await ai_credits_service.deduct_credits(user_id, cost=cost, action=action)
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


async def _refund_credits_after_ai_failure(
    *,
    user_id: str,
    cost: int,
    action: str,
    endpoint: str,
) -> None:
    max_attempts = 3
    for attempt in range(1, max_attempts + 1):
        try:
            credits_status = await ai_credits_service.refund_credits(
                user_id,
                cost=cost,
                action=action,
            )
            logger.info(
                "Refunded AI credits after upstream failure.",
                extra={
                    "user_id": user_id,
                    "endpoint": endpoint,
                    "cost": cost,
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
                    "attempts": max_attempts,
                },
            )


def _build_ai_response_fields(
    *,
    credits_status: AiCreditsStatus,
    warnings: list[str],
    gateway_result: legacy_ai_gateway_service.GatewayResult | None = None,
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
    gateway_result: legacy_ai_gateway_service.GatewayResult,
    credit_cost: int,
    endpoint: str,
    ai_call: Callable[[], Awaitable[tuple[Any, int | None]]],
    started_at: float,
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

    credits_status = await _deduct_credits_or_raise(
        user_id=user_id,
        cost=credit_cost,
        action=action_type,
    )

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
        )
        raise_service_unavailable(exc, detail="AI service unavailable")

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

    return result, credits_status, actual_tokens


@router.post("/ai/ask", response_model=AiAskResponse)
async def ask_ai(
    http_request: Request,
    request: AiAskRequest,
    current_user: AuthenticatedUser = Depends(get_required_authenticated_user),
) -> AiAskResponse:
    started_at = perf_counter()
    user_id = current_user.uid
    action_type = "chat"
    request_id = _resolve_request_id(http_request)
    ask_payload = request.model_dump_json(exclude_none=True)
    gateway_result = await legacy_ai_gateway_service.evaluate_request(
        user_id,
        action_type,
        request.message,
        language=request.language or "pl",
        request_id=request_id,
        raw_payload_chars=len(ask_payload),
    )

    context = await legacy_ai_context_service.build_chat_context(user_id, request.threadId)
    profile = context["profile"]
    language = legacy_ai_context_service.resolve_language(request.language, profile)

    has_consent = legacy_ai_context_service.has_ai_health_data_consent(profile)
    if not has_consent and "PROFILE_UNAVAILABLE" in context["warnings"]:
        context["warnings"].append("CONSENT_CHECK_SKIPPED")
        has_consent = True

    if not has_consent:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "message": "AI health data consent required",
                "code": "AI_HEALTH_DATA_CONSENT_REQUIRED",
            },
        )

    if gateway_result["decision"] == "REJECT":
        await _reject_gateway_request(
            user_id=user_id,
            action_type=action_type,
            message=request.message,
            language=language,
            gateway_result=gateway_result,
            started_at=started_at,
        )

    credits_status = await _deduct_credits_or_raise(
        user_id=user_id,
        cost=settings.AI_CREDIT_COST_CHAT,
        action=action_type,
    )

    assistant_message_id = uuid.uuid4().hex
    user_created_at = int(time.time() * 1000)
    assistant_created_at = user_created_at + 1
    base_warnings = list(dict.fromkeys(context["warnings"]))
    scope_decision = cast(
        Literal["ALLOW_APP", "ALLOW_USER_DATA", "ALLOW_NUTRITION", "DENY_OTHER"],
        gateway_result.get("scope_decision") or "DENY_OTHER",
    )

    sanitized_context = sanitization_service.sanitize_context(
        {
            "profile": profile,
            "history": context["history_messages"],
            "meals": context["meals"],
        }
    ) or {}
    sanitized_profile = cast(dict[str, Any], sanitized_context.get("profile") or {})
    sanitized_history = cast(list[dict[str, Any]], sanitized_context.get("history") or [])
    sanitized_meals = cast(list[dict[str, Any]], sanitized_context.get("meals") or [])
    sanitized_message = sanitization_service.sanitize_request(request.message, sanitized_context)

    prompt_data = legacy_ai_token_budget_service.build_budgeted_prompt(
        user_message=sanitized_message,
        language=language,
        profile=sanitized_profile,
        meals=sanitized_meals,
        history_messages=sanitized_history,
        memory_summary=context["memory_summary"],
    )

    try:
        completion = await legacy_openai_service.ask_chat_completion_with_retry(prompt_data["prompt"])
    except OpenAIServiceError as exc:
        elapsed_ms = (perf_counter() - started_at) * 1000
        logged_gateway_result = _with_gateway_runtime(
            gateway_result,
            latency_ms=elapsed_ms,
            outcome="UPSTREAM_ERROR",
            failure_reason=exc.__class__.__name__,
            actual_tokens=None,
            retry_count=0,
            used_summary=prompt_data["used_summary"],
            truncated=prompt_data["truncated"],
            cost_charged=float(settings.AI_CREDIT_COST_CHAT),
        )
        await _log_gateway_result(
            user_id=user_id,
            action_type=action_type,
            message=request.message,
            language=language,
            result=logged_gateway_result,
            response_time_ms=elapsed_ms,
            execution_time_ms=elapsed_ms,
            tier=credits_status.tier,
            credit_cost=float(settings.AI_CREDIT_COST_CHAT),
        )
        await _refund_credits_after_ai_failure(
            user_id=user_id,
            cost=settings.AI_CREDIT_COST_CHAT,
            action="chat_failure_refund",
            endpoint="/ai/ask",
        )
        raise_service_unavailable(exc, detail="AI service unavailable")

    reply = completion["content"]
    usage_payload = completion["usage"]
    retry_count = completion["retry_count"]

    await chat_thread_service.persist_exchange(
        user_id,
        request.threadId,
        user_message_id=request.clientMessageId,
        user_content=request.message,
        user_created_at=user_created_at,
        assistant_message_id=assistant_message_id,
        assistant_content=reply,
        assistant_created_at=assistant_created_at,
        title=_build_thread_title(request.message),
    )

    history_with_latest = [
        *context["history_messages"],
        {"id": request.clientMessageId, "role": "user", "content": request.message},
        {"id": assistant_message_id, "role": "assistant", "content": reply},
    ]
    if len(history_with_latest) >= 10 or prompt_data["generated_summary"]:
        await legacy_conversation_memory_service.refresh_summary_from_history(
            user_id,
            request.threadId,
            history_with_latest,
            covered_until_message_id=assistant_message_id,
        )

    elapsed_ms = (perf_counter() - started_at) * 1000
    logged_gateway_result = _with_gateway_runtime(
        gateway_result,
        latency_ms=elapsed_ms,
        outcome="FORWARDED",
        actual_tokens=usage_payload["total_tokens"],
        retry_count=retry_count,
        used_summary=prompt_data["used_summary"],
        truncated=prompt_data["truncated"],
        cost_charged=float(settings.AI_CREDIT_COST_CHAT),
    )
    await _log_gateway_result(
        user_id=user_id,
        action_type=action_type,
        message=request.message,
        language=language,
        result=logged_gateway_result,
        response_time_ms=elapsed_ms,
        execution_time_ms=elapsed_ms,
        tier=credits_status.tier,
        credit_cost=float(settings.AI_CREDIT_COST_CHAT),
    )
    await _log_ai_run_safe(
        logged_gateway_result["request_id"],
        {
            "userId": user_id,
            "threadId": request.threadId,
            "actionType": action_type,
            "scopeDecision": scope_decision,
            "outcome": "FORWARDED",
            "costCharged": float(settings.AI_CREDIT_COST_CHAT),
            "retryCount": retry_count,
            "usedSummary": prompt_data["used_summary"],
            "truncated": prompt_data["truncated"],
            "usage": {
                "promptTokens": usage_payload["prompt_tokens"],
                "completionTokens": usage_payload["completion_tokens"],
                "totalTokens": usage_payload["total_tokens"],
            },
        },
    )

    warnings = base_warnings[:]
    if prompt_data["used_summary"]:
        warnings.append("USED_SUMMARY")
    if prompt_data["truncated"]:
        warnings.append("CONTEXT_TRUNCATED")

    usage = AiAskUsage(
        promptTokens=usage_payload["prompt_tokens"],
        completionTokens=usage_payload["completion_tokens"],
        totalTokens=usage_payload["total_tokens"],
    )
    context_stats = AiAskContextStats(
        usedSummary=prompt_data["used_summary"],
        historyTurns=prompt_data["history_turns"],
        truncated=prompt_data["truncated"],
        scopeDecision=scope_decision,
    )

    return AiAskResponse(
        reply=reply,
        threadId=request.threadId,
        assistantMessageId=assistant_message_id,
        usage=usage,
        contextStats=context_stats,
        scopeDecision=scope_decision,
        **_build_ai_response_fields(
            credits_status=credits_status,
            warnings=warnings,
            gateway_result=logged_gateway_result,
        ),
    )


@router.post("/ai/photo/analyze", response_model=AiPhotoAnalyzeResponse)
async def analyze_photo_ai(
    http_request: Request,
    request: AiPhotoAnalyzeRequest,
    current_user: AuthenticatedUser = Depends(get_required_authenticated_user),
) -> AiPhotoAnalyzeResponse:
    started_at = perf_counter()
    user_id = current_user.uid
    gateway_message = _safe_photo_gateway_message(request.imageBase64)
    gateway_result = await legacy_ai_gateway_service.evaluate_request(
        user_id,
        "photo_analysis",
        gateway_message,
        language=request.lang,
        request_id=_resolve_request_id(http_request),
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
    started_at = perf_counter()
    user_id = current_user.uid
    gateway_message = request.payload.model_dump_json(exclude_none=True)
    gateway_result = await legacy_ai_gateway_service.evaluate_request(
        user_id,
        "text_meal_analysis",
        gateway_message,
        language=request.lang,
        request_id=_resolve_request_id(http_request),
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
    )

    return AiTextMealAnalyzeResponse(
        ingredients=[AiTextMealIngredient(**ingredient) for ingredient in ingredients],
        **_build_ai_response_fields(
            credits_status=credits_status,
            warnings=[],
            gateway_result=gateway_result,
        ),
    )


async def _execute_chat_completion(message: str) -> tuple[str, int | None]:
    completion = await legacy_openai_service.ask_chat_completion(message)
    return completion["content"], completion["usage"]["total_tokens"]


async def _execute_photo_completion(
    image_base64: str,
    *,
    lang: str,
) -> tuple[list[legacy_openai_service.AnalyzedIngredient], int | None]:
    completion = await legacy_openai_service.analyze_photo_completion(
        image_base64,
        lang=lang,
    )
    return completion["ingredients"], completion["usage"]["total_tokens"]


async def _execute_text_meal_completion(
    payload: Any,
    *,
    lang: str,
) -> tuple[list[legacy_openai_service.AnalyzedIngredient], int | None]:
    completion = await text_meal_service.analyze_text_meal_with_usage(
        payload,
        lang=lang,
    )
    return completion["ingredients"], completion["total_tokens"]
