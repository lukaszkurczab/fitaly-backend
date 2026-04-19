from app.domain.tools.resolve_time_scope import ResolveTimeScopeTool


async def test_resolve_time_scope_today_and_week_for_warsaw() -> None:
    tool = ResolveTimeScopeTool()

    today = await tool.execute(
        user_id="user-1",
        args={"label": "today", "timezone": "Europe/Warsaw", "today": "2026-04-19"},
    )
    assert today == {
        "type": "today",
        "startDate": "2026-04-19",
        "endDate": "2026-04-19",
        "timezone": "Europe/Warsaw",
        "isPartial": True,
    }

    this_week = await tool.execute(
        user_id="user-1",
        args={"label": "this_week", "timezone": "Europe/Warsaw", "today": "2026-04-19"},
    )
    assert this_week == {
        "type": "calendar_week",
        "startDate": "2026-04-13",
        "endDate": "2026-04-19",
        "timezone": "Europe/Warsaw",
        "isPartial": True,
    }


async def test_resolve_time_scope_rolling_and_custom_range() -> None:
    tool = ResolveTimeScopeTool()

    rolling = await tool.execute(
        user_id="user-1",
        args={"label": "rolling_7d", "today": "2026-05-10"},
    )
    assert rolling["startDate"] == "2026-05-04"
    assert rolling["endDate"] == "2026-05-10"
    assert rolling["isPartial"] is True

    custom = await tool.execute(
        user_id="user-1",
        args={
            "label": "date_range",
            "startDate": "2026-05-01",
            "endDate": "2026-05-07",
            "timezone": "Europe/Warsaw",
            "isPartial": False,
        },
    )
    assert custom == {
        "type": "date_range",
        "startDate": "2026-05-01",
        "endDate": "2026-05-07",
        "timezone": "Europe/Warsaw",
        "isPartial": False,
    }
