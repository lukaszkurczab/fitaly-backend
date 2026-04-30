from __future__ import annotations

import copy
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, cast

from app.core.config import settings
from app.core.exceptions import AiCreditsExhaustedError
from app.domain.ai_runs.services.ai_run_service import AiRunService
from app.domain.chat.context_builder import ContextBuilder
from app.domain.chat.generator import GenerationResult, GenerationUsage
from app.domain.chat.orchestrator import ChatOrchestrator
from app.domain.chat.prompt_composer import PromptComposer
from app.domain.chat.retry_policy import RetryPolicy
from app.domain.chat_memory.services.message_service import MessageService
from app.domain.chat_memory.services.summary_service import SummaryService
from app.domain.chat_memory.services.thread_service import ThreadService
from app.domain.tools.base import DomainTool
from app.domain.tools.registry import ToolRegistry
from app.domain.users.services.consent_service import ConsentService
from app.infra.firestore.repositories.ai_run_repository import AiRunRepository
from app.infra.firestore.repositories.chat_message_repository import ChatMessageRepository
from app.infra.firestore.repositories.chat_thread_repository import ChatThreadRepository
from app.infra.firestore.repositories.memory_summary_repository import MemorySummaryRepository
from app.schemas.ai_credits import AiCreditsStatus, CreditCosts
from app.schemas.ai_chat.planner import PlannerResultDto
from app.services.ai_credits_service import IdempotentCreditResult


@dataclass
class FakeSnapshot:
    id: str
    _data: dict[str, Any] | None

    @property
    def exists(self) -> bool:
        return self._data is not None

    def to_dict(self) -> dict[str, Any] | None:
        return copy.deepcopy(self._data)


class FakeFirestore:
    def __init__(self) -> None:
        self.docs: dict[tuple[str, ...], dict[str, Any]] = {}

    def collection(self, name: str) -> "FakeCollectionRef":
        return FakeCollectionRef(self, (name,))


class FakeCollectionRef:
    def __init__(self, db: FakeFirestore, path: tuple[str, ...]) -> None:
        self._db = db
        self._path = path

    def document(self, doc_id: str) -> "FakeDocRef":
        return FakeDocRef(self._db, self._path + (doc_id,))

    def where(self, field: str, op: str, value: Any) -> "FakeQuery":
        return FakeQuery(self).where(field, op, value)

    def order_by(self, field: str, direction: str = "ASCENDING") -> "FakeQuery":
        return FakeQuery(self).order_by(field, direction=direction)

    def limit(self, count: int) -> "FakeQuery":
        return FakeQuery(self).limit(count)

    def stream(self) -> list[FakeSnapshot]:
        return FakeQuery(self).stream()

    def _stream_items(self) -> list[tuple[str, dict[str, Any]]]:
        expected_len = len(self._path) + 1
        items: list[tuple[str, dict[str, Any]]] = []
        for key, payload in self._db.docs.items():
            if len(key) != expected_len:
                continue
            if key[: len(self._path)] != self._path:
                continue
            items.append((key[-1], copy.deepcopy(payload)))
        items.sort(key=lambda item: item[0])
        return items


class FakeDocRef:
    def __init__(self, db: FakeFirestore, path: tuple[str, ...]) -> None:
        self._db = db
        self._path = path

    def collection(self, name: str) -> FakeCollectionRef:
        return FakeCollectionRef(self._db, self._path + (name,))

    def set(self, payload: dict[str, Any], merge: bool = False) -> None:
        incoming = copy.deepcopy(payload)
        if merge and self._path in self._db.docs:
            current = copy.deepcopy(self._db.docs[self._path])
            current.update(incoming)
            self._db.docs[self._path] = current
            return
        self._db.docs[self._path] = incoming

    def get(self) -> FakeSnapshot:
        existing = self._db.docs.get(self._path)
        return FakeSnapshot(id=self._path[-1], _data=copy.deepcopy(existing))


