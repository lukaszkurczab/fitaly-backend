from __future__ import annotations

import copy
from dataclasses import dataclass
from typing import Any

from app.core.firestore_constants import (
    AI_RUNS_COLLECTION,
    CHAT_THREADS_SUBCOLLECTION,
    MESSAGES_SUBCOLLECTION,
    USERS_COLLECTION,
)
from app.domain.ai_runs.services.ai_run_service import AiRunService
from app.domain.chat_memory.services.message_service import MessageService
from app.domain.chat_memory.services.summary_service import SummaryService
from app.domain.chat_memory.services.thread_service import ThreadService
from app.infra.firestore.repositories.ai_run_repository import AiRunRepository
from app.infra.firestore.repositories.chat_message_repository import ChatMessageRepository
from app.infra.firestore.repositories.chat_thread_repository import ChatThreadRepository
from app.infra.firestore.repositories.memory_summary_repository import (
    DEFAULT_MEMORY_DOC_ID,
    MemorySummaryRepository,
)


@dataclass
class _FakeSnapshot:
    id: str
    _data: dict[str, Any] | None

    @property
    def exists(self) -> bool:
        return self._data is not None

    def to_dict(self) -> dict[str, Any] | None:
        return copy.deepcopy(self._data)


class _FakeFirestore:
    def __init__(self) -> None:
        self.docs: dict[tuple[str, ...], dict[str, Any]] = {}

    def collection(self, name: str) -> _FakeCollectionRef:
        return _FakeCollectionRef(self, (name,))


class _FakeCollectionRef:
    def __init__(self, db: _FakeFirestore, path: tuple[str, ...]) -> None:
        self._db = db
        self._path = path

    def document(self, doc_id: str) -> _FakeDocRef:
        return _FakeDocRef(self._db, self._path + (doc_id,))

    def where(self, field: str, op: str, value: Any) -> _FakeQuery:
        return _FakeQuery(self).where(field, op, value)

    def order_by(self, field: str, direction: str = "ASCENDING") -> _FakeQuery:
        return _FakeQuery(self).order_by(field, direction=direction)

    def limit(self, count: int) -> _FakeQuery:
        return _FakeQuery(self).limit(count)

    def stream(self) -> list[_FakeSnapshot]:
        return _FakeQuery(self).stream()

    def _stream_items(self) -> list[tuple[str, dict[str, Any]]]:
        items: list[tuple[str, dict[str, Any]]] = []
        expected_len = len(self._path) + 1
        for key, value in self._db.docs.items():
            if len(key) != expected_len:
                continue
            if key[: len(self._path)] != self._path:
                continue
            items.append((key[-1], copy.deepcopy(value)))
        items.sort(key=lambda item: item[0])
        return items


class _FakeDocRef:
    def __init__(self, db: _FakeFirestore, path: tuple[str, ...]) -> None:
        self._db = db
        self._path = path

    def collection(self, name: str) -> _FakeCollectionRef:
        return _FakeCollectionRef(self._db, self._path + (name,))

    def set(self, payload: dict[str, Any], merge: bool = False) -> None:
        next_payload = copy.deepcopy(payload)
        if merge and self._path in self._db.docs:
            current = copy.deepcopy(self._db.docs[self._path])
            current.update(next_payload)
            self._db.docs[self._path] = current
            return
        self._db.docs[self._path] = next_payload

    def get(self) -> _FakeSnapshot:
        existing = self._db.docs.get(self._path)
        return _FakeSnapshot(id=self._path[-1], _data=copy.deepcopy(existing))


class _FakeQuery:
    def __init__(self, collection_ref: _FakeCollectionRef) -> None:
        self._collection_ref = collection_ref
        self._filters: list[tuple[str, str, Any]] = []
        self._order_field: str | None = None
        self._order_direction: str = "ASCENDING"
        self._limit: int | None = None

    def where(self, field: str, op: str, value: Any) -> _FakeQuery:
        self._filters.append((field, op, value))
        return self

    def order_by(self, field: str, direction: str = "ASCENDING") -> _FakeQuery:
        self._order_field = field
        self._order_direction = direction
        return self

    def limit(self, count: int) -> _FakeQuery:
        self._limit = count
        return self

    def stream(self) -> list[_FakeSnapshot]:
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
                raise ValueError(f"Unsupported filter operator in fake firestore: {op}")

        if self._order_field is not None:
            reverse = str(self._order_direction).upper() == "DESCENDING"
            order_field = self._order_field
            items.sort(
                key=lambda item: (
                    item[1].get(order_field) is None,
                    item[1].get(order_field),
                    item[0],
                ),
                reverse=reverse,
            )

        if self._limit is not None:
            items = items[: self._limit]

        return [_FakeSnapshot(id=item_id, _data=payload) for item_id, payload in items]


