import logging
from time import perf_counter

from fastapi import APIRouter, HTTPException, status
from fastapi.responses import JSONResponse

from app.core.config import settings
from app.core.exceptions import (
    AiUsageLimitExceededError,
    ContentBlockedError,
    FirestoreServiceError,
    OpenAIServiceError,
)
from app.schemas.ai_ask import AiAskRequest, AiAskResponse
from app.schemas.ai_photo import (
    AiPhotoAnalyzeRequest,
    AiPhotoAnalyzeResponse,
    AiPhotoIngredient,
)
from app.services import (
    ai_gateway_logger,
    ai_gateway_service,
    ai_usage_service,
    content_guard_service,
    openai_service,
    sanitization_service,
)

router = APIRouter()
logger = logging.getLogger(__name__)


def _resolve_language(request: AiAskRequest) -> str:
    if request.context:
        for key in ("language", "lang"):
            value = request.context.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    return "pl"


def _get_local_answer(message: str, language: str) -> str:
    del message
    if language.lower() == "en":
        return "This request was handled locally."
    return "To zapytanie zostalo obsluzone lokalnie."


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


@router.post("/ai/ask", response_model=AiAskResponse)
async def ask_ai(request: AiAskRequest) -> AiAskResponse | JSONResponse:
    started_at = perf_counter()

    try:
        content_guard_service.check_allowed(request.message)
    except ContentBlockedError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=str(exc),
        ) from exc

    language = _resolve_language(request)
    gateway_result: ai_gateway_service.GatewayResult = {
        "decision": "FORWARD",
        "reason": "GATEWAY_DISABLED",
        "score": 1.0,
        "credit_cost": 1.0,
    }
    if settings.AI_GATEWAY_ENABLED:
        gateway_result = ai_gateway_service.evaluate_request(
            request.userId,
            "chat",
            request.message,
            language=language,
        )

    sanitized_message = sanitization_service.sanitize_request(request.message, request.context)

    try:
        usage_count, daily_limit, date_key, remaining = await ai_usage_service.increment_usage(
            request.userId,
            cost=gateway_result["credit_cost"],
        )
    except AiUsageLimitExceededError as exc:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="AI usage limit exceeded",
        ) from exc
    except FirestoreServiceError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Database error",
        ) from exc

    if gateway_result["decision"] == "REJECT":
        await _log_gateway_result(
            user_id=request.userId,
            action_type="chat",
            message=request.message,
            language=language,
            result=gateway_result,
            execution_time_ms=(perf_counter() - started_at) * 1000,
        )
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={
                "reason": gateway_result["reason"],
                "credit_cost": gateway_result["credit_cost"],
            },
        )

    if gateway_result["decision"] == "LOCAL_ANSWER":
        await _log_gateway_result(
            user_id=request.userId,
            action_type="chat",
            message=request.message,
            language=language,
            result=gateway_result,
            execution_time_ms=(perf_counter() - started_at) * 1000,
        )
        return AiAskResponse(
            userId=request.userId,
            reply=_get_local_answer(request.message, language),
            usageCount=usage_count,
            remaining=remaining,
            dateKey=date_key,
            version=settings.VERSION,
        )

    try:
        reply = await openai_service.ask_chat(sanitized_message)
    except OpenAIServiceError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="AI service unavailable",
        ) from exc

    await _log_gateway_result(
        user_id=request.userId,
        action_type="chat",
        message=request.message,
        language=language,
        result=gateway_result,
        response_time_ms=(perf_counter() - started_at) * 1000,
        execution_time_ms=(perf_counter() - started_at) * 1000,
    )

    return AiAskResponse(
        userId=request.userId,
        reply=reply,
        usageCount=usage_count,
        remaining=remaining,
        dateKey=date_key,
        version=settings.VERSION,
    )


@router.post("/ai/photo/analyze", response_model=AiPhotoAnalyzeResponse)
async def analyze_photo_ai(request: AiPhotoAnalyzeRequest) -> AiPhotoAnalyzeResponse:
    try:
        usage_count, _daily_limit, date_key, remaining = await ai_usage_service.increment_usage(
            request.userId
        )
    except AiUsageLimitExceededError as exc:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="AI usage limit exceeded",
        ) from exc
    except FirestoreServiceError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Database error",
        ) from exc

    try:
        ingredients = await openai_service.analyze_photo(
            request.imageBase64,
            lang=request.lang,
        )
    except OpenAIServiceError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="AI service unavailable",
        ) from exc

    return AiPhotoAnalyzeResponse(
        userId=request.userId,
        ingredients=[AiPhotoIngredient(**ingredient) for ingredient in ingredients],
        usageCount=usage_count,
        remaining=remaining,
        dateKey=date_key,
        version=settings.VERSION,
    )
