import logging
from time import perf_counter
from typing import TypedDict

from fastapi import APIRouter, Depends, HTTPException, status

from app.api.deps import (
    AuthenticatedUser,
    get_required_authenticated_user,
)
from app.api.http_errors import (
    raise_service_unavailable,
)
from app.core.config import settings
from app.core.exceptions import (
    AiUsageLimitExceededError,
    OpenAIServiceError,
)
from app.schemas.ai_ask import AiAskRequest, AiAskResponse
from app.schemas.ai_common import AiPersistence, BACKEND_OWNED_PERSISTENCE
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
    ai_chat_prompt_service,
    ai_gateway_logger,
    ai_gateway_service,
    ai_usage_service,
    openai_service,
    sanitization_service,
    text_meal_service,
)

router = APIRouter()
logger = logging.getLogger(__name__)


class AiResponseFields(TypedDict):
    usageCount: float
    dailyLimit: int
    remaining: float
    dateKey: str
    version: str
    persistence: AiPersistence


def _resolve_language(request: AiAskRequest) -> str:
    if request.context:
        for key in ("language", "lang"):
            value = request.context.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    return "pl"


def _resolve_action_type(request: AiAskRequest) -> str:
    if request.context:
        for key in ("actionType", "action_type"):
            value = request.context.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    return "chat"


async def _log_gateway_result(
    *,
    user_id: str,
    action_type: str,
    message: str,
    language: str,
    result: ai_gateway_service.GatewayResult,
    response_time_ms: float | None = None,
    execution_time_ms: float | None = None,
    profile: str | None = None,
) -> None:
    try:
        ai_gateway_logger.log_gateway_decision(
            user_id,
            message,
            result,
            action_type,
            language=language,
            response_time_ms=response_time_ms,
            execution_time_ms=execution_time_ms,
            profile=profile,
        )
    except Exception:
        logger.exception(
            "Failed to persist AI gateway decision.",
            extra={"user_id": user_id, "action_type": action_type},
        )


async def _increment_usage_or_raise(
    user_id: str,
    *,
    cost: float = 1.0,
    include_cost_kwarg: bool = False,
) -> tuple[float, int, str, float]:
    try:
        if include_cost_kwarg:
            usage_count, daily_limit, date_key, remaining = (
                await ai_usage_service.increment_usage(user_id, cost=cost)
            )
        else:
            usage_count, daily_limit, date_key, remaining = (
                await ai_usage_service.increment_usage(user_id)
            )
    except AiUsageLimitExceededError:
        usage_count, daily_limit, date_key = await ai_usage_service.get_usage(user_id)
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail={
                "message": "AI usage limit exceeded",
                "code": "AI_USAGE_LIMIT_EXCEEDED",
                "usage": ai_usage_service.build_usage_status(
                    usage_count=usage_count,
                    daily_limit=daily_limit,
                    date_key=date_key,
                ),
            },
        )

    return usage_count, daily_limit, date_key, remaining


async def _refund_usage_after_ai_failure(
    *,
    user_id: str,
    date_key: str,
    cost: float,
    endpoint: str,
) -> None:
    try:
        usage_count, _daily_limit, _refunded_date_key, remaining = (
            await ai_usage_service.decrement_usage(
                user_id,
                cost=cost,
                date_key=date_key,
            )
        )
        logger.info(
            "Refunded AI usage after upstream failure.",
            extra={
                "user_id": user_id,
                "endpoint": endpoint,
                "cost": cost,
                "usage_count": usage_count,
                "remaining": remaining,
                "date_key": date_key,
            },
        )
    except Exception:
        logger.exception(
            "Failed to refund AI usage after upstream failure.",
            extra={
                "user_id": user_id,
                "endpoint": endpoint,
                "cost": cost,
                "date_key": date_key,
            },
        )


def _build_ai_response_fields(
    *,
    usage_count: float,
    daily_limit: int,
    remaining: float,
    date_key: str,
) -> AiResponseFields:
    return {
        **ai_usage_service.build_usage_status(
            usage_count=usage_count,
            daily_limit=daily_limit,
            date_key=date_key,
        ),
        "remaining": remaining,
        "version": settings.VERSION,
        "persistence": BACKEND_OWNED_PERSISTENCE,
    }


def _build_ai_ask_response(
    *,
    reply: str,
    usage_count: float,
    daily_limit: int,
    remaining: float,
    date_key: str,
) -> AiAskResponse:
    return AiAskResponse(
        reply=reply,
        **_build_ai_response_fields(
            usage_count=usage_count,
            daily_limit=daily_limit,
            remaining=remaining,
            date_key=date_key,
        ),
    )