async def test_chat_v2_persistence_layer_e2e() -> None:
    db = _FakeFirestore()

    thread_repository = ChatThreadRepository(db)  # type: ignore[arg-type]
    message_repository = ChatMessageRepository(db)  # type: ignore[arg-type]
    memory_repository = MemorySummaryRepository(db)  # type: ignore[arg-type]
    run_repository = AiRunRepository(db)  # type: ignore[arg-type]

    thread_service = ThreadService(thread_repository)
    message_service = MessageService(message_repository, thread_service)
    summary_service = SummaryService(memory_repository)
    ai_run_service = AiRunService(run_repository)

    thread = await thread_service.ensure_thread(user_id="user-1", thread_id="thread-1")
    assert thread.id == "thread-1"
    assert thread.user_id == "user-1"

    run_id = ai_run_service.new_run_id()
    await ai_run_service.create_run(
        run_id=run_id,
        user_id="user-1",
        thread_id="thread-1",
        status="started",
    )

    first_user_message = await message_service.create_user_message(
        user_id="user-1",
        thread_id="thread-1",
        run_id=run_id,
        client_message_id="client-1",
        content="Ile bialka zjadlem dzisiaj?",
        language="pl",
    )
    second_user_message = await message_service.create_user_message(
        user_id="user-1",
        thread_id="thread-1",
        run_id=run_id,
        client_message_id="client-1",
        content="Ile bialka zjadlem dzisiaj?",
        language="pl",
    )
    assert second_user_message.id == first_user_message.id

    assistant_message = await message_service.create_assistant_message(
        user_id="user-1",
        thread_id="thread-1",
        run_id=run_id,
        content="Na razie mam tylko placeholder odpowiedzi.",
    )
    assert assistant_message.role == "assistant"

    turns = await message_service.get_recent_turns(
        user_id="user-1", thread_id="thread-1", limit=10
    )
    assert turns == [
        {"role": "user", "content": "Ile bialka zjadlem dzisiaj?"},
        {"role": "assistant", "content": "Na razie mam tylko placeholder odpowiedzi."},
    ]

    summary = await summary_service.upsert_summary(
        user_id="user-1",
        thread_id="thread-1",
        summary="Uzytkownik pyta o bialko.",
        resolved_facts=["temat:bialko"],
        covered_until_message_id=assistant_message.id,
        summary_model="gpt-4o-mini",
        version=1,
    )
    assert summary.thread_id == "thread-1"
    loaded_summary = await summary_service.get_current_summary(
        user_id="user-1", thread_id="thread-1"
    )
    assert loaded_summary is not None
    assert loaded_summary.summary == "Uzytkownik pyta o bialko."
    assert loaded_summary.covered_until_message_id == assistant_message.id

    await ai_run_service.update_run(
        run_id=run_id,
        status="completed",
        outcome="completed",
        tools_used=["get_nutrition_period_summary"],
        prompt_tokens=120,
        completion_tokens=45,
        total_tokens=165,
        total_latency_ms=780,
    )

    run = await ai_run_service.get_run(run_id=run_id)
    assert run is not None
    assert run.status == "completed"
    assert run.tools_used == ["get_nutrition_period_summary"]
    assert run.total_tokens == 165

    user_message_doc = db.docs[
        (
            USERS_COLLECTION,
            "user-1",
            CHAT_THREADS_SUBCOLLECTION,
            "thread-1",
            MESSAGES_SUBCOLLECTION,
            first_user_message.id,
        )
    ]
    assert user_message_doc["clientMessageId"] == "client-1"
    assert user_message_doc["runId"] == run_id
    assert user_message_doc["status"] == "accepted"

    memory_doc = db.docs[
        (
            USERS_COLLECTION,
            "user-1",
            CHAT_THREADS_SUBCOLLECTION,
            "thread-1",
            "memory",
            DEFAULT_MEMORY_DOC_ID,
        )
    ]
    assert memory_doc["summary"] == "Uzytkownik pyta o bialko."

    run_doc = db.docs[(AI_RUNS_COLLECTION, run_id)]
    assert run_doc["status"] == "completed"
    assert run_doc["threadId"] == "thread-1"


async def test_idempotency_lookup_is_scoped_to_thread() -> None:
    db = _FakeFirestore()
    thread_repository = ChatThreadRepository(db)  # type: ignore[arg-type]
    message_repository = ChatMessageRepository(db)  # type: ignore[arg-type]
    thread_service = ThreadService(thread_repository)
    message_service = MessageService(message_repository, thread_service)

    await thread_service.ensure_thread(user_id="user-1", thread_id="thread-a")
    await thread_service.ensure_thread(user_id="user-1", thread_id="thread-b")

    first = await message_service.create_user_message(
        user_id="user-1",
        thread_id="thread-a",
        run_id="run-a",
        client_message_id="same-client-id",
        content="Pierwsza wiadomosc",
        language="pl",
    )
    second = await message_service.create_user_message(
        user_id="user-1",
        thread_id="thread-b",
        run_id="run-b",
        client_message_id="same-client-id",
        content="Druga wiadomosc",
        language="pl",
    )

    assert first.id != second.id
