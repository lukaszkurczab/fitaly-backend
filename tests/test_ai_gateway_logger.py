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
