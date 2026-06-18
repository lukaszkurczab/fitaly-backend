from fastapi.testclient import TestClient
from pytest_mock import MockerFixture

from app.core.exceptions import FirestoreServiceError
from app.main import app
from app.schemas.known_patterns import (
    KnownPatternCandidate,
    KnownPatternCandidateQueryEcho,
    KnownPatternCandidatesResponse,
    KnownPatternExplanation,
    KnownPatternSourceRef,
)
from tests.types import AuthHeaders

client = TestClient(app)


def _response() -> KnownPatternCandidatesResponse:
    return KnownPatternCandidatesResponse(
        items=[
            KnownPatternCandidate(
                candidateId="candidate-hash",
                candidateType="repeated_meal_snapshot",
                subjectKeyHash="subject-hash",
                state="candidate",
                confidenceBucket="medium",
                sourceCountBucket="3_4",
                distinctDayCountBucket="3_4",
                firstSeenAt="2026-06-01T07:30:00.000Z",
                lastSeenAt="2026-06-03T07:40:00.000Z",
                expiresAt="2026-06-17T07:40:00.000Z",
                sourceRefs=[
                    KnownPatternSourceRef(
                        sourceType="meal_snapshot",
                        sourceHash="source-hash-1",
                    )
                ],
                explanation=KnownPatternExplanation(
                    key="knownPattern.explanation.repeatedMealSnapshot",
                    reasonCode="repeated_meal_recent_distinct_days",
                ),
                suggestedAction="open_review_draft",
                createdByRuleVersion="known-pattern-v1",
            )
        ],
        queryEcho=KnownPatternCandidateQueryEcho(
            ruleVersion="known-pattern-v1",
            minSourceCount=3,
            minDistinctDays=3,
            maxHistoryItems=100,
            returnedCandidates=1,
        ),
    )


def test_known_pattern_candidates_require_authentication() -> None:
    response = client.get("/api/v2/users/me/known-patterns/candidates")

    assert response.status_code == 401
    assert response.json() == {"detail": "Authentication required"}


def test_known_pattern_candidates_route_returns_read_only_candidates(
    mocker: MockerFixture,
    auth_headers: AuthHeaders,
) -> None:
    list_candidates = mocker.patch(
        "app.api.v2.endpoints.known_patterns.list_known_pattern_candidates_for_user",
        return_value=_response(),
    )

    response = client.get(
        "/api/v2/users/me/known-patterns/candidates?limit=3",
        headers=auth_headers("known-pattern-user-1"),
    )

    assert response.status_code == 200
    body = response.json()
    assert body["items"][0]["candidateType"] == "repeated_meal_snapshot"
    assert body["items"][0]["suggestedAction"] == "open_review_draft"
    assert "meal" not in body["items"][0]
    list_candidates.assert_awaited_once_with("known-pattern-user-1", limit=3)


def test_known_pattern_candidates_profile_failure_is_explicit_503(
    mocker: MockerFixture,
    auth_headers: AuthHeaders,
) -> None:
    mocker.patch(
        "app.api.v2.endpoints.known_patterns.list_known_pattern_candidates_for_user",
        side_effect=FirestoreServiceError("history unavailable"),
    )

    response = client.get(
        "/api/v2/users/me/known-patterns/candidates",
        headers=auth_headers("known-pattern-user-1"),
    )

    assert response.status_code == 503
    assert response.json() == {
        "detail": "Known pattern candidates are temporarily unavailable"
    }
