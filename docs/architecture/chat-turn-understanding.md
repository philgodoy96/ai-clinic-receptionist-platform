# Chat Turn Understanding

## Summary

The Chat API persists one `chat_turn_understandings` row per user message to record how the backend interpreted that turn: LLM analysis, normalized fields, slot-filling outcomes, and reliability metadata.

This is **diagnostic and auditability data only**. It does not drive replies, mutate conversation state, or execute side effects.

Architectural rationale: [ADR-004: Introduce Structured Chat Turn Understanding](../adr/004-structured-chat-turn-understanding.md).

## Core Principle

```text
The LLM understands. The backend validates and decides. Domain services execute.
```

## Channel Comparison

| Concern | Retell / voice | Chat API |
| --- | --- | --- |
| Orchestration | Provider-managed voice flow + backend tools | Backend-owned conversation state |
| Latency | Low; avoid per-turn backend blocking | Slightly higher latency acceptable |
| Turn understanding | Provider runtime + tool payloads | Structured LLM analysis + backend validation |
| Per-turn diagnostics | Tool calls, audit logs, voice bridge metadata | `chat_turn_understandings` + message metadata |
| Side effects | Domain services via validated tools | Domain services via `ChatReceptionistService` |

Both channels share the same domain services: scheduling, availability, holds, patient identity resolution, booking, idempotency, and business audit events.

## What Is Stored Where

| Data | Location |
| --- | --- |
| Raw user text | `conversation_messages.content` |
| Assistant reply text | `conversation_messages.content` |
| Structured turn understanding | `chat_turn_understandings` |
| Legacy LLM shadow summary | `conversation_messages.message_metadata.llm_shadow_analysis` (compatibility) |
| Durable business outcomes | `audit_logs` |

Forbidden in `chat_turn_understandings`: raw prompts, system prompts, raw provider output, duplicated user message text.

## Record Lifecycle

1. User message is appended to `conversation_messages`.
2. `LLMReceptionistAnalysisService` optionally produces `ReceptionistAnalysisResult`.
3. `LLMChatSlotFillingService` optionally validates and applies/rejects extracted candidates.
4. Deterministic chat logic generates the assistant reply and any side effects.
5. Assistant message is appended.
6. `ChatTurnUnderstandingRecordService.record_best_effort(...)` persists one row linked to `conversation_id`, `user_message_id`, and `assistant_message_id`.
7. The chat route commits the database transaction.

Persistence failures are swallowed (best-effort). The user response is unchanged.

## Related Documents

- [Structured-Output-Assisted Slot Filling](structured-output-slot-filling.md)
- [Chat LLM Interpretation Reliability](chat-llm-reliability.md)
- [Chat API Foundation](chat-api-foundation.md)
- [Audit Logs](audit-logs.md)
