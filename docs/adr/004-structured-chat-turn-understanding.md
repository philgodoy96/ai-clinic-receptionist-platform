# ADR-004: Introduce Structured Chat Turn Understanding

## Status

Accepted

## Context

The AI Clinic Receptionist Platform supports multiple receptionist channels:

1. **Chat API** — first-party written text orchestration owned by the backend
2. **Retell voice** — real-time spoken interaction orchestrated by the voice provider

Voice and chat share the same backend domain services and invariants (scheduling, availability, appointment holds, patient identity resolution, appointment booking, idempotency, and auditability). They do **not** share the same orchestration strategy.

### Voice channel constraints

Retell validates the voice workflow through provider-managed orchestration and tool calling. Voice is optimized for:

- Low-latency turn-taking
- Natural speech flow and interruptions
- Real-time UX without backend round-trips on every spoken turn

A rigid backend state machine at the voice layer would make conversations feel slow or blocked. Retell remains responsible for voice interaction flow; backend tools enforce domain rules when real data, validation, or side effects are required.

See also: [ADR-001: Voice UX must not be modeled as chat UX](001-chat-vs-voice-conversation-boundaries.md).

### Chat channel constraints

Chat is a first-party text orchestration channel. Written chat can tolerate slightly more latency than voice. Chat benefits from:

- Stronger auditability and replayability
- Explicit backend-owned conversation state
- Structured validation before side effects

Manual chat validation showed that deterministic parsers alone are insufficient for natural written conversations. Users provide multiple fields in one turn, use flexible date formats, select slots casually, and confirm in informal language.

The project needs auditability to compare, per user turn:

- Raw user message (in `conversation_messages.content`)
- LLM interpretation (intent, confidence, extracted candidates)
- Normalized fields (date/time parsing outcomes)
- Backend accepted and rejected fields
- Backend decision and action

Core principle:

```text
The LLM understands. The backend validates and decides. Domain services execute.
```

## Decision

### Channel orchestration

- **Keep Retell as the voice-channel orchestrator.** Voice continues to use provider-managed orchestration and backend tool calls at domain boundaries.
- **Use backend-owned conversation state plus structured LLM turn understanding for Chat API.** The LLM interprets each user message in context; the backend validates fields, controls state transitions, and executes side effects through domain services.

### Persistence model

- Introduce a dedicated `chat_turn_understandings` table.
- Persist **one record per chat user turn**, keyed by unique `user_message_id`.
- Raw user text remains in `conversation_messages.content`; structured understanding is stored separately.
- Do **not** store raw prompts, system prompts, or raw provider output in this table. User text is referenced through `user_message_id`, not duplicated.
- `audit_logs` remains a **business-event ledger** (holds, bookings, failures, escalations). It is not the primary store for high-cardinality per-turn LLM diagnostics.
- Existing `llm_shadow_analysis` in assistant `message_metadata` remains for backward compatibility during migration. New diagnostics should prefer `chat_turn_understandings` for queryability and analytics.

### Execution boundary

- The LLM never directly executes side effects (holds, bookings, emails, identity commits).
- `ChatTurnUnderstandingRecordService` maps in-memory analysis and slot-filling results into persistence records only. It does not change conversation state, response metadata, or public API responses.
- Persistence uses best-effort writes so diagnostic failures cannot break the user-facing chat reply.
- Repositories flush within the request transaction; the chat route owns `commit`.

## Consequences

### Positive consequences

- Stronger foundation for natural written chat UX without giving the LLM control over scheduling.
- Better observability: compare model extraction vs backend validation vs final action.
- Clear separation between interpretation (LLM), validation (backend), and execution (domain services).
- Safer LLM use in a backend system with explicit invariants.
- Supports future replay, evaluation, and debug workflows.
- Aligns with deterministic-first chat architecture documented in [Chat LLM Interpretation Reliability](../architecture/chat-llm-reliability.md).

### Trade-offs

- **Storage volume:** one row per chat user turn, including JSON fields for extracted and slot-filling outcomes.
- **PII surface:** `extracted_fields` may contain patient identity candidates. Production requires retention, redaction, and access-control policies.
- **Schema and repository surface area:** additional model, migration, repository, and service layers.
- **Dual-write period:** `llm_shadow_analysis` and `chat_turn_understandings` may coexist until shadow metadata is retired or reduced.

## Alternatives Considered

### Alternative 1: Use Retell-style orchestration for chat

Route chat through a provider-managed conversational runtime similar to voice.

**Rejected.** Chat can benefit from backend-owned state, stronger auditability, and structured per-turn diagnostics without voice latency constraints. Chat and voice orchestration should remain channel-appropriate while sharing domain services.

### Alternative 2: Keep deterministic parsers only

Rely on regex, keyword, and fixed slot-collection flows without structured LLM turn understanding.

**Rejected.** Natural user input is too flexible. Brittle parsers fail on multi-field turns, informal dates, and casual confirmations. LLM-assisted interpretation with backend validation is the intended assistive layer.

### Alternative 3: Let the LLM directly manage actions

Allow the model to create holds, book appointments, or mutate patient records from its output.

**Rejected.** The backend must enforce invariants, idempotency, hold ownership, identity completeness, and explicit confirmation. Domain services execute durable side effects.

### Alternative 4: Store everything in `audit_logs`

Record per-turn LLM diagnostics as audit events.

**Rejected.** Audit logs are for durable **business events** with lower cardinality. Per-turn LLM diagnostics would flood the ledger, mix operational telemetry with business outcomes, and complicate retention policies.

### Alternative 5: Store everything in `conversation_messages.message_metadata`

Keep `llm_shadow_analysis`, slot filling, and diagnostics only in assistant message JSON.

**Rejected as primary store.** Message metadata is less queryable, mixes user-visible diagnostics with persistence concerns, and lacks direct analytical structure (indexes on intent, failure category, prompt version, per-turn foreign keys). It remains as a compatibility layer during migration.

## Follow-up Work

- Add structured `ChatTurnUnderstanding` interpreter and fake provider for tests and local development.
- Add Groq-backed interpreter behind configuration when reliability gates pass.
- Wire understanding into availability and slot selection flows.
- Wire understanding into patient identity intake and confirmation states.
- Define retention and redaction policy for extracted PII in `chat_turn_understandings`.
- Optionally add a thin `CHAT_TURN_ANALYZED` audit event later, pointing to `chat_turn_understanding_id` without duplicating full diagnostics in `event_metadata`.
- Gradually reduce reliance on `llm_shadow_analysis` once consumers migrate to `chat_turn_understandings`.

## Related Documents

- [ADR-001: Voice UX must not be modeled as chat UX](001-chat-vs-voice-conversation-boundaries.md)
- [Chat Turn Understanding](../architecture/chat-turn-understanding.md)
- [Structured-Output-Assisted Slot Filling](../architecture/structured-output-slot-filling.md)
- [Chat LLM Interpretation Reliability](../architecture/chat-llm-reliability.md)
- [Audit Logs](../architecture/audit-logs.md)
