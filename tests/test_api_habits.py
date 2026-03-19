from datetime import UTC, datetime

from fastapi import FastAPI
from fastapi.testclient import TestClient
from pytest_mock import MockerFixture

from app.api.v2.router import router as v2_router
from app.core.exceptions import HabitsDisabledError
from app.schemas.habits import (
    DayCoverage14,
    HabitBehavior,
    HabitDataQuality,
    HabitSignalsResponse,
    HabitTimingPatterns14,
    MealTypeCoverage14,
    MealTypeFrequency14,
    ProteinDaysHit14,
)


def create_test_client() -> TestClient:
    app = FastAPI()
    app.include_router(v2_router, prefix="/api/v2")
    return TestClient(app)


def test_get_user_habits_returns_response_shape(
    mocker: MockerFixture,
    auth_headers,
) -> None:
    mocker.patch(
        "app.api.routes.habits.get_habit_signals",
        return_value=HabitSignalsResponse(
            computedAt=datetime(2026, 3, 18, 12, 0, tzinfo=UTC)
            .isoformat()
            .replace("+00:00", "Z"),
            behavior=HabitBehavior(
                loggingDays7=5,
                validLoggingDays7=4,
                loggingConsistency28=0.6,
                validLoggingConsistency28=0.5,
                avgMealsPerLoggedDay14=2.2,
                avgValidMealsPerValidLoggedDay14=2.0,
                mealTypeCoverage14=MealTypeCoverage14(
                    breakfast=True,
                    lunch=True,
                    dinner=False,
                    snack=True,
                    other=False,
                    coveredCount=3,
                ),
                mealTypeFrequency14=MealTypeFrequency14(
                    breakfast=3,
                    lunch=4,
                    dinner=0,
                    snack=1,
                    other=0,
                ),
                dayCoverage14=DayCoverage14(
                    loggedDays=5,
                    validLoggedDays=4,
                ),
                kcalAdherence14=0.94,
                kcalUnderTargetRatio14=0.3,
                proteinDaysHit14=ProteinDaysHit14(
                    hitDays=4,
                    eligibleDays=5,
                    unknownDays=0,
                    ratio=0.8,
                ),
                timingPatterns14=HabitTimingPatterns14(
                    available=True,
                    observedDays=4,
                    firstMealMedianHour=8.0,
                    lastMealMedianHour=18.5,
                    eatingWindowHoursMedian=10.0,
                    breakfastMedianHour=8.0,
                    lunchMedianHour=13.0,
                    dinnerMedianHour=None,
                    snackMedianHour=16.5,
                    otherMedianHour=None,
                ),
            ),
            dataQuality=HabitDataQuality(
                daysWithUnknownMealDetails14=1,
                daysUsingTimestampDayFallback14=0,
                daysUsingTimestampTimingFallback14=1,
            ),
            topRisk="low_protein_consistency",
            coachPriority="protein_consistency",
        ),
    )
    client = create_test_client()

    response = client.get("/api/v2/users/me/habits", headers=auth_headers("user-1"))

    assert response.status_code == 200
    assert response.json() == {
        "computedAt": "2026-03-18T12:00:00Z",
        "windowDays": {"recentActivity": 7, "adherence": 14, "consistency": 28},
        "behavior": {
            "loggingDays7": 5,
            "validLoggingDays7": 4,
            "loggingConsistency28": 0.6,
            "validLoggingConsistency28": 0.5,
            "avgMealsPerLoggedDay14": 2.2,
            "avgValidMealsPerValidLoggedDay14": 2.0,
            "mealTypeCoverage14": {
                "breakfast": True,
                "lunch": True,
                "dinner": False,
                "snack": True,
                "other": False,
                "coveredCount": 3,
            },
            "mealTypeFrequency14": {
                "breakfast": 3,
                "lunch": 4,
                "dinner": 0,
                "snack": 1,
                "other": 0,
            },
            "dayCoverage14": {
                "loggedDays": 5,
                "validLoggedDays": 4,
            },
            "kcalAdherence14": 0.94,
            "kcalUnderTargetRatio14": 0.3,
            "proteinDaysHit14": {
                "hitDays": 4,
                "eligibleDays": 5,
                "unknownDays": 0,
                "ratio": 0.8,
            },
            "timingPatterns14": {
                "available": True,
                "observedDays": 4,
                "firstMealMedianHour": 8.0,
                "lastMealMedianHour": 18.5,
                "eatingWindowHoursMedian": 10.0,
                "breakfastMedianHour": 8.0,
                "lunchMedianHour": 13.0,
                "dinnerMedianHour": None,
                "snackMedianHour": 16.5,
                "otherMedianHour": None,
            },
        },
        "dataQuality": {
            "daysWithUnknownMealDetails14": 1,
            "daysUsingTimestampDayFallback14": 0,
            "daysUsingTimestampTimingFallback14": 1,
        },
        "topRisk": "low_protein_consistency",
        "coachPriority": "protein_consistency",
    }


def test_get_user_habits_returns_503_when_feature_flag_is_disabled(
    mocker: MockerFixture,
    auth_headers,
) -> None:
    mocker.patch(
        "app.api.routes.habits.get_habit_signals",
        side_effect=HabitsDisabledError("disabled"),
    )
    client = create_test_client()

    response = client.get("/api/v2/users/me/habits", headers=auth_headers("user-1"))

    assert response.status_code == 503
    assert response.json() == {"detail": "Habit signals are disabled"}
