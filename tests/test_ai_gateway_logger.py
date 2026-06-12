from collections.abc import Mapping

from pytest_mock import MockerFixture

from app.services import ai_gateway_logger


def _sample_gateway_result() -> dict[str, object]:
    return {
        "decision": "FORWARD",
        "reason": "PASS_THROUGH",
        "score": 1.0,
        "credit_cost": 1.0,
        "request_id": "req-1",
        "action_type": "chat",
        "task_type": "chat",
        "hypothetical_decision": "LOCAL_ANSWER",
        "hypothetical_reason": "TRIVIAL_GREETING",
        "enforced": False,
        "model": "gpt-4o-mini",
        "estimated_tokens": 12,
        "actual_tokens": 9,
        "latency_ms": 111.11,
        "estimated_cost": 1.0,
        "outcome": "FORWARDED",
        "scope_decision": "ALLOW_NUTRITION",
        "retry_count": 0,
        "used_summary": False,
        "truncated": False,
        "cost_charged": 1.0,
    }


def _payload_text(payload: Mapping[str, object]) -> str:
    return " ".join(str(value) for value in payload.values())


def test_log_gateway_decision_emits_observability_and_analytics(
    mocker: MockerFixture,
) -> None:
    observability_logger = mocker.Mock()
    analytics_logger = mocker.Mock()
    mocker.patch.object(ai_gateway_logger, "_OBSERVABILITY_LOGGER", observability_logger)
    mocker.patch.object(ai_gateway_logger, "_ANALYTICS_LOGGER", analytics_logger)

    ai_gateway_logger.log_gateway_decision(
        "user-1",
        "Ile kalorii ma jablko?",
        _sample_gateway_result(),  # type: ignore[arg-type]
        "chat",
        language="pl",
        response_time_ms=123.456,
        execution_time_ms=234.567,
        profile="free",
        tier="free",
        credit_cost=1.0,
        thread_id="thread-1",
    )

    observability_logger.info.assert_called_once()
    analytics_logger.info.assert_called_once()

    observability_args = observability_logger.info.call_args
    assert observability_args.args[0] == ai_gateway_logger.OBSERVABILITY_EVENT_NAME
    observability_payload = observability_args.kwargs["extra"]["context"]
    assert observability_payload["userId"] == "user-1"
    assert observability_payload["requestId"] == "req-1"
    assert observability_payload["threadId"] == "thread-1"
    assert observability_payload["actionType"] == "chat"
    assert observability_payload["messageLength"] == len("Ile kalorii ma jablko?")
    assert observability_payload["decision"] == "FORWARD"
    assert observability_payload["reason"] == "PASS_THROUGH"
    assert observability_payload["responseTimeMs"] == 123.46
    assert observability_payload["executionTimeMs"] == 234.57
    assert observability_payload["estimatedTokens"] == 12
    assert observability_payload["actualTokens"] == 9
    assert observability_payload["latencyMs"] == 111.11
    assert "messageHash" not in observability_payload

    analytics_args = analytics_logger.info.call_args
    assert analytics_args.args[0] == ai_gateway_logger.ANALYTICS_EVENT_NAME
    analytics_payload = analytics_args.kwargs["extra"]["context"]
    assert analytics_payload["eventName"] == ai_gateway_logger.ANALYTICS_EVENT_NAME
    assert analytics_payload["userId"] == "user-1"
    assert analytics_payload["requestId"] == "req-1"
    assert analytics_payload["threadId"] == "thread-1"
    assert analytics_payload["actionType"] == "chat"
    assert analytics_payload["decision"] == "FORWARD"
    assert analytics_payload["reason"] == "PASS_THROUGH"
    assert analytics_payload["outcome"] == "FORWARDED"
    assert analytics_payload["scopeDecision"] == "ALLOW_NUTRITION"
    assert analytics_payload["tier"] == "free"
    assert analytics_payload["creditCost"] == 1.0
    assert analytics_payload["costCharged"] == 1.0
    assert analytics_payload["latencyMs"] == 111.11
    assert "estimatedTokens" not in analytics_payload
    assert "actualTokens" not in analytics_payload
    assert "retryCount" not in analytics_payload
    assert "usedSummary" not in analytics_payload


def test_log_gateway_decision_does_not_emit_raw_user_content(
    mocker: MockerFixture,
) -> None:
    observability_logger = mocker.Mock()
    analytics_logger = mocker.Mock()
    mocker.patch.object(ai_gateway_logger, "_OBSERVABILITY_LOGGER", observability_logger)
    mocker.patch.object(ai_gateway_logger, "_ANALYTICS_LOGGER", analytics_logger)
    raw_message = (
        '{"name":"secret family recipe","notes":"allergic to private ingredient"}'
    )

    ai_gateway_logger.log_gateway_decision(
        "user-1",
        raw_message,
        _sample_gateway_result(),  # type: ignore[arg-type]
        "text_meal_analysis",
        language="en",
    )

    observability_payload = observability_logger.info.call_args.kwargs["extra"]["context"]
    analytics_payload = analytics_logger.info.call_args.kwargs["extra"]["context"]

    assert observability_payload["messageLength"] == len(raw_message)
    assert "message" not in observability_payload
    assert "messageHash" not in observability_payload
    assert "message" not in analytics_payload
    assert "messageLength" not in analytics_payload
    for forbidden in ("secret family recipe", "allergic to private ingredient"):
        assert forbidden not in _payload_text(observability_payload)
        assert forbidden not in _payload_text(analytics_payload)


