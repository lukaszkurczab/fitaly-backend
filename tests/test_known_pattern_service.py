from __future__ import annotations

from datetime import datetime, timezone
import inspect

from app.services import known_pattern_service
from app.services.known_pattern_service import evaluate_known_pattern_candidates


def _meal(
    meal_id: str,
    *,
    name: str = "Owsianka z owocami",
    day_key: str = "2026-06-01",
    logged_at: str = "2026-06-01T07:30:00.000Z",
    deleted: bool = False,
) -> dict[str, object]:
    return {
        "id": meal_id,
        "type": "breakfast",
        "name": name,
        "dayKey": day_key,
        "loggedAt": logged_at,
        "deleted": deleted,
        "ingredients": [{"name": "private ingredient"}],
        "notes": "private note",
        "totals": {"kcal": 420, "protein": 18, "fat": 12, "carbs": 56},
    }


def test_known_pattern_candidates_require_three_distinct_days() -> None:
    response = evaluate_known_pattern_candidates(
        [
            _meal("meal-1", day_key="2026-06-01", logged_at="2026-06-01T07:30:00Z"),
            _meal("meal-2", day_key="2026-06-02", logged_at="2026-06-02T07:30:00Z"),
            _meal("meal-3", day_key="2026-06-02", logged_at="2026-06-02T08:30:00Z"),
        ]
    )

    assert response.items == []
    assert response.queryEcho.returnedCandidates == 0


def test_known_pattern_candidates_return_bounded_repeated_meal_candidate() -> None:
    response = evaluate_known_pattern_candidates(
        [
            _meal("meal-1", day_key="2026-06-01", logged_at="2026-06-01T07:30:00Z"),
            _meal("meal-2", day_key="2026-06-02", logged_at="2026-06-02T07:35:00Z"),
            _meal("meal-3", day_key="2026-06-03", logged_at="2026-06-03T07:40:00Z"),
        ],
        now=datetime(2026, 6, 10, tzinfo=timezone.utc),
    )

    assert response.queryEcho.returnedCandidates == 1
    candidate = response.items[0]
    assert candidate.candidateType == "repeated_meal_snapshot"
    assert candidate.state == "candidate"
    assert candidate.confidenceBucket == "medium"
    assert candidate.sourceCountBucket == "3_4"
    assert candidate.distinctDayCountBucket == "3_4"
    assert candidate.suggestedAction == "open_review_draft"
    assert candidate.explanation.reasonCode == "repeated_meal_recent_distinct_days"
    assert len(candidate.sourceRefs) == 3

    payload = response.model_dump_json()
    assert "Owsianka" not in payload
    assert "private ingredient" not in payload
    assert "private note" not in payload
    assert "kcal" not in payload
    assert "meal-1" not in payload


def test_known_pattern_candidates_ignore_deleted_and_missing_name_meals() -> None:
    response = evaluate_known_pattern_candidates(
        [
            _meal("meal-1", day_key="2026-06-01", logged_at="2026-06-01T07:30:00Z"),
            _meal("meal-2", day_key="2026-06-02", logged_at="2026-06-02T07:35:00Z"),
            _meal(
                "meal-3",
                day_key="2026-06-03",
                logged_at="2026-06-03T07:40:00Z",
                deleted=True,
            ),
            _meal(
                "meal-4",
                name=" ",
                day_key="2026-06-04",
                logged_at="2026-06-04T07:40:00Z",
            ),
        ]
    )

    assert response.items == []


def test_known_pattern_candidates_do_not_return_expired_current_suggestions() -> None:
    response = evaluate_known_pattern_candidates(
        [
            _meal("meal-1", day_key="2026-05-01", logged_at="2026-05-01T07:30:00Z"),
            _meal("meal-2", day_key="2026-05-02", logged_at="2026-05-02T07:35:00Z"),
            _meal("meal-3", day_key="2026-05-03", logged_at="2026-05-03T07:40:00Z"),
        ],
        now=datetime(2026, 6, 18, tzinfo=timezone.utc),
    )

    assert response.items == []
    assert response.queryEcho.returnedCandidates == 0


def test_known_pattern_service_stays_deterministic_and_read_only() -> None:
    service_source = inspect.getsource(known_pattern_service)

    assert "openai" not in service_source.casefold()
    assert "upsert_meal" not in service_source
    assert "mark_deleted" not in service_source
    assert ".set(" not in service_source
    assert ".update(" not in service_source
    assert ".delete(" not in service_source
