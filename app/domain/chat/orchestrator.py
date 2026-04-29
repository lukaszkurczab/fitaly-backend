"""Canonical AI Chat v2 orchestration lifecycle."""

from __future__ import annotations

from dataclasses import dataclass
from time import perf_counter
from typing import Any, Protocol

from app.domain.ai_runs.models.ai_run import AiRun, RunStatus
from app.domain.ai_runs.services.ai_run_service import AiRunService
from app.core.errors import (
    AiProviderNonRetryableError,
    AiProviderRetryableError,
    ToolExecutionError,
)
from app.domain.chat.context_builder import BudgetResult, ContextBuilder
from app.domain.chat.generator import ChatGenerator, GenerationResult, GenerationUsage
from app.domain.chat.planner import ChatPlanner
from app.domain.chat.prompt_composer import PromptComposer
from app.domain.chat.retry_policy import RetryPolicy
from app.domain.chat_memory.models.chat_message import ChatMessage
from app.domain.chat_memory.models.memory_summary import MemorySummary
from app.domain.chat_memory.services.message_service import MessageService
from app.domain.chat_memory.services.summary_service import SummaryService
from app.domain.chat_memory.services.thread_service import ThreadService
from app.domain.tools.registry import ToolRegistry
from app.domain.users.services.consent_service import ConsentService
from app.schemas.ai_chat.planner import PlannerResultDto
from app.schemas.ai_chat.request import ChatRunRequestDto
from app.schemas.ai_chat.response import ChatRunResponseDto, ContextStatsDto, UsageDto


@dataclass(frozen=True)
class _ToolExecutionResult:
    outputs: dict[str, dict[str, Any]]
    names: list[str]
    metrics: list[dict[str, Any]]
    scope_resolved: str | None


class _ToolLike(Protocol):
    async def execute(self, *, user_id: str, args: dict[str, Any]) -> dict[str, Any]: ...


