# AI Chat v2 Architecture

## Purpose

This document defines the canonical backend path for AI Chat v2 and the guardrails that prevent legacy chat v1 from returning.

## Canonical v2 Path

- API endpoint:
  - `app/api/v2/endpoints/ai_chat.py`
  - `POST /api/v2/ai/chat/runs`
- Thread projection endpoints:
  - `app/api/routes/chat_threads.py`, mounted only by `app/api/v2/router.py`
  - `GET /api/v2/users/me/chat/threads`
  - `GET /api/v2/users/me/chat/threads/{threadId}/messages`
  - direct client message persistence is not mounted; writes happen through `POST /api/v2/ai/chat/runs`
- DI/wiring:
  - `app/api/v2/deps/ai_chat.py`
- Orchestration:
  - `app/domain/chat/*`
- Deterministic tools:
  - `app/domain/tools/*`
- Thread memory and messages:
  - `app/domain/chat_memory/*`
- AI run telemetry persistence:
  - `app/domain/ai_runs/*`
- Firestore repositories/mappers:
  - `app/infra/firestore/repositories/*`
  - `app/infra/firestore/mappers/*`
- DTO contracts:
  - `app/schemas/ai_chat/*`
- Context ownership:
  - profile, goal, nutrition, and meal context used by AI Chat v2 are backend-owned and loaded via `app/domain/tools/*`
  - selected assistant style is read from backend user profile data (`aiStyle` today, bounded to canonical `aiPersona`/`styleProfile`)
  - frontend request payload must stay minimal (`threadId`, `clientMessageId`, `message`, `language`, optional `uiContext`)
  - frontend must not send raw `meals`, `profile`, or competing meal summaries with chat runs
  - if launch later needs unsynced client-only context, add a bounded `contextSnapshot` DTO with explicit semantics and limits; do not pass full meal history lists

## Runtime Lifecycle (v2)

Kill switch:

- `AI_CHAT_ENABLED=true` by default.
- When `AI_CHAT_ENABLED=false`, `POST /api/v2/ai/chat/runs` returns:
  - status `503`
  - `detail = {"code": "AI_CHAT_DISABLED", "message": "AI Chat v2 is temporarily disabled."}`
- The endpoint must not fallback to any v1 chat runtime or hidden compatibility path.

1. auth (endpoint dependency)
2. kill switch gate
3. consent gate
4. response replay lookup by `clientMessageId` in thread scope
5. ai_run bootstrap
6. idempotent credits deduct for `userId + threadId + clientMessageId + action=chat`
7. ensure/create thread and user message persistence
8. planner
9. tool execution via canonical `ToolRegistry` to fetch backend-owned profile/goal/nutrition/meal context
10. grounded context build, bounded style profile, and token budget enforcement
11. generator + retry policy
12. assistant message persistence
13. memory summary refresh
14. ai_run telemetry update
15. response DTO mapping

## Credits Contract

- Every consented AI Chat v2 request costs `AI_CREDIT_COST_CHAT`, including `out_of_scope_refusal`.
- Out-of-scope refusal is billable because the backend already spends planner/provider capacity to classify the request before it can refuse safely.
- Credits deduct is backend-owned and idempotent per `userId + threadId + clientMessageId + action=chat`.
- Successful retry/replay of the same `clientMessageId` returns the existing assistant response and does not deduct again.
- Provider failure after deduct triggers an idempotent refund using the same credit idempotency key.
- Success responses include the updated credits status in `credits`.
- Exhausted credits return HTTP `402` with `detail.code = "AI_CREDITS_EXHAUSTED"` and current credits status in `detail.credits`.
- `ai_run.metadata` records `creditCost`, `creditDeducted`, `creditRefunded`, `balanceAfter`, and `idempotentReplay`.

## v1/v2 Boundary Rules

- Canonical chat runtime is only:
  - `POST /api/v2/ai/chat/runs`
  - `GET /api/v2/users/me/chat/threads`
  - `GET /api/v2/users/me/chat/threads/{threadId}/messages`
  - `app/api/v2/endpoints/ai_chat.py`
  - `app/api/v2/deps/ai_chat.py`
  - `app/domain/chat/*`
- Removed and forbidden for chat:
  - legacy v1 ask endpoint
  - backward-compat alias exports in `app/api/routes/ai.py` (`legacy_*`, `ai_context_service = ...`, etc.)
  - chat-only v1 modules in `app/services/*` and `app/schemas/ai_ask.py`
- v2 path must not depend on legacy AI context/prompt flow.
- v2 path must not accept or depend on frontend-owned meal/profile history for canonical chat context.
- v1 router must not mount chat thread/message projection endpoints.
- Forbidden in canonical v2 path:
  - `app.services.ai_context_service`
  - `app.services.ai_chat_prompt_service`
  - `app.services.conversation_memory_service`
  - `app.services.ai_token_budget_service`
  - `app.services.sanitization_service`
  - `app.services.openai_service` (for chat flow)
- Allowed legacy v1 AI surface:
  - `app/api/routes/ai.py` endpoints for photo/text meal analysis.

## Bounded Persona Usage

- Backend source of truth:
  - current mobile profile field: `aiStyle`
  - optional future backend profile field: `aiPersona`
- `GetProfileSummaryTool` normalizes this into allowlisted `aiPersona` and `styleProfile`:
  - `calm_guide` / Calm Guide
  - `cheerful_companion` / Cheerful Companion
  - `focused_coach` / Focused Coach
  - `mediterranean_friend` / Mediterranean Friend
- Persona is a bounded expression control only. It may change warmth, brevity, and framing, but must not change facts, confidence, safety boundaries, medical disclaimers, or nutrition evidence.
- PromptComposer always includes Fitaly brand core guardrails: calm, supportive, smart, light, non-judgmental, no diagnosis, no shame, and no aggressive fitness pressure.

## Test Ownership

Markers:

- `ai_v2`:
  - canonical v2 tests under `app/tests/*`
- `legacy_ai`:
  - legacy AI v1 tests in `tests/*` (analysis/gateway compatibility), not chat v1 runtime.

Recommended commands:

- only v2 tests:
  - `pytest -q -m ai_v2 app/tests`
- only legacy AI v1 compatibility tests:
  - `pytest -q -m legacy_ai tests`

## Known Limitations (Current)

- Same-`clientMessageId` concurrency is protected for credits by the billing idempotency document, but full run/message writes are still eventually reconciled by persistence replay.
- No cross-document transaction spanning full run/thread/message/summary write set.
- Summary refresh is intentionally lightweight and not a full long-context summarization pipeline.
- Planner/tools/generator orchestration is backend-owned, but deeper adaptive policies are deferred to later iterations.
