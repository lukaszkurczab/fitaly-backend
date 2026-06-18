from fastapi.testclient import TestClient
from pytest_mock import MockerFixture

from app.core.exceptions import FirestoreServiceError
from app.main import app
from app.schemas.known_patterns import (
    KnownPatternCandidate,
    KnownPatternCandidateControl,
    KnownPatternCandidateControlResponse,
    KnownPatternCandidateQueryEcho,
    KnownPatternCandidatesResponse,
    KnownPatternExplanation,
    KnownPatternReviewDraft,
    KnownPatternReviewDraftResponse,
    KnownPatternSourceRef,
)
from app.schemas.meal import MealIngredient, MealTotals
from app.services.known_pattern_service import (
    KnownPatternMutationDedupeConflictError,
    KnownPatternNotFoundError,
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


def _control_response(*, updated: bool = True) -> dict[str, object]:
    return {
        "document": KnownPatternCandidateControl(
            controlId="control-hash-1",
            candidateId="candidate-hash",
            subjectKeyHash="subject-hash",
            state="declined",
            createdByRuleVersion="known-pattern-v1",
            expiresAt="2026-06-17T07:40:00.000Z",
            createdAt="2026-06-10T07:40:00.000Z",
            updatedAt="2026-06-10T07:40:00.000Z",
        ).model_dump(),
        "applied": updated,
    }


def _review_draft_response(*, updated: bool = True) -> dict[str, object]:
    control = KnownPatternCandidateControl(
        controlId="control-hash-1",
        candidateId="candidate-hash",
        subjectKeyHash="subject-hash",
        state="shown",
        createdByRuleVersion="known-pattern-v1",
        expiresAt="2026-06-17T07:40:00.000Z",
        createdAt="2026-06-10T07:40:00.000Z",
        updatedAt="2026-06-10T07:40:00.000Z",
    ).model_dump()
    return {
        "draft": KnownPatternReviewDraft(
            name="Owsianka z owocami",
            type="breakfast",
            ingredients=[
                MealIngredient(
                    id="ingredient-1",
                    name="Płatki owsiane",
                    amount=50,
                    unit="g",
                    kcal=180,
                    protein=6,
                    fat=3,
                    carbs=32,
                )
            ],
            totals=MealTotals(kcal=180, protein=6, fat=3, carbs=32),
            notes=None,
            tags=[],
        ),
        "control": control,
        "applied": updated,
    }


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


def test_known_pattern_candidate_control_updates_explicit_state(
    mocker: MockerFixture,
    auth_headers: AuthHeaders,
) -> None:
    mark_control = mocker.patch(
        "app.api.v2.endpoints.known_patterns.mark_known_pattern_candidate_control_for_user",
        return_value=_control_response(),
    )

    response = client.post(
        "/api/v2/users/me/known-patterns/candidates/candidate-hash/control",
        headers=auth_headers("known-pattern-user-1"),
        json={
            "clientMutationId": "mutation-1",
            "subjectKeyHash": "subject-hash",
            "createdByRuleVersion": "known-pattern-v1",
            "action": "declined",
        },
    )

    assert response.status_code == 200
    assert response.json() == KnownPatternCandidateControlResponse(
        control=KnownPatternCandidateControl.model_validate(
            _control_response()["document"]
        ),
        updated=True,
    ).model_dump()
    mark_control.assert_awaited_once()
    await_args = mark_control.await_args
    assert await_args is not None
    assert await_args.args[0] == "known-pattern-user-1"
    assert await_args.args[1] == "candidate-hash"


def test_known_pattern_review_draft_returns_editable_draft_without_route_meal_write(
    mocker: MockerFixture,
    auth_headers: AuthHeaders,
) -> None:
    open_review_draft = mocker.patch(
        "app.api.v2.endpoints.known_patterns.open_known_pattern_review_draft_for_user",
        return_value=_review_draft_response(),
    )

    response = client.post(
        "/api/v2/users/me/known-patterns/candidates/candidate-hash/review-draft",
        headers=auth_headers("known-pattern-user-1"),
        json={
            "clientMutationId": "mutation-review-1",
            "subjectKeyHash": "subject-hash",
            "createdByRuleVersion": "known-pattern-v1",
        },
    )

    assert response.status_code == 200
    assert response.json() == KnownPatternReviewDraftResponse(
        draft=KnownPatternReviewDraft.model_validate(_review_draft_response()["draft"]),
        control=KnownPatternCandidateControl.model_validate(
            _review_draft_response()["control"]
        ),
        updated=True,
    ).model_dump()
    assert response.json()["draft"]["name"] == "Owsianka z owocami"
    open_review_draft.assert_awaited_once()


def test_known_pattern_candidate_control_not_found_returns_404(
    mocker: MockerFixture,
    auth_headers: AuthHeaders,
) -> None:
    mocker.patch(
        "app.api.v2.endpoints.known_patterns.mark_known_pattern_candidate_control_for_user",
        side_effect=KnownPatternNotFoundError("Known Pattern candidate was not found"),
    )

    response = client.post(
        "/api/v2/users/me/known-patterns/candidates/missing/control",
        headers=auth_headers("known-pattern-user-1"),
        json={
            "clientMutationId": "mutation-1",
            "subjectKeyHash": "subject-hash",
            "createdByRuleVersion": "known-pattern-v1",
            "action": "declined",
        },
    )

    assert response.status_code == 404
    assert response.json() == {"detail": "Known Pattern candidate was not found"}


def test_known_pattern_review_draft_mutation_conflict_returns_409(
    mocker: MockerFixture,
    auth_headers: AuthHeaders,
) -> None:
    mocker.patch(
        "app.api.v2.endpoints.known_patterns.open_known_pattern_review_draft_for_user",
        side_effect=KnownPatternMutationDedupeConflictError("clientMutationId conflict"),
    )

    response = client.post(
        "/api/v2/users/me/known-patterns/candidates/candidate-hash/review-draft",
        headers=auth_headers("known-pattern-user-1"),
        json={
            "clientMutationId": "mutation-review-1",
            "subjectKeyHash": "subject-hash",
            "createdByRuleVersion": "known-pattern-v1",
        },
    )

    assert response.status_code == 409
    assert response.json() == {"detail": "clientMutationId conflict"}
