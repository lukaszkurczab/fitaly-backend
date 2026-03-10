"""Integration tests for the AI ask endpoint with mocked dependencies and Firestore logs."""

from unittest.mock import MagicMock

from fastapi.testclient import TestClient
from pytest_mock import MockerFixture

from app.core.config import settings
from app.core.exceptions import (
    AiUsageLimitExceededError,
    FirestoreServiceError,
    OpenAIServiceError,
)
from app.main import app

client = TestClient(app)


def _mock_gateway_firestore(mocker: MockerFixture) -> tuple[MagicMock, MagicMock]:
    firestore_client = mocker.Mock()
    collection_ref = mocker.Mock()
    firestore_client.collection.return_value = collection_ref
    mocker.patch(
        "app.services.ai_gateway_logger.get_firestore",
        return_value=firestore_client,
    )
    return firestore_client, collection_ref


def test_post_ai_ask_returns_reply_and_logs_gateway_document(
    mocker: MockerFixture,
    auth_headers,
) -> None:
    evaluate_request = mocker.patch(
        "app.api.routes.ai.ai_gateway_service.evaluate_request",
        return_value={
            "decision": "FORWARD",
            "reason": "PASS_THROUGH",
            "score": 1.0,
            "credit_cost": 1.0,
        },
    )
    firestore_client, collection_ref = _mock_gateway_firestore(mocker)
    sanitize_context = mocker.patch(
        "app.api.routes.ai.sanitization_service.sanitize_context",
        return_value={"weightKg": "70-80"},
    )
    sanitize_request = mocker.patch(
        "app.api.routes.ai.sanitization_service.sanitize_request",
        return_value="sanitized prompt",
    )
    build_chat_prompt = mocker.patch(
        "app.api.routes.ai.ai_chat_prompt_service.build_chat_prompt",
        return_value="chat prompt",
    )
    increment_usage = mocker.patch(
        "app.api.routes.ai.ai_usage_service.increment_usage",
        return_value=(4.0, 20, "2026-03-02", 16.0),
    )
    ask_chat = mocker.patch(
        "app.api.routes.ai.openai_service.ask_chat",
        return_value="Try grilled chicken with rice.",
    )

    response = client.post(
        "/api/v1/ai/ask",
        json={
            "message": "Suggest a dinner",
            "context": {"weightKg": 78},
        },
        headers=auth_headers("abc"),
    )

    assert response.status_code == 200
    assert response.json() == {
        "reply": "Try grilled chicken with rice.",
        "usageCount": 4.0,
        "dailyLimit": 20,
        "remaining": 16.0,
        "dateKey": "2026-03-02",
        "version": settings.VERSION,
        "persistence": "backend_owned",
    }
    evaluate_request.assert_called_once_with("abc", "chat", "Suggest a dinner", language="pl")
    sanitize_context.assert_called_once_with({"weightKg": 78})
    sanitize_request.assert_called_once_with("Suggest a dinner", {"weightKg": "70-80"})
    build_chat_prompt.assert_called_once_with(
        "sanitized prompt",
        {"weightKg": "70-80"},
        language="pl",
    )
    increment_usage.assert_called_once_with("abc", cost=1.0)
    ask_chat.assert_called_once_with("chat prompt")
    firestore_client.collection.assert_called_once_with("ai_gateway_logs")
    collection_ref.add.assert_called_once()
    logged_doc = collection_ref.add.call_args.args[0]
    assert logged_doc["userId"] == "abc"
    assert logged_doc["decision"] == "FORWARD"
    assert logged_doc["reason"] == "PASS_THROUGH"
    assert logged_doc["creditCost"] == 1.0
    assert logged_doc["length"] == len("Suggest a dinner")
    assert logged_doc["actionType"] == "chat"


def test_post_ai_ask_requires_required_fields(auth_headers) -> None:
    response = client.post(
        "/api/v1/ai/ask",
        json={},
        headers=auth_headers("abc"),
    )

    assert response.status_code == 422