class FakeQuery:
    def __init__(self, collection_ref: FakeCollectionRef) -> None:
        self._collection_ref = collection_ref
        self._filters: list[tuple[str, str, Any]] = []
        self._order_field: str | None = None
        self._order_direction: str = "ASCENDING"
        self._limit: int | None = None

    def where(self, field: str, op: str, value: Any) -> "FakeQuery":
        self._filters.append((field, op, value))
        return self

    def order_by(self, field: str, direction: str = "ASCENDING") -> "FakeQuery":
        self._order_field = field
        self._order_direction = direction
        return self

    def limit(self, count: int) -> "FakeQuery":
        self._limit = count
        return self

    def stream(self) -> list[FakeSnapshot]:
        items = self._collection_ref._stream_items()

        for field, op, value in self._filters:
            if op == "==":
                items = [item for item in items if item[1].get(field) == value]
            elif op == "<":
                items = [
                    item
                    for item in items
                    if item[1].get(field) is not None and item[1].get(field) < value
                ]
            else:
                raise ValueError(f"Unsupported fake filter operator: {op}")

        if self._order_field is not None:
            reverse = str(self._order_direction).upper() == "DESCENDING"
            field = self._order_field
            items.sort(
                key=lambda item: (
                    item[1].get(field) is None,
                    item[1].get(field),
                    item[0],
                ),
                reverse=reverse,
            )

        if self._limit is not None:
            items = items[: self._limit]

        return [FakeSnapshot(id=item_id, _data=payload) for item_id, payload in items]


class FakeConsentService:
    def __init__(self, *, allowed: bool) -> None:
        self.allowed = allowed

    async def ensure_ai_health_data_consent(self, *, user_id: str) -> None:
        del user_id
        if self.allowed:
            return
        from app.core.errors import ConsentRequiredError

        raise ConsentRequiredError("AI health data consent required.")


class FakePlanner:
    def __init__(self, result: PlannerResultDto) -> None:
        self.result = result
        self.calls: list[dict[str, Any]] = []

    async def plan(self, **kwargs: Any) -> PlannerResultDto:
        self.calls.append(kwargs)
        return self.result


class StaticTool(DomainTool):
    def __init__(
        self,
        *,
        name: str,
        output: dict[str, Any] | Callable[[dict[str, Any]], dict[str, Any]],
    ) -> None:
        self.name = name
        self._output = output
        self.calls: list[dict[str, Any]] = []

    async def execute(self, *, user_id: str, args: dict[str, Any]) -> dict[str, Any]:
        del user_id
        self.calls.append(copy.deepcopy(args))
        if callable(self._output):
            return copy.deepcopy(self._output(args))
        return copy.deepcopy(self._output)


class FakeGenerator:
    def __init__(self, scripted: list[GenerationResult | Exception]) -> None:
        self._scripted = list(scripted)
        self.calls: list[list[dict[str, str]]] = []

    async def generate(self, *, messages: list[dict[str, str]]) -> GenerationResult:
        self.calls.append(copy.deepcopy(messages))
        if not self._scripted:
            raise RuntimeError("No scripted generation result.")
        item = self._scripted.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


