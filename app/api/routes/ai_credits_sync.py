from dataclasses import dataclass
from datetime import datetime
import logging
from typing import Literal, cast
from urllib.parse import quote

import httpx
from fastapi import APIRouter, Depends, HTTPException, status

from app.api.deps import AuthenticatedUser, get_required_authenticated_user
from app.core.config import settings
from app.core.datetime_utils import add_one_month_clamped, parse_flexible_datetime, utc_now
from app.schemas.ai_credits import AiCreditsSyncMetadata, AiCreditsSyncResponse
from app.services import ai_credits_service

router = APIRouter()
logger = logging.getLogger(__name__)


def _as_object_map(value: object) -> dict[str, object] | None:
    if not isinstance(value, dict):
        return None
    raw_map = cast(dict[object, object], value)
    result: dict[str, object] = {}
    for raw_key, raw_item in raw_map.items():
        if isinstance(raw_key, str):
            result[raw_key] = raw_item
    return result


@dataclass(frozen=True)
class RevenueCatActiveEntitlement:
    entitlement_id: str
    anchor_at: datetime
    period_end_at: datetime | None


@dataclass(frozen=True)
class RevenueCatEntitlementDecision:
    status: Literal["active", "confirmed_inactive"]
    active_entitlement: RevenueCatActiveEntitlement | None = None


class RevenueCatMalformedResponse(ValueError):
    pass


def _parse_revenuecat_datetime(
    entitlement: dict[str, object],
    *keys: str,
) -> datetime | None:
    for key in keys:
        if key not in entitlement:
            continue
        raw_value = entitlement.get(key)
        if raw_value is None:
            return None
        parsed = parse_flexible_datetime(raw_value)
        if parsed is None:
            raise RevenueCatMalformedResponse(f"Invalid RevenueCat datetime field: {key}")
        return parsed
    return None


def _resolve_entitlement_decision(
    subscriber: dict[str, object],
) -> RevenueCatEntitlementDecision:
    entitlements = _as_object_map(subscriber.get("entitlements"))
    if entitlements is None:
        raise RevenueCatMalformedResponse(
            "RevenueCat subscriber entitlements are missing or invalid"
        )

    now = utc_now()
    for entitlement_id, entitlement_raw in entitlements.items():
        if not entitlement_id.strip():
            raise RevenueCatMalformedResponse("RevenueCat entitlement ID is empty")
        entitlement_map = _as_object_map(entitlement_raw)
        if entitlement_map is None:
            raise RevenueCatMalformedResponse("RevenueCat entitlement payload is invalid")

        expires_at = _parse_revenuecat_datetime(
            entitlement_map,
            "expires_date",
            "expires_date_ms",
        )
        if expires_at is not None and expires_at <= now:
            continue

        anchor_at = (
            _parse_revenuecat_datetime(
                entitlement_map,
                "purchase_date",
                "purchase_date_ms",
            )
            or _parse_revenuecat_datetime(
                entitlement_map,
                "original_purchase_date",
                "original_purchase_date_ms",
            )
            or now
        )

        return RevenueCatEntitlementDecision(
            status="active",
            active_entitlement=RevenueCatActiveEntitlement(
                entitlement_id=entitlement_id.strip(),
                anchor_at=anchor_at,
                period_end_at=expires_at,
            ),
        )

    return RevenueCatEntitlementDecision(status="confirmed_inactive")


def _build_sync_event_id(
    *,
    user_id: str,
    entitlement_id: str,
    anchor_at: datetime,
    period_end_at: datetime | None,
) -> str:
    anchor_ts = int(anchor_at.timestamp())
    end_ts = int(period_end_at.timestamp()) if period_end_at is not None else 0
    return f"sync:{user_id}:{entitlement_id}:{anchor_ts}:{end_ts}"


async def _fetch_revenuecat_subscriber(user_id: str) -> dict[str, object]:
    api_key = settings.REVENUECAT_API_KEY.strip()
    if not api_key:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="RevenueCat API key is not configured",
        )

    safe_user_id = quote(user_id, safe="")
    url = f"https://api.revenuecat.com/v1/subscribers/{safe_user_id}"
    headers = {"Authorization": f"Bearer {api_key}"}

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(url, headers=headers)
    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="RevenueCat sync unavailable",
        ) from exc

    if response.status_code == status.HTTP_404_NOT_FOUND:
        return {"subscriber": {"entitlements": {}}}
    if response.status_code >= status.HTTP_400_BAD_REQUEST:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="RevenueCat sync unavailable",
        )

    try:
        payload_raw = response.json()
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Invalid RevenueCat response",
        ) from exc
    payload = _as_object_map(payload_raw)
    if payload is None:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Invalid RevenueCat response",
        )
    return payload


@router.post("/ai/credits/sync-tier", response_model=AiCreditsSyncResponse)
async def sync_ai_credits_tier(
    current_user: AuthenticatedUser = Depends(get_required_authenticated_user),
) -> AiCreditsSyncResponse:
    user_id = current_user.uid
    revenuecat_payload = await _fetch_revenuecat_subscriber(user_id)
    subscriber_data = _as_object_map(revenuecat_payload.get("subscriber"))
    if subscriber_data is None:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Invalid RevenueCat response",
        )

    try:
        entitlement_decision = _resolve_entitlement_decision(subscriber_data)
    except RevenueCatMalformedResponse as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Invalid RevenueCat response",
        ) from exc

    current_status = await ai_credits_service.get_credits_status(user_id)
    active_entitlement = entitlement_decision.active_entitlement
    if entitlement_decision.status == "confirmed_inactive":
        if current_status.tier == "premium":
            status_after_sync = await ai_credits_service.apply_premium_expiration(
                user_id,
                anchor_at=utc_now(),
                event_id=f"sync-expiration:{user_id}:{int(current_status.periodEndAt.timestamp())}",
            )
            sync_action: Literal["activated_premium", "expired_to_free", "kept_current"] = (
                "expired_to_free"
            )
        else:
            status_after_sync = current_status
            sync_action = "kept_current"
    else:
        if active_entitlement is None:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="Invalid RevenueCat response",
            )
        resolved_period_end_at = active_entitlement.period_end_at or add_one_month_clamped(
            active_entitlement.anchor_at
        )
        status_after_sync = await ai_credits_service.apply_premium_activation(
            user_id,
            anchor_at=active_entitlement.anchor_at,
            period_end_at=resolved_period_end_at,
            event_id=_build_sync_event_id(
                user_id=user_id,
                entitlement_id=active_entitlement.entitlement_id,
                anchor_at=active_entitlement.anchor_at,
                period_end_at=resolved_period_end_at,
            ),
            entitlement_id=active_entitlement.entitlement_id,
        )
        sync_action = "activated_premium"

    logger.info(
        "revenuecat_sync_tier_reconciled",
        extra={
            "user_id": user_id,
            "had_active_entitlement": entitlement_decision.status == "active",
            "entitlement_status": entitlement_decision.status,
            "sync_action": sync_action,
            "previous_tier": current_status.tier,
            "result_tier": status_after_sync.tier,
            "entitlement_id": (
                active_entitlement.entitlement_id if active_entitlement is not None else None
            ),
        },
    )

    return AiCreditsSyncResponse(
        **status_after_sync.model_dump(),
        syncStatus=AiCreditsSyncMetadata(
            entitlementStatus=entitlement_decision.status,
            syncAction=sync_action,
            entitlementId=active_entitlement.entitlement_id if active_entitlement else None,
        ),
    )