def test_post_ai_ask_returns_429_when_limit_is_exceeded(
    mocker: MockerFixture,
    auth_headers,
) -> None:
    mocker.patch(
        "app.api.routes.ai.ai_gateway_service.evaluate_request",
        return_value={
            "decision": "FORWARD",
            "reason": "PASS_THROUGH",
            "score": 1.0,
            "credit_cost": 1.0,
        },
    )
    mocker.patch(
        "app.api.routes.ai.sanitization_service.sanitize_context",
        return_value=None,
    )
    mocker.patch(
        "app.api.routes.ai.sanitization_service.sanitize_request",
        return_value="sanitized prompt",
    )
    mocker.patch(
        "app.api.routes.ai.ai_usage_service.increment_usage",
        side_effect=AiUsageLimitExceededError("limit"),
    )
    mocker.patch(
        "app.api.routes.ai.ai_usage_service.get_usage",
        return_value=(20.0, 20, "2026-03-02"),
    )

    response = client.post(
        "/api/v1/ai/ask",
        json={"message": "Suggest a dinner"},
        headers=auth_headers("abc"),
    )

    assert response.status_code == 429
    assert response.json() == {
        "detail": {
            "message": "AI usage limit exceeded",
            "code": "AI_USAGE_LIMIT_EXCEEDED",
            "usage": {
                "dateKey": "2026-03-02",
                "usageCount": 20.0,
                "dailyLimit": 20,
                "remaining": 0.0,
            },
        }
    }


def test_post_ai_ask_returns_500_when_firestore_fails(
    mocker: MockerFixture,
    auth_headers,
) -> None:
    mocker.patch(
        "app.api.routes.ai.ai_gateway_service.evaluate_request",
        return_value={
            "decision": "FORWARD",
            "reason": "PASS_THROUGH",
            "score": 1.0,
            "credit_cost": 1.0,
        },
    )
    mocker.patch(
        "app.api.routes.ai.sanitization_service.sanitize_context",
        return_value=None,
    )
    mocker.patch(
        "app.api.routes.ai.sanitization_service.sanitize_request",
        return_value="sanitized prompt",
    )
    mocker.patch(
        "app.api.routes.ai.ai_usage_service.increment_usage",
        side_effect=FirestoreServiceError("db down"),
    )

    response = client.post(
        "/api/v1/ai/ask",
        json={"message": "Suggest a dinner"},
        headers=auth_headers("abc"),
    )

    assert response.status_code == 500
    assert response.json() == {"detail": "Database error"}


def test_post_ai_ask_returns_503_when_openai_fails(
    mocker: MockerFixture,
    auth_headers,
) -> None:
    mocker.patch(
        "app.api.routes.ai.ai_gateway_service.evaluate_request",
        return_value={
            "decision": "FORWARD",
            "reason": "PASS_THROUGH",
            "score": 1.0,
            "credit_cost": 1.0,
        },
    )
    _mock_gateway_firestore(mocker)
    mocker.patch(
        "app.api.routes.ai.sanitization_service.sanitize_context",
        return_value=None,
    )
    mocker.patch(
        "app.api.routes.ai.sanitization_service.sanitize_request",
        return_value="sanitized prompt",
    )
    mocker.patch(
        "app.api.routes.ai.ai_chat_prompt_service.build_chat_prompt",
        return_value="chat prompt",
    )
    mocker.patch(
        "app.api.routes.ai.ai_usage_service.increment_usage",
        return_value=(1.0, 20, "2026-03-02", 19.0),
    )
    mocker.patch(
        "app.api.routes.ai.openai_service.ask_chat",
        side_effect=OpenAIServiceError("unavailable"),
    )
    refund_usage = mocker.patch(
        "app.api.routes.ai.ai_usage_service.decrement_usage",
        return_value=(0.0, 20, "2026-03-02", 20.0),
    )

    response = client.post(
        "/api/v1/ai/ask",
        json={"message": "Suggest a dinner"},
        headers=auth_headers("abc"),
    )

    assert response.status_code == 503
    assert response.json() == {"detail": "AI service unavailable"}
    refund_usage.assert_called_once_with("abc", cost=1.0, date_key="2026-03-02")


def test_post_ai_ask_returns_400_when_gateway_blocks_request(
    mocker: MockerFixture,
    auth_headers,
) -> None:
    mocker.patch("app.api.routes.ai.ai_gateway_logger.log_gateway_decision")
    mocker.patch(
        "app.api.routes.ai.ai_gateway_service.evaluate_request",
        return_value={
            "decision": "REJECT",
            "reason": "OFF_TOPIC",
            "score": 0.12,
            "credit_cost": 0.2,
        },
    )
    increment_usage = mocker.patch("app.api.routes.ai.ai_usage_service.increment_usage")
    ask_chat = mocker.patch("app.api.routes.ai.openai_service.ask_chat")

    response = client.post(
        "/api/v1/ai/ask",
        json={"message": "Jaka bedzie pogoda jutro?"},
        headers=auth_headers("abc"),
    )

    assert response.status_code == 400
    assert response.json() == {
        "detail": {
            "message": "AI request blocked by gateway",
            "code": "AI_GATEWAY_BLOCKED",
            "reason": "OFF_TOPIC",
            "score": 0.12,
        }
    }
    increment_usage.assert_not_called()
    ask_chat.assert_not_called()