class FakeAiCreditsService:
    def __init__(self, *, balance: int = 10, tier: str = "free") -> None:
        self.balance = balance
        self.allocation = max(balance, settings.AI_CREDITS_FREE)
        self.tier = "premium" if tier == "premium" else "free"
        self.deductions: list[dict[str, Any]] = []
        self.refunds: list[dict[str, Any]] = []
        self.idempotency: dict[str, dict[str, Any]] = {}

    async def get_credits_status(self, user_id: str) -> AiCreditsStatus:
        return self._status(user_id)

    async def deduct_credits_idempotent(
        self,
        user_id: str,
        *,
        cost: int,
        action: str,
        idempotency_key: str,
    ) -> IdempotentCreditResult:
        if idempotency_key in self.idempotency:
            item = self.idempotency[idempotency_key]
            if item.get("state") == "refunded" or item.get("creditRefunded") is True:
                if self.balance < cost:
                    raise AiCreditsExhaustedError("AI credits exhausted.")
                before = self.balance
                self.balance -= cost
                item.update(
                    {
                        "state": "deducted",
                        "creditDeducted": True,
                        "creditRefunded": False,
                        "balanceBefore": before,
                        "balanceAfter": self.balance,
                        "deductCount": int(item.get("deductCount") or 1) + 1,
                    }
                )
                self.deductions.append(
                    {
                        "user_id": user_id,
                        "cost": cost,
                        "action": action,
                        "idempotency_key": idempotency_key,
                        "balance_after": self.balance,
                    }
                )
                return IdempotentCreditResult(status=self._status(user_id), applied=True)
            return IdempotentCreditResult(
                status=self._status(user_id),
                applied=False,
                refunded=item.get("state") == "refunded",
            )
        if self.balance < cost:
            raise AiCreditsExhaustedError("AI credits exhausted.")

        before = self.balance
        self.balance -= cost
        self.idempotency[idempotency_key] = {
            "cost": cost,
            "action": action,
            "state": "deducted",
            "creditDeducted": True,
            "creditRefunded": False,
            "deductCount": 1,
            "refundCount": 0,
            "balanceBefore": before,
            "balanceAfter": self.balance,
        }
        self.deductions.append(
            {
                "user_id": user_id,
                "cost": cost,
                "action": action,
                "idempotency_key": idempotency_key,
                "balance_after": self.balance,
            }
        )
        return IdempotentCreditResult(status=self._status(user_id), applied=True)

    async def refund_credits_idempotent(
        self,
        user_id: str,
        *,
        cost: int,
        action: str,
        idempotency_key: str,
    ) -> IdempotentCreditResult:
        item = self.idempotency.get(idempotency_key)
        if item is None or item.get("state") != "deducted":
            return IdempotentCreditResult(status=self._status(user_id), applied=False)

        refund_cost = int(item.get("cost") or cost)
        before = self.balance
        self.balance = min(self.balance + refund_cost, self.allocation)
        item["state"] = "refunded"
        item["creditRefunded"] = True
        item["refundCount"] = int(item.get("refundCount") or 0) + 1
        item["refundBalanceBefore"] = before
        item["refundBalanceAfter"] = self.balance
        self.refunds.append(
            {
                "user_id": user_id,
                "cost": refund_cost,
                "action": action,
                "idempotency_key": idempotency_key,
                "balance_after": self.balance,
            }
        )
        return IdempotentCreditResult(
            status=self._status(user_id),
            applied=True,
            refunded=True,
        )

    async def complete_credits_idempotent(
        self,
        user_id: str,
        *,
        idempotency_key: str,
    ) -> IdempotentCreditResult:
        item = self.idempotency.get(idempotency_key)
        if item is None:
            return IdempotentCreditResult(status=self._status(user_id), applied=False)
        if item.get("state") == "deducted":
            item["state"] = "completed"
            return IdempotentCreditResult(status=self._status(user_id), applied=True)
        return IdempotentCreditResult(
            status=self._status(user_id),
            applied=False,
            refunded=item.get("state") == "refunded",
        )

    def _status(self, user_id: str) -> AiCreditsStatus:
        now = datetime(2026, 4, 19, tzinfo=timezone.utc)
        return AiCreditsStatus(
            userId=user_id,
            tier=cast(Any, self.tier),
            balance=self.balance,
            allocation=self.allocation,
            periodStartAt=now,
            periodEndAt=datetime(2026, 5, 19, tzinfo=timezone.utc),
            costs=CreditCosts(
                chat=settings.AI_CREDIT_COST_CHAT,
                textMeal=settings.AI_CREDIT_COST_TEXT_MEAL,
                photo=settings.AI_CREDIT_COST_PHOTO,
            ),
        )