def test_log_gateway_decision_omits_raw_provider_debug_fields(
    mocker: MockerFixture,
) -> None:
    observability_logger = mocker.Mock()
    analytics_logger = mocker.Mock()
    mocker.patch.object(ai_gateway_logger, "_OBSERVABILITY_LOGGER", observability_logger)
    mocker.patch.object(ai_gateway_logger, "_ANALYTICS_LOGGER", analytics_logger)
    forbidden_fields: dict[str, str | list[str]] = {
        "rawPrompt": "secret-provider-prompt",
        "rawResponse": "secret-provider-response",
        "providerMessages": ["secret-provider-prompt"],
        "fullPayload": "secret-full-payload",
        "rawToolOutput": "secret-tool-dump",
        "rawImage": "secret-raw-image",
        "debug": "secret-debug-log",
        "logs": "secret-debug-log",
    }
    result_with_raw_provider_payload: dict[str, object] = {
        **_sample_gateway_result(),
        **forbidden_fields,
    }

    ai_gateway_logger.log_gateway_decision(
        "user-1",
        "analyze current meal",
        result_with_raw_provider_payload,  # type: ignore[arg-type]
        "photo_meal_analysis",
        language="en",
        response_time_ms=123.456,
        execution_time_ms=234.567,
        profile="free",
        tier="free",
        credit_cost=1.0,
        thread_id="thread-1",
    )

    observability_payload = observability_logger.info.call_args.kwargs["extra"]["context"]
    analytics_payload = analytics_logger.info.call_args.kwargs["extra"]["context"]

    assert observability_payload["requestId"] == "req-1"
    assert observability_payload["model"] == "gpt-4o-mini"
    assert observability_payload["latencyMs"] == 111.11
    assert observability_payload["actualTokens"] == 9
    assert observability_payload["outcome"] == "FORWARDED"
    assert observability_payload["scopeDecision"] == "ALLOW_NUTRITION"
    assert observability_payload["retryCount"] == 0
    assert observability_payload["usedSummary"] is False
    assert observability_payload["truncated"] is False
    assert observability_payload["costCharged"] == 1.0

    assert analytics_payload["requestId"] == "req-1"
    assert analytics_payload["outcome"] == "FORWARDED"
    assert analytics_payload["scopeDecision"] == "ALLOW_NUTRITION"
    assert analytics_payload["costCharged"] == 1.0
    assert analytics_payload["creditCost"] == 1.0
    assert analytics_payload["latencyMs"] == 111.11

    for payload in (observability_payload, analytics_payload):
        payload_text = _payload_text(payload)
        for forbidden_key, forbidden_value in forbidden_fields.items():
            assert forbidden_key not in payload
            if isinstance(forbidden_value, list):
                for sentinel in forbidden_value:
                    assert sentinel not in payload_text
            else:
                assert forbidden_value not in payload_text


def test_log_gateway_decision_falls_back_when_sink_is_unavailable(
    mocker: MockerFixture,
) -> None:
    observability_logger = mocker.Mock()
    observability_logger.info.side_effect = RuntimeError("sink-down")
    analytics_logger = mocker.Mock()
    fallback_logger = mocker.Mock()
    mocker.patch.object(ai_gateway_logger, "_OBSERVABILITY_LOGGER", observability_logger)
    mocker.patch.object(ai_gateway_logger, "_ANALYTICS_LOGGER", analytics_logger)
    mocker.patch.object(ai_gateway_logger, "_FALLBACK_LOGGER", fallback_logger)

    ai_gateway_logger.log_gateway_decision(
        "user-1",
        "abc",
        _sample_gateway_result(),  # type: ignore[arg-type]
        "chat",
        language="pl",
    )

    fallback_logger.warning.assert_called_once()
    warning_args = fallback_logger.warning.call_args
    assert warning_args.args[0] == ai_gateway_logger.SINK_FALLBACK_EVENT_NAME
    fallback_payload = warning_args.kwargs["extra"]["context"]
    assert fallback_payload["sinkEventName"] == ai_gateway_logger.OBSERVABILITY_EVENT_NAME
    assert fallback_payload["errorType"] == "RuntimeError"
    assert fallback_payload["userId"] == "user-1"
    assert fallback_payload["requestId"] == "req-1"
    assert fallback_payload["actionType"] == "chat"
    assert warning_args.kwargs["exc_info"] is True
    analytics_logger.info.assert_called_once()


def test_emit_structured_sink_path_uses_info_level(mocker: MockerFixture) -> None:
    sink_logger = mocker.Mock()
    ai_gateway_logger._emit_structured(  # type: ignore[attr-defined]
        sink_logger=sink_logger,
        event_name="custom.event",
        payload={"requestId": "req-1"},
    )
    sink_logger.info.assert_called_once_with(
        "custom.event",
        extra={"context": {"requestId": "req-1"}},
    )
