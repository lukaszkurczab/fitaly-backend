# ADR: AI Gateway Data Boundary (Domain vs Analytics vs Observability)

## Status
Accepted

## Date
2026-04-23

## Context
Legacy AI gateway (`/api/v1/ai/*`) persisted per-request diagnostic payloads in Firestore `ai_gateway_logs`.
Those records were observability data, not product domain data.

Storing gateway diagnostics in Firestore mixed concerns:
- domain database started acting as a log store,
- payloads were event-heavy and low-signal for product KPI,
- support/debug correlation concerns were coupled to Firestore write availability.

## Decision
Remove `ai_gateway_logs` Firestore persistence from runtime.

Gateway emissions are now split into two sinks:

1. Observability logs (structured logger sink)
- event name: `ai_gateway.decision`
- purpose: operational diagnostics and incident triage
- includes request correlation (`requestId`, `userId`, optional `threadId`) and technical diagnostics (decision, reason, outcome, latency, token stats, retry flags, etc.)
- does **not** persist raw prompt content or message hash

2. Product analytics events (analytics layer)
- event name: `ai_gateway_kpi.decision`
- purpose: minimal KPI-oriented tracking
- includes only decision-oriented fields: action, decision, reason, outcome, scope/tier/cost, request correlation
- excludes debug-heavy internals (token counters, retry internals, truncation internals)

Feature-safe fallback:
- If a sink logger fails temporarily, runtime falls back to local warning log `ai_gateway.log_sink_fallback`.
- Gateway request flow continues (logging failures are non-blocking).

## Domain Boundary
Domain data remains in Firestore domain collections (`users/*`, meals, billing snapshots/ledgers, chat thread/message/run data, etc.).

AI gateway request diagnostics are explicitly **not** domain data.

## Consequences
- Core chat/meal analysis flow no longer depends on Firestore writes for gateway logging.
- Request correlation is preserved in logs for support.
- Product KPI is emitted as small semantic events, separate from diagnostic verbosity.
