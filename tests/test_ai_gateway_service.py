from app.services.ai_gateway_service import evaluate_request


def test_evaluate_request_forwards_when_gateway_enabled() -> None:
    result = evaluate_request("user-1", "chat", "Jaka bedzie pogoda jutro?")

    assert result == {
        "decision": "FORWARD",
        "reason": "PASS_THROUGH",
        "score": 1.0,
        "credit_cost": 1.0,
    }


def test_evaluate_request_forwards_when_gateway_disabled(mocker) -> None:
    mocker.patch("app.services.ai_gateway_service.settings.AI_GATEWAY_ENABLED", False)

    result = evaluate_request("user-1", "chat", "hej")

    assert result == {
        "decision": "FORWARD",
        "reason": "GATEWAY_DISABLED",
        "score": 1.0,
        "credit_cost": 1.0,
    }