@dataclass
class OrchestratorHarness:
    orchestrator: ChatOrchestrator
    db: FakeFirestore
    planner: FakePlanner
    tools: dict[str, StaticTool]
    generator: FakeGenerator
    ai_run_service: AiRunService
    message_service: MessageService
    summary_service: SummaryService
    credits_service: FakeAiCreditsService


def planner_result_payload(
    *,
    task_type: str,
    capabilities: list[dict[str, Any]],
    response_mode: str = "assessment_plus_guidance",
    needs_follow_up: bool = False,
    follow_up_question: str | None = None,
    requires_user_data: bool = True,
    mixed_request: bool = False,
    topics: list[str] | None = None,
    requested_scope_label: str | None = None,
) -> PlannerResultDto:
    return PlannerResultDto.model_validate(
        {
            "taskType": task_type,
            "queryUnderstanding": {
                "requiresUserData": requires_user_data,
                "requestedScopeLabel": requested_scope_label,
                "mixedRequest": mixed_request,
                "topics": topics or ["nutrition"],
            },
            "capabilities": capabilities,
            "responseMode": response_mode,
            "needsFollowUp": needs_follow_up,
            "followUpQuestion": follow_up_question,
        }
    )


def generation_result(
    *,
    text: str,
    prompt_tokens: int = 120,
    completion_tokens: int = 40,
    total_tokens: int = 160,
) -> GenerationResult:
    return GenerationResult(
        text=text,
        usage=GenerationUsage(
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
        ),
    )


def build_orchestrator_harness(
    *,
    planner_result: PlannerResultDto,
    tools: dict[str, dict[str, Any] | Callable[[dict[str, Any]], dict[str, Any]]],
    generator_script: list[GenerationResult | Exception],
    consent_allowed: bool = True,
    retry_policy: RetryPolicy | None = None,
    initial_credits: int = 10,
) -> OrchestratorHarness:
    db = FakeFirestore()

    thread_repository = ChatThreadRepository(db)  # type: ignore[arg-type]
    message_repository = ChatMessageRepository(db)  # type: ignore[arg-type]
    summary_repository = MemorySummaryRepository(db)  # type: ignore[arg-type]
    run_repository = AiRunRepository(db)  # type: ignore[arg-type]

    thread_service = ThreadService(thread_repository)
    message_service = MessageService(message_repository, thread_service)
    summary_service = SummaryService(summary_repository)
    ai_run_service = AiRunService(run_repository)

    planner = FakePlanner(planner_result)
    static_tools = {
        name: StaticTool(name=name, output=output) for name, output in tools.items()
    }
    registry = ToolRegistry(list(static_tools.values()))

    generator = FakeGenerator(scripted=generator_script)
    credits_service = FakeAiCreditsService(balance=initial_credits)
    effective_retry_policy = retry_policy or RetryPolicy(
        max_attempts=3,
        timeout_seconds=0.2,
        base_delay_seconds=0.0,
        jitter_seconds=0.0,
    )

    orchestrator = ChatOrchestrator(
        consent_service=cast(ConsentService, FakeConsentService(allowed=consent_allowed)),
        thread_service=thread_service,
        message_service=message_service,
        summary_service=summary_service,
        ai_run_service=ai_run_service,
        planner=planner,  # type: ignore[arg-type]
        tool_registry=registry,
        context_builder=ContextBuilder(),
        prompt_composer=PromptComposer(),
        generator=generator,  # type: ignore[arg-type]
        retry_policy=effective_retry_policy,
        credits_service=credits_service,
    )

    return OrchestratorHarness(
        orchestrator=orchestrator,
        db=db,
        planner=planner,
        tools=static_tools,
        generator=generator,
        ai_run_service=ai_run_service,
        message_service=message_service,
        summary_service=summary_service,
        credits_service=credits_service,
    )