class ChatOrchestrator:
    def __init__(
        self,
        *,
        consent_service: ConsentService,
        thread_service: ThreadService,
        message_service: MessageService,
        summary_service: SummaryService,
        ai_run_service: AiRunService,
        planner: ChatPlanner,
        tool_registry: ToolRegistry,
        context_builder: ContextBuilder,
        prompt_composer: PromptComposer,
        generator: ChatGenerator,
        retry_policy: RetryPolicy,
        recent_turns_limit: int = 10,
    ) -> None:
        self.consent_service = consent_service
        self.thread_service = thread_service
        self.message_service = message_service
        self.summary_service = summary_service
        self.ai_run_service = ai_run_service
        self.planner = planner
        self.tool_registry = tool_registry
        self.context_builder = context_builder
        self.prompt_composer = prompt_composer
        self.generator = generator
        self.retry_policy = retry_policy
        self.recent_turns_limit = max(2, recent_turns_limit)

    async def run(self, *, user_id: str, request: ChatRunRequestDto) -> ChatRunResponseDto:
        """Execute canonical AI Chat v2 lifecycle for one user request.

        Flow order:
        1. consent gate
        2. idempotency replay lookup
        3. run/thread/message persistence bootstrap
        4. planner
        5. tools
        6. grounding + token budget
        7. generator (+ retry policy)
        8. assistant message + summary refresh
        9. ai_run telemetry finalization
        10. response DTO mapping
        """
        language = self._normalize_language(request.language)

        await self.consent_service.ensure_ai_health_data_consent(user_id=user_id)

        existing_user_message = await self.message_service.find_by_client_message_id(
            user_id=user_id,
            thread_id=request.thread_id,
            client_message_id=request.client_message_id,
        )
        if existing_user_message is not None and existing_user_message.run_id:
            replay = await self._build_idempotent_replay_response(
                user_id=user_id,
                thread_id=request.thread_id,
                user_message=existing_user_message,
            )
            if replay is not None:
                return replay

        run_id = (
            existing_user_message.run_id
            if existing_user_message is not None and existing_user_message.run_id
            else self.ai_run_service.new_run_id()
        )
        started_at = perf_counter()

        await self._ensure_run_started(
            run_id=run_id,
            user_id=user_id,
            thread_id=request.thread_id,
            request=request,
        )

        planner_used = False
        tools_used: list[str] = []
        tool_metrics: list[dict[str, Any]] = []
        usage = GenerationUsage(prompt_tokens=0, completion_tokens=0, total_tokens=0)
        budget = BudgetResult(
            used_summary=False,
            truncated=False,
            history_turns=0,
        )
        reply = ""
        assistant_message_id = ""
        scope_decision = "ALLOW_APP"
        scope_resolved: str | None = None
        retry_count = 0
        run_status: RunStatus = "completed"
        run_outcome: RunStatus = "completed"
        failure_reason: str | None = None
        planner_task_type: str | None = None
        planner_response_mode: str | None = None
        follow_up_required = False

        try:
            await self.thread_service.ensure_thread(
                user_id=user_id,
                thread_id=request.thread_id,
            )
            await self.message_service.create_user_message(
                user_id=user_id,
                thread_id=request.thread_id,
                run_id=run_id,
                client_message_id=request.client_message_id,
                content=request.message,
                language=language,
            )

            recent_turns = await self.message_service.get_recent_turns(
                user_id=user_id,
                thread_id=request.thread_id,
                limit=self.recent_turns_limit,
            )
            memory_summary = await self.summary_service.get_current_summary(
                user_id=user_id,
                thread_id=request.thread_id,
            )

            try:
                planner_result = await self.planner.plan(
                    user_id=user_id,
                    user_message=request.message,
                    recent_turns=recent_turns,
                    memory_summary=memory_summary,
                    language=language,
                )
                planner_used = True
            except Exception as exc:  # noqa: BLE001
                raise self._map_provider_error(exc) from exc

            planner_task_type = planner_result.task_type
            planner_response_mode = planner_result.response_mode
            follow_up_required = planner_result.needs_follow_up
            budget = BudgetResult(
                used_summary=self._has_summary(memory_summary),
                truncated=False,
                history_turns=min(len(recent_turns), self.context_builder.max_recent_turns),
            )

            if planner_result.task_type == "out_of_scope_refusal":
                reply = self.prompt_composer.build_refusal_response(language)
                scope_decision = "DENY_OTHER"
                run_status = "rejected"
                run_outcome = "rejected"
            elif planner_result.needs_follow_up:
                scope_decision = self._derive_scope_decision(planner_result)
                reply = (
                    planner_result.follow_up_question
                    or self._default_follow_up_question(language=language)
                )
            else:
                execution = await self._execute_tools(
                    user_id=user_id,
                    planner_result=planner_result,
                    request=request,
                )
                tools_used = execution.names
                scope_decision = self._derive_scope_decision(planner_result)
                tool_metrics = execution.metrics
                scope_resolved = execution.scope_resolved

                grounding = self.context_builder.build_grounding(
                    planner_result=planner_result,
                    tool_outputs=execution.outputs,
                    recent_turns=recent_turns,
                    memory_summary=memory_summary,
                )
                prompt_input = self.prompt_composer.build_prompt_input(
                    language=language,
                    response_mode=planner_result.response_mode,
                    grounding=grounding,
                    user_message=request.message,
                )
                prompt_messages = self.prompt_composer.compose_messages(prompt_input)
                token_stats = self.context_builder.token_counter.measure_messages(prompt_messages)
                prompt_messages, budget = self.context_builder.enforce_token_budget(
                    messages=prompt_messages,
                    token_stats=token_stats,
                    memory_summary=memory_summary,
                )

                generation, retry_count = await self._generate_with_retry(
                    prompt_messages=prompt_messages
                )
                reply = generation.text
                usage = generation.usage

            assistant_message = await self.message_service.create_assistant_message(
                user_id=user_id,
                thread_id=request.thread_id,
                run_id=run_id,
                content=reply,
                status="completed",
            )
            assistant_message_id = assistant_message.id

            await self.summary_service.maybe_refresh_summary(
                user_id=user_id,
                thread_id=request.thread_id,
                recent_turns=recent_turns,
                user_message=request.message,
                assistant_message=reply,
                previous_summary=memory_summary,
                covered_until_message_id=assistant_message.id,
            )

        except Exception as exc:  # noqa: BLE001
            if isinstance(exc, (AiProviderRetryableError, AiProviderNonRetryableError)):
                retry_count = max(
                    retry_count,
                    self._coerce_int(getattr(exc, "retry_count", retry_count), default=retry_count),
                )
                failure_reason = (
                    "provider_retryable_error"
                    if isinstance(exc, AiProviderRetryableError)
                    else "provider_non_retryable_error"
                )
            elif isinstance(exc, ToolExecutionError):
                failure_reason = "tool_execution_failed"
            else:
                failure_reason = "orchestrator_failed"

            await self.ai_run_service.update_run(
                run_id=run_id,
                status="failed",
                outcome="failed",
                failure_reason=failure_reason,
                planner_used=planner_used,
                tools_used=tools_used,
                tool_metrics=tool_metrics,
                summary_used=budget.used_summary,
                truncated=budget.truncated,
                retry_count=retry_count,
                prompt_tokens=usage.prompt_tokens,
                completion_tokens=usage.completion_tokens,
                total_tokens=usage.total_tokens,
                total_latency_ms=self._elapsed_ms(started_at),
                metadata={
                    "taskType": planner_task_type,
                    "responseMode": planner_response_mode,
                    "scopeResolved": scope_resolved,
                    "historyTurns": budget.history_turns,
                    "followUpRequired": follow_up_required,
                    "scopeDecision": scope_decision,
                    "clientMessageId": request.client_message_id,
                    "threadId": request.thread_id,
                    "language": language,
                },
            )
            raise

        await self.ai_run_service.update_run(
            run_id=run_id,
            status=run_status,
            outcome=run_outcome,
            failure_reason=failure_reason,
            planner_used=planner_used,
            tools_used=tools_used,
            tool_metrics=tool_metrics,
            summary_used=budget.used_summary,
            truncated=budget.truncated,
            retry_count=retry_count,
            prompt_tokens=usage.prompt_tokens,
            completion_tokens=usage.completion_tokens,
            total_tokens=usage.total_tokens,
            total_latency_ms=self._elapsed_ms(started_at),
            metadata={
                "taskType": planner_task_type,
                "responseMode": planner_response_mode,
                "scopeResolved": scope_resolved,
                "historyTurns": budget.history_turns,
                "followUpRequired": follow_up_required,
                "scopeDecision": scope_decision,
                "clientMessageId": request.client_message_id,
                "threadId": request.thread_id,
                "language": language,
            },
        )

        return ChatRunResponseDto(
            runId=run_id,
            threadId=request.thread_id,
            clientMessageId=request.client_message_id,
            assistantMessageId=assistant_message_id,
            reply=reply,
            usage=UsageDto(
                promptTokens=usage.prompt_tokens,
                completionTokens=usage.completion_tokens,
                totalTokens=usage.total_tokens,
            ),
            contextStats=ContextStatsDto(
                usedSummary=budget.used_summary,
                historyTurns=budget.history_turns,
                truncated=budget.truncated,
                scopeDecision=scope_decision,
            ),
            credits=None,
            persistence="backend_owned",
        )

    async def _ensure_run_started(
        self,
        *,
        run_id: str,
        user_id: str,
        thread_id: str,
        request: ChatRunRequestDto,
    ) -> None:
        existing = await self.ai_run_service.get_run(run_id=run_id)
        if existing is not None:
            await self.ai_run_service.update_run(
                run_id=run_id,
                status="started",
                metadata={
                    "clientMessageId": request.client_message_id,
                    "threadId": thread_id,
                    "language": self._normalize_language(request.language),
                },
            )
            return

        await self.ai_run_service.create_run(
            run_id=run_id,
            user_id=user_id,
            thread_id=thread_id,
            status="started",
            metadata={
                "clientMessageId": request.client_message_id,
                "threadId": thread_id,
                "language": self._normalize_language(request.language),
            },
        )

    async def _build_idempotent_replay_response(
        self,
        *,
        user_id: str,
        thread_id: str,
        user_message: ChatMessage,
    ) -> ChatRunResponseDto | None:
        if not user_message.run_id:
            return None

        assistant = await self.message_service.get_assistant_message_by_run_id(
            user_id=user_id,
            thread_id=thread_id,
            run_id=user_message.run_id,
        )
        if assistant is None:
            return None

        run = await self.ai_run_service.get_run(run_id=user_message.run_id)
        return self._response_from_existing_run(
            run_id=user_message.run_id,
            thread_id=thread_id,
            client_message_id=user_message.client_message_id or user_message.id,
            assistant_message=assistant,
            run=run,
        )

    async def _execute_tools(
        self,
        *,
        user_id: str,
        planner_result: PlannerResultDto,
        request: ChatRunRequestDto,
    ) -> _ToolExecutionResult:
        outputs: dict[str, dict[str, Any]] = {}
        names: list[str] = []
        metrics: list[dict[str, Any]] = []
        scope_resolved: str | None = None

        for capability in planner_result.capabilities:
            tool_name = capability.name
            tool: _ToolLike = self.tool_registry.get(tool_name)
            names.append(tool_name)
            started_at = perf_counter()
            try:
                raw_args = capability.args if isinstance(capability.args, dict) else {}
                resolved_args = self.context_builder.resolve_tool_args(
                    raw_args=raw_args,
                    tool_outputs=outputs,
                )
                resolved_args = self._inject_tool_defaults(
                    tool_name=tool_name,
                    args=resolved_args,
                    request=request,
                    tool_outputs=outputs,
                )
                output = await tool.execute(user_id=user_id, args=resolved_args)
            except Exception as exc:  # noqa: BLE001
                metrics.append(
                    {
                        "name": tool_name,
                        "durationMs": self._elapsed_ms(started_at),
                        "success": False,
                    }
                )
                raise ToolExecutionError(
                    f"Tool execution failed for capability '{tool_name}'."
                ) from exc

            metrics.append(
                {
                    "name": tool_name,
                    "durationMs": self._elapsed_ms(started_at),
                    "success": True,
                }
            )
            outputs[tool_name] = output
            if tool_name == "resolve_time_scope" and isinstance(output, dict):
                resolved_scope = output.get("type")
                if isinstance(resolved_scope, str) and resolved_scope.strip():
                    scope_resolved = resolved_scope.strip()

        return _ToolExecutionResult(
            outputs=outputs,
            names=names,
            metrics=metrics,
            scope_resolved=scope_resolved,
        )

    async def _generate_with_retry(
        self,
        *,
        prompt_messages: list[dict[str, str]],
    ) -> tuple[GenerationResult, int]:
        attempts = 0

        async def _invoke() -> GenerationResult:
            nonlocal attempts
            attempts += 1
            return await self.generator.generate(messages=prompt_messages)

        try:
            result = await self.retry_policy.run_with_retry(_invoke)
        except Exception as exc:  # noqa: BLE001
            retry_count = max(0, attempts - 1)
            if self.retry_policy.is_retryable(exc):
                wrapped = AiProviderRetryableError(
                    "AI provider timed out or is temporarily unavailable."
                )
                setattr(wrapped, "retry_count", retry_count)
                raise wrapped from exc
            wrapped = AiProviderNonRetryableError("AI provider request failed.")
            setattr(wrapped, "retry_count", retry_count)
            raise wrapped from exc

        retry_count = max(0, attempts - 1)
        return result, retry_count

    def _inject_tool_defaults(
        self,
        *,
        tool_name: str,
        args: dict[str, Any],
        request: ChatRunRequestDto,
        tool_outputs: dict[str, dict[str, Any]],
    ) -> dict[str, Any]:
        normalized = dict(args)

        if tool_name == "resolve_time_scope":
            normalized.setdefault("label", "today")
            normalized.setdefault("timezone", "Europe/Warsaw")

        if tool_name == "get_recent_chat_summary":
            normalized.setdefault("threadId", request.thread_id)
            normalized.setdefault("fallbackTurnsLimit", 6)

        scope = tool_outputs.get("resolve_time_scope")
        if isinstance(scope, dict):
            if tool_name == "get_nutrition_period_summary":
                normalized.setdefault("startDate", scope.get("startDate"))
                normalized.setdefault("endDate", scope.get("endDate"))
                normalized.setdefault("timezone", scope.get("timezone", "Europe/Warsaw"))
                normalized.setdefault("type", scope.get("type", "date_range"))
                normalized.setdefault("isPartial", scope.get("isPartial"))
            elif tool_name == "get_meal_logging_quality":
                normalized.setdefault("startDate", scope.get("startDate"))
                normalized.setdefault("endDate", scope.get("endDate"))
                normalized.setdefault("timezone", scope.get("timezone", "Europe/Warsaw"))
            elif tool_name == "compare_periods":
                normalized.setdefault("currentScope", scope)

        return normalized

    def _response_from_existing_run(
        self,
        *,
        run_id: str,
        thread_id: str,
        client_message_id: str,
        assistant_message: ChatMessage,
        run: AiRun | None,
    ) -> ChatRunResponseDto:
        prompt_tokens = 0
        completion_tokens = 0
        total_tokens = 0
        used_summary = False
        history_turns = 0
        truncated = False
        scope_decision = "ALLOW_APP"

        if run is not None:
            prompt_tokens = run.prompt_tokens
            completion_tokens = run.completion_tokens
            total_tokens = run.total_tokens
            used_summary = run.summary_used
            history_turns = self._coerce_int(run.metadata.get("historyTurns"), default=0)
            truncated = run.truncated
            metadata_scope_decision = run.metadata.get("scopeDecision")
            if isinstance(metadata_scope_decision, str) and metadata_scope_decision.strip():
                scope_decision = metadata_scope_decision.strip()

        return ChatRunResponseDto(
            runId=run_id,
            threadId=thread_id,
            clientMessageId=client_message_id,
            assistantMessageId=assistant_message.id,
            reply=assistant_message.content,
            usage=UsageDto(
                promptTokens=prompt_tokens,
                completionTokens=completion_tokens,
                totalTokens=total_tokens,
            ),
            contextStats=ContextStatsDto(
                usedSummary=used_summary,
                historyTurns=history_turns,
                truncated=truncated,
                scopeDecision=scope_decision,
            ),
            credits=None,
            persistence="backend_owned",
        )

    def _map_provider_error(self, exc: Exception) -> Exception:
        if self.retry_policy.is_retryable(exc):
            return AiProviderRetryableError(
                "AI provider timed out or is temporarily unavailable."
            )
        return AiProviderNonRetryableError("AI provider request failed.")

    @staticmethod
    def _derive_scope_decision(planner_result: PlannerResultDto) -> str:
        if planner_result.task_type == "out_of_scope_refusal":
            return "DENY_OTHER"

        capability_names = {capability.name for capability in planner_result.capabilities}
        if capability_names & {
            "resolve_time_scope",
            "get_nutrition_period_summary",
            "compare_periods",
            "get_meal_logging_quality",
        }:
            return "ALLOW_NUTRITION"
        if capability_names & {
            "get_profile_summary",
            "get_goal_context",
            "get_recent_chat_summary",
        }:
            return "ALLOW_USER_DATA"
        return "ALLOW_APP"

    @staticmethod
    def _normalize_language(value: str | None) -> str:
        if value == "en":
            return "en"
        return "pl"

    @staticmethod
    def _default_follow_up_question(*, language: str) -> str:
        if language == "en":
            return "Which exact time range should I analyze?"
        return "Jaki dokladnie zakres czasu mam przeanalizowac?"

    @staticmethod
    def _has_summary(summary: MemorySummary | None) -> bool:
        if summary is None:
            return False
        return bool(summary.summary.strip())

    @staticmethod
    def _elapsed_ms(started_at: float) -> int:
        return int((perf_counter() - started_at) * 1000)

    @staticmethod
    def _coerce_int(value: object, *, default: int) -> int:
        if isinstance(value, bool):
            return default
        if isinstance(value, (int, float)):
            return int(value)
        if isinstance(value, str):
            text = value.strip()
            if not text:
                return default
            try:
                return int(float(text))
            except ValueError:
                return default
        return default
