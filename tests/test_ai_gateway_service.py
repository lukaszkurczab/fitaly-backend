import pytest
from pytest_mock import MockerFixture

from app.services.ai_gateway_service import evaluate_request


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        (
            "hej",
            {
                "decision": "REJECT",
                "reason": "TOO_SHORT",
                "score": -1.0,
                "credit_cost": 0.2,
            },
        ),
        (
            "Ile kalorii ma jablko?",
            {
                "decision": "FORWARD",
                "reason": "OK",
                "score": 1.0,
                "credit_cost": 1.0,
            },
        ),
    ],
)
def test_evaluate_request_returns_expected_decision_for_basic_messages(
    mocker: MockerFixture,
    message: str,
    expected: dict[str, object],
) -> None:
    is_off_topic_mock = mocker.patch(
        "app.services.ai_gateway_service.is_off_topic",
        return_value=False,
    )
    local_answer_mock = mocker.patch(
        "app.services.ai_gateway_service._can_answer_locally",
        return_value=False,
    )

    result = evaluate_request("user-1", "chat", message)

    assert result == expected
    if expected["reason"] == "TOO_SHORT":
        is_off_topic_mock.assert_not_called()
        local_answer_mock.assert_not_called()
    else:
        is_off_topic_mock.assert_called_once_with(message, "pl")
        local_answer_mock.assert_called_once_with(message)


def test_evaluate_request_rejects_off_topic_message(mocker: MockerFixture) -> None:
    is_off_topic_mock = mocker.patch(
        "app.services.ai_gateway_service.is_off_topic",
        return_value=True,
    )
    ml_probability_mock = mocker.patch(
        "app.services.ai_gateway_service._predict_on_topic_probability",
    )

    result = evaluate_request("user-1", "chat", "Jaka bedzie pogoda jutro?")

    assert result == {
        "decision": "REJECT",
        "reason": "OFF_TOPIC",
        "score": -0.8,
        "credit_cost": 0.2,
    }
    is_off_topic_mock.assert_called_once_with("Jaka bedzie pogoda jutro?", "pl")
    ml_probability_mock.assert_not_called()


def test_evaluate_request_forwards_valid_message(mocker: MockerFixture) -> None:
    is_off_topic_mock = mocker.patch(
        "app.services.ai_gateway_service.is_off_topic",
        return_value=False,
    )
    local_answer_mock = mocker.patch(
        "app.services.ai_gateway_service._can_answer_locally",
        return_value=False,
    )
    ml_probability_mock = mocker.patch(
        "app.services.ai_gateway_service._predict_on_topic_probability",
        return_value=None,
    )

    result = evaluate_request("user-1", "chat", "Ile kalorii ma jablko?")

    assert result == {
        "decision": "FORWARD",
        "reason": "OK",
        "score": 1.0,
        "credit_cost": 1.0,
    }
    is_off_topic_mock.assert_called_once_with("Ile kalorii ma jablko?", "pl")
    local_answer_mock.assert_called_once_with("Ile kalorii ma jablko?")
    ml_probability_mock.assert_called_once_with("Ile kalorii ma jablko?")


def test_evaluate_request_forwards_when_gateway_is_disabled(mocker: MockerFixture) -> None:
    mocker.patch("app.services.ai_gateway_service.settings.AI_GATEWAY_ENABLED", False)
    is_off_topic_mock = mocker.patch("app.services.ai_gateway_service.is_off_topic")
    local_answer_mock = mocker.patch("app.services.ai_gateway_service._can_answer_locally")
    ml_probability_mock = mocker.patch("app.services.ai_gateway_service._predict_on_topic_probability")

    result = evaluate_request("user-1", "chat", "hej")

    assert result == {
        "decision": "FORWARD",
        "reason": "GATEWAY_DISABLED",
        "score": 1.0,
        "credit_cost": 1.0,
    }
    is_off_topic_mock.assert_not_called()
    local_answer_mock.assert_not_called()
    ml_probability_mock.assert_not_called()


def test_evaluate_request_rejects_when_ml_marks_message_off_topic(
    mocker: MockerFixture,
) -> None:
    mocker.patch("app.services.ai_gateway_service.is_off_topic", return_value=False)
    mocker.patch("app.services.ai_gateway_service._can_answer_locally", return_value=False)
    mocker.patch("app.services.ai_gateway_service.settings.AI_GATEWAY_ML_ENABLED", True)
    ml_probability_mock = mocker.patch(
        "app.services.ai_gateway_service._predict_on_topic_probability",
        return_value=0.12,
    )

    result = evaluate_request("user-1", "chat", "Podaj przepis na obiad")

    assert result == {
        "decision": "REJECT",
        "reason": "ML_OFF_TOPIC",
        "score": 0.12,
        "credit_cost": 0.2,
    }
    ml_probability_mock.assert_called_once_with("Podaj przepis na obiad")


def test_evaluate_request_returns_local_answer_for_simple_calorie_query(
    mocker: MockerFixture,
) -> None:
    mocker.patch("app.services.ai_gateway_service.is_off_topic", return_value=False)
    local_answer_mock = mocker.patch(
        "app.services.ai_gateway_service._can_answer_locally",
        return_value=True,
    )
    ml_probability_mock = mocker.patch(
        "app.services.ai_gateway_service._predict_on_topic_probability",
    )
    mocker.patch("app.services.ai_gateway_service.settings.AI_LOCAL_COST", 0.4)

    result = evaluate_request("user-1", "chat", "Ile kalorii ma banan?")

    assert result == {
        "decision": "LOCAL_ANSWER",
        "reason": "LOCAL_SIMPLE_QUERY",
        "score": 0.7,
        "credit_cost": 0.4,
    }
    local_answer_mock.assert_called_once_with("Ile kalorii ma banan?")
    ml_probability_mock.assert_not_called()
