from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from app.domain.tools.base import DomainTool


class ResolveTimeScopeTool(DomainTool):
    name = "resolve_time_scope"

    @staticmethod
    def _resolve_today(*, timezone: str, today_override: str | None) -> date:
        if today_override:
            return datetime.fromisoformat(today_override).date()
        return datetime.now(ZoneInfo(timezone)).date()

    async def execute(self, *, user_id: str, args: dict[str, Any]) -> dict[str, Any]:
        del user_id

        label = str(args.get("label") or "today").strip().lower()
        timezone = str(args.get("timezone") or "Europe/Warsaw").strip() or "Europe/Warsaw"
        today = self._resolve_today(
            timezone=timezone,
            today_override=str(args.get("today")).strip() if args.get("today") else None,
        )

        if label == "today":
            return {
                "type": "today",
                "startDate": today.isoformat(),
                "endDate": today.isoformat(),
                "timezone": timezone,
                "isPartial": True,
            }

        if label == "yesterday":
            yesterday = today - timedelta(days=1)
            return {
                "type": "yesterday",
                "startDate": yesterday.isoformat(),
                "endDate": yesterday.isoformat(),
                "timezone": timezone,
                "isPartial": False,
            }

        if label in {"this_week", "calendar_week"}:
            start = today - timedelta(days=today.weekday())
            return {
                "type": "calendar_week",
                "startDate": start.isoformat(),
                "endDate": today.isoformat(),
                "timezone": timezone,
                "isPartial": True,
            }

        if label in {"rolling_7d", "last_7d"}:
            start = today - timedelta(days=6)
            return {
                "type": "rolling_7d",
                "startDate": start.isoformat(),
                "endDate": today.isoformat(),
                "timezone": timezone,
                "isPartial": True,
            }

        if label in {"date_range", "custom"}:
            start_date = str(args.get("startDate") or "").strip()
            end_date = str(args.get("endDate") or "").strip()
            if not start_date or not end_date:
                raise ValueError("date_range requires startDate and endDate")
            start = datetime.fromisoformat(start_date).date()
            end = datetime.fromisoformat(end_date).date()
            if end < start:
                raise ValueError("endDate must be on or after startDate")
            return {
                "type": "date_range",
                "startDate": start.isoformat(),
                "endDate": end.isoformat(),
                "timezone": timezone,
                "isPartial": bool(args.get("isPartial", False)),
            }

        raise ValueError(f"Unsupported scope label: {label}")