@router.post("/ai/ask", response_model=AiAskResponse)
async def ask_ai(
    request: AiAskRequest,
    current_user: AuthenticatedUser = Depends(get_required_authenticated_user),
) -> AiAskResponse:
    started_at = perf_counter()
    user_id = current_user.uid

    language = _resolve_language(request)
    action_type = _resolve_action_type(request)
    gateway_result: ai_gateway_service.GatewayResult = {
        "decision": "FORWARD",
        "reason": "GATEWAY_DISABLED",
        "score": 1.0,
        "credit_cost": 1.0,
    }
    if settings.AI_GATEWAY_ENABLED and action_type == "chat":
        gateway_result = ai_gateway_service.evaluate_request(
            user_id,
            action_type,
            request.message,
            language=language,
        )
    elif action_type != "chat":
        gateway_result = {
            "decision": "FORWARD",
            "reason": "NON_CHAT_BYPASS",
            "score": 1.0,
            "credit_cost": 1.0,
        }
    if gateway_result["decision"] != "FORWARD":
        await _log_gateway_result(
            user_id=user_id,
            action_type=action_type,
            message=request.message,
            language=language,
            result=gateway_result,
            response_time_ms=(perf_counter() - started_at) * 1000,
            execution_time_ms=(perf_counter() - started_at) * 1000,
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "message": "AI request blocked by gateway",
                "code": "AI_GATEWAY_BLOCKED",
                "reason": gateway_result["reason"],
                "score": gateway_result["score"],
            },
        )

    sanitized_context = sanitization_service.sanitize_context(request.context)
    sanitized_message = sanitization_service.sanitize_request(
        request.message, sanitized_context
    )
    prompt_message = (
        ai_chat_prompt_service.build_chat_prompt(
            sanitized_message,
            sanitized_context,
            language=language,
        )
        if action_type == "chat"
        else sanitized_message
    )

    usage_count, daily_limit, date_key, remaining = await _increment_usage_or_raise(
        user_id,
        cost=gateway_result["credit_cost"],
        include_cost_kwarg=True,
    )

    try:
        reply = await openai_service.ask_chat(prompt_message)
    except OpenAIServiceError as exc:
        await _refund_usage_after_ai_failure(
            user_id=user_id,
            date_key=date_key,
            cost=gateway_result["credit_cost"],
            endpoint="/ai/ask",
        )
        raise_service_unavailable(exc, detail="AI service unavailable")

    await _log_gateway_result(
        user_id=user_id,
        action_type=action_type,
        message=request.message,
        language=language,
        result=gateway_result,
        response_time_ms=(perf_counter() - started_at) * 1000,
        execution_time_ms=(perf_counter() - started_at) * 1000,
    )

    return _build_ai_ask_response(
        reply=reply,
        usage_count=usage_count,
        daily_limit=daily_limit,
        remaining=remaining,
        date_key=date_key,
    )


@router.post("/ai/photo/analyze", response_model=AiPhotoAnalyzeResponse)
async def analyze_photo_ai(
    request: AiPhotoAnalyzeRequest,
    current_user: AuthenticatedUser = Depends(get_required_authenticated_user),
) -> AiPhotoAnalyzeResponse:
    user_id = current_user.uid
    usage_count, daily_limit, date_key, remaining = await _increment_usage_or_raise(user_id)

    try:
        ingredients = await openai_service.analyze_photo(
            request.imageBase64,
            lang=request.lang,
        )
    except OpenAIServiceError as exc:
        await _refund_usage_after_ai_failure(
            user_id=user_id,
            date_key=date_key,
            cost=1.0,
            endpoint="/ai/photo/analyze",
        )
        raise_service_unavailable(exc, detail="AI service unavailable")

    return AiPhotoAnalyzeResponse(
        ingredients=[AiPhotoIngredient(**ingredient) for ingredient in ingredients],
        **_build_ai_response_fields(
            usage_count=usage_count,
            daily_limit=daily_limit,
            remaining=remaining,
            date_key=date_key,
        ),
    )


@router.post("/ai/text-meal/analyze", response_model=AiTextMealAnalyzeResponse)
async def analyze_text_meal_ai(
    request: AiTextMealAnalyzeRequest,
    current_user: AuthenticatedUser = Depends(get_required_authenticated_user),
) -> AiTextMealAnalyzeResponse:
    user_id = current_user.uid
    usage_count, daily_limit, date_key, remaining = await _increment_usage_or_raise(user_id)

    try:
        ingredients = await text_meal_service.analyze_text_meal(
            request.payload,
            lang=request.lang,
        )
    except OpenAIServiceError as exc:
        await _refund_usage_after_ai_failure(
            user_id=user_id,
            date_key=date_key,
            cost=1.0,
            endpoint="/ai/text-meal/analyze",
        )
        raise_service_unavailable(exc, detail="AI service unavailable")

    return AiTextMealAnalyzeResponse(
        ingredients=[AiTextMealIngredient(**ingredient) for ingredient in ingredients],
        **_build_ai_response_fields(
            usage_count=usage_count,
            daily_limit=daily_limit,
            remaining=remaining,
            date_key=date_key,
        ),
    )