def test_post_ai_ask_forwards_off_topic_like_message_to_openai(
    mocker: MockerFixture,
    auth_headers,
) -> None:
    mocker.patch(
        "app.api.routes.ai.ai_gateway_service.evaluate_request",
        return_value={
            "decision": "FORWARD",
            "reason": "PASS_THROUGH",
            "score": 1.0,
            "credit_cost": 1.0,
        },
    )
    mocker.patch(
        "app.api.routes.ai.sanitization_service.sanitize_context",
        return_value=None,
    )
    mocker.patch(
        "app.api.routes.ai.sanitization_service.sanitize_request",
        return_value="sanitized prompt",
    )
    mocker.patch(
        "app.api.routes.ai.ai_chat_prompt_service.build_chat_prompt",
        return_value="chat prompt",
    )
    increment_usage = mocker.patch(
        "app.api.routes.ai.ai_usage_service.increment_usage",
        return_value=(1.0, 20, "2026-03-02", 19.0),
    )
    _firestore_client, collection_ref = _mock_gateway_firestore(mocker)
    ask_chat = mocker.patch(
        "app.api.routes.ai.openai_service.ask_chat",
        return_value="To pytanie jest poza zakresem tego czatu. Moge pomoc tylko w tematach zywienia, diety i posilkow.",
    )

    response = client.post(
        "/api/v1/ai/ask",
        json={"message": "Jaka bedzie pogoda jutro?"},
        headers=auth_headers("abc"),
    )

    assert response.status_code == 200
    assert response.json()["reply"].startswith("To pytanie jest poza zakresem tego czatu.")
    increment_usage.assert_called_once_with("abc", cost=1.0)
    ask_chat.assert_called_once_with("chat prompt")
    collection_ref.add.assert_called_once()
    logged_doc = collection_ref.add.call_args.args[0]
    assert logged_doc["userId"] == "abc"
    assert logged_doc["decision"] == "FORWARD"
    assert logged_doc["reason"] == "PASS_THROUGH"
    assert logged_doc["creditCost"] == 1.0


def test_post_ai_ask_forwards_simple_calorie_query_to_openai(
    mocker: MockerFixture,
    auth_headers,
) -> None:
    mocker.patch(
        "app.api.routes.ai.ai_gateway_service.evaluate_request",
        return_value={
            "decision": "FORWARD",
            "reason": "PASS_THROUGH",
            "score": 1.0,
            "credit_cost": 1.0,
        },
    )
    mocker.patch(
        "app.api.routes.ai.sanitization_service.sanitize_context",
        return_value=None,
    )
    mocker.patch(
        "app.api.routes.ai.sanitization_service.sanitize_request",
        return_value="sanitized prompt",
    )
    mocker.patch(
        "app.api.routes.ai.ai_chat_prompt_service.build_chat_prompt",
        return_value="chat prompt",
    )
    increment_usage = mocker.patch(
        "app.api.routes.ai.ai_usage_service.increment_usage",
        return_value=(1.0, 20, "2026-03-02", 19.0),
    )
    _mock_gateway_firestore(mocker)
    ask_chat = mocker.patch(
        "app.api.routes.ai.openai_service.ask_chat",
        return_value="Jablko (100 g) ma okolo 52 kcal.",
    )

    response = client.post(
        "/api/v1/ai/ask",
        json={"message": "Ile kalorii ma jablko?"},
        headers=auth_headers("abc"),
    )

    assert response.status_code == 200
    assert response.json() == {
        "reply": "Jablko (100 g) ma okolo 52 kcal.",
        "usageCount": 1.0,
        "dailyLimit": 20,
        "remaining": 19.0,
        "dateKey": "2026-03-02",
        "version": settings.VERSION,
        "persistence": "backend_owned",
    }
    increment_usage.assert_called_once_with("abc", cost=1.0)
    ask_chat.assert_called_once_with("chat prompt")


def test_post_ai_ask_skips_gateway_evaluation_when_feature_flag_is_disabled(
    mocker: MockerFixture,
    auth_headers,
) -> None:
    evaluate_request = mocker.patch("app.api.routes.ai.ai_gateway_service.evaluate_request")
    _mock_gateway_firestore(mocker)
    mocker.patch(
        "app.api.routes.ai.sanitization_service.sanitize_context",
        return_value=None,
    )
    mocker.patch(
        "app.api.routes.ai.sanitization_service.sanitize_request",
        return_value="sanitized prompt",
    )
    mocker.patch(
        "app.api.routes.ai.ai_chat_prompt_service.build_chat_prompt",
        return_value="chat prompt",
    )
    mocker.patch("app.api.routes.ai.settings.AI_GATEWAY_ENABLED", False)
    increment_usage = mocker.patch(
        "app.api.routes.ai.ai_usage_service.increment_usage",
        return_value=(1.0, 20, "2026-03-02", 19.0),
    )
    ask_chat = mocker.patch(
        "app.api.routes.ai.openai_service.ask_chat",
        return_value="Try grilled chicken with rice.",
    )

    response = client.post(
        "/api/v1/ai/ask",
        json={"message": "Suggest a dinner"},
        headers=auth_headers("abc"),
    )

    assert response.status_code == 200
    evaluate_request.assert_not_called()
    increment_usage.assert_called_once_with("abc", cost=1.0)
    ask_chat.assert_called_once_with("chat prompt")


def test_post_ai_ask_bypasses_gateway_for_non_chat_action_type(
    mocker: MockerFixture,
    auth_headers,
) -> None:
    evaluate_request = mocker.patch("app.api.routes.ai.ai_gateway_service.evaluate_request")
    _firestore_client, collection_ref = _mock_gateway_firestore(mocker)
    mocker.patch(
        "app.api.routes.ai.sanitization_service.sanitize_context",
        return_value={"actionType": "meal_text_analysis", "lang": "en"},
    )
    mocker.patch(
        "app.api.routes.ai.sanitization_service.sanitize_request",
        return_value="sanitized prompt",
    )
    increment_usage = mocker.patch(
        "app.api.routes.ai.ai_usage_service.increment_usage",
        return_value=(1.0, 20, "2026-03-02", 19.0),
    )
    ask_chat = mocker.patch(
        "app.api.routes.ai.openai_service.ask_chat",
        return_value='[{"name":"Rice","amount":100,"protein":2,"fat":0,"carbs":28,"kcal":130}]',
    )

    response = client.post(
        "/api/v1/ai/ask",
        json={
            "message": "Analyze this meal payload",
            "context": {
                "actionType": "meal_text_analysis",
                "lang": "en",
            },
        },
        headers=auth_headers("abc"),
    )

    assert response.status_code == 200
    assert response.json()["reply"].startswith("[{\"name\":\"Rice\"")
    evaluate_request.assert_not_called()
    increment_usage.assert_called_once_with("abc", cost=1.0)
    ask_chat.assert_called_once_with("sanitized prompt")
    collection_ref.add.assert_called_once()
    logged_doc = collection_ref.add.call_args.args[0]
    assert logged_doc["actionType"] == "meal_text_analysis"


def test_post_ai_ask_uses_uid_from_token(
    auth_headers,
    mocker: MockerFixture,
) -> None:
    mocker.patch("app.api.routes.ai.ai_gateway_logger.log_gateway_decision")
    mocker.patch(
        "app.api.routes.ai.ai_gateway_service.evaluate_request",
        return_value={
            "decision": "FORWARD",
            "reason": "PASS_THROUGH",
            "score": 1.0,
            "credit_cost": 1.0,
        },
    )
    increment_usage = mocker.patch(
        "app.api.routes.ai.ai_usage_service.increment_usage",
        return_value=(1.0, 20, "2026-03-02", 19.0),
    )
    mocker.patch(
        "app.api.routes.ai.sanitization_service.sanitize_context",
        return_value=None,
    )
    mocker.patch(
        "app.api.routes.ai.sanitization_service.sanitize_request",
        return_value="prompt",
    )
    mocker.patch(
        "app.api.routes.ai.ai_chat_prompt_service.build_chat_prompt",
        return_value="chat prompt",
    )
    mocker.patch(
        "app.api.routes.ai.openai_service.ask_chat",
        return_value="Try grilled chicken with rice.",
    )

    response = client.post(
        "/api/v1/ai/ask",
        json={"message": "Suggest a dinner"},
        headers=auth_headers("other-user"),
    )

    assert response.status_code == 200
    increment_usage.assert_called_once_with("other-user", cost=1.0)
