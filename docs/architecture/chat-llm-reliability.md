# Chat LLM Interpretation Reliability

## Context

The chat channel is **deterministic-first**.

Public user-facing scheduling is controlled by backend conversation logic, scheduling services, hold services, and booking services. The LLM receptionist analysis layer assists interpretation and structured slot filling. It does not own durable scheduling actions.

This document describes the chat-specific LLM reliability boundary added after the core LLM provider foundation and reliability orchestration were in place.

## Design Principle

```text
LLM invalid output = internal retry/repair
User ambiguity = clarification question
Missing required information = normal follow-up
Safety issue = safe deterministic handling
Provider failure = retry/fallback/deterministic fallback
```

The backend remains the source of truth for:

- availability
- appointment holds
- patient identity
- booking confirmation
- durable appointment state
- safety boundaries

## Deterministic-First Architecture

`ChatReceptionistService` handles each user message in this order:

1. Store the user message.
2. Optionally run `LLMReceptionistAnalysisService` in shadow/assist mode.
3. Optionally run `LLMChatSlotFillingService` when analysis is eligible.
4. Generate the assistant reply through the deterministic responder and scheduling/booking services.
5. Persist assistant message metadata and return the public API response.

The LLM is **not** the primary chat orchestrator.

Assistant `intent`, `reply`, hold creation, booking confirmation, and escalation behavior come from deterministic logic. LLM output is untrusted input that may pre-fill validated conversational context when eligibility checks pass.

See also: [Chat API Foundation](chat-api-foundation.md), [Structured-Output-Assisted Slot Filling](structured-output-slot-filling.md), [LLM Provider Foundation](llm-provider-foundation.md).

## Sanitized Conversation Context Injection

`LLMReceptionistAnalysisService._build_llm_request` may include a compact backend-provided context snapshot when useful scheduling context exists in `conversation_metadata.chat_context`.

### Request shape

When sanitized context is non-empty:

```text
system: receptionist analysis system prompt
system: backend-provided sanitized conversation context snapshot
user: current user message
```

When sanitized context is empty:

```text
system: receptionist analysis system prompt
user: current user message
```

On a structural-output retry, an additional strict repair `system` message is inserted before the user message. See Structured Output Retry/Repair below.

### Purpose

The snapshot helps the model interpret the **latest user message** in multi-turn scheduling conversations. For example, if the user already selected Cardiology and later says `tomorrow morning`, the model receives structured state instead of inferring everything from a single turn.

The snapshot does **not** authorize durable actions. The disclaimer in the prompt states that the backend remains responsible for validation and side effects.

### Preferred representation

The snapshot prefers **structured state** over raw transcript history. Full conversation history is not injected into the analysis prompt.

### Allowed safe fields

Examples of fields that may appear in the sanitized snapshot:

```json
{
  "selected_specialty": "Cardiology",
  "selected_doctor": "Dr. Emily Carter",
  "requested_date": "2026-06-25",
  "requested_time_window": "morning",
  "selected_time": "10:00",
  "has_offered_slots": true,
  "last_availability_status": "available",
  "has_active_hold": true,
  "awaiting_confirmation": false,
  "booking_confirmed": false,
  "patient_identity_status": "missing",
  "has_patient_name": false,
  "has_patient_date_of_birth": false,
  "has_confirmed_email": false
}
```

Field policy:

- `selected_specialty` and `selected_doctor` come from display names only, not IDs.
- `requested_time_window` is the label from structured `requested_time_window` metadata.
- `selected_time` is the selected appointment time (HH:MM derived from `selected_start_time`), not a generic pre-selection preference label.
- `has_offered_slots` is included only when `offered_slots` is present as a list.
- `last_availability_status` is included when an explicit status exists in context (`last_availability_status` or `availability_status`), or when `offered_slots` is a **non-empty** list (safe inference of `available`). Empty or missing `offered_slots` must **not** be treated as proof that availability is unavailable.
- Identity state uses coarse flags (`patient_identity_status`, `has_patient_name`, `has_patient_date_of_birth`, `has_confirmed_email`). Coarse flags are included when identity-related keys exist or when an active hold/booking flow requires them.

### Explicitly excluded

The LLM prompt must not include:

- raw patient email, phone number, full date of birth, or patient name when unnecessary
- patient resolution IDs
- appointment IDs, hold IDs, slot IDs, doctor IDs, specialty IDs, or other UUIDs
- raw `offered_slots` payloads
- Redis, database, or internal tool details

Implementation: `sanitize_conversation_context_for_llm` in `app/services/llm_receptionist.py`.

## Structured Output Retry/Repair Boundary

`LLMReceptionistAnalysisService` retries interpretation internally when model output fails structurally. The same user message and the same sanitized context snapshot are reused. Durable scheduling actions are not retried inside this loop.

### Default retry policy

Primary provider attempts are bounded by:

```env
LLM_MAX_PRIMARY_ATTEMPTS=2
```

This means one initial attempt plus one retry by default.

### Local repair vs repair prompt

Two repair mechanisms exist:

1. **Local JSON extraction repair** — the structured output parser may extract a JSON object from markdown fences or surrounding text before declaring parse failure. When this happens, `used_repair` metadata is `true`.
2. **Repair prompt retry** — after a structural output failure, the next provider attempt adds a strict repair instruction telling the model to return only valid JSON matching the required schema.

`used_repair` refers to **local JSON extraction repair**, not repair-prompt usage. Repair-prompt usage is observable through attempt count and request inspection in tests; it is not stored as a separate metadata field today.

### Repair prompt behavior

**First attempt:** normal request (system prompt, optional context snapshot, user message).

**Retry after structural failure:** same request rebuilt with an additional `system` repair instruction. The user message and context snapshot are unchanged. No extra transcript history is added.

Repair prompt is added only after structural output failures:

- `JSON_PARSE_FAILED`
- `JSON_REPAIR_FAILED`
- `SCHEMA_VALIDATION_FAILED`

Provider exception, timeout, and rate-limit retries reuse the normal request **without** the repair prompt.

### Primary provider retryable failures

- `JSON_PARSE_FAILED`
- `JSON_REPAIR_FAILED`
- `SCHEMA_VALIDATION_FAILED`
- `EMPTY_RESPONSE`
- provider exception
- provider timeout
- provider rate limit

### Not blindly retried

- `SAFETY_VIOLATION`
- `LOW_CONFIDENCE` on structurally valid output (analysis succeeds; slot filling remains ineligible)
- policy / escalation handling reasons such as `HUMAN_ESCALATION_REQUEST` and `MEDICAL_EMERGENCY` when returned as successful structured output

User ambiguity is handled through normal clarification replies in the deterministic flow, not as a model failure.

### Deterministic fallback

If all primary attempts fail (and optional fallback provider attempts, when enabled and eligible), `LLMReceptionistAnalysisService` returns deterministic fallback analysis. The chat API does not expose internal LLM errors to the user. The deterministic responder still produces a safe assistant reply.

Slot filling is skipped when analysis used deterministic fallback, failed reliability checks, low confidence, emergency intent, or safety flags.

### Fallback provider policy

Fallback provider support remains optional and disabled by default:

```env
LLM_FALLBACK_ENABLED=false
```

When enabled, the fallback provider is attempted only after **fallback-eligible retryable** primary failures (for example provider exception or `JSON_REPAIR_FAILED`). Structural failures such as `JSON_PARSE_FAILED` and `SCHEMA_VALIDATION_FAILED` are repairable for primary retry but are **not** fallback-provider eligible under the current taxonomy. If fallback is disabled or ineligible, primary exhaustion leads directly to deterministic fallback analysis.

See also: [LLM Reliability Orchestration](llm-reliability-orchestration.md).

## Side-Effect Boundary

LLM retry/repair retries **interpretation only**.

It does not retry or execute:

- hold creation
- booking
- cancellation
- rescheduling
- email enqueueing
- audit logging

Durable scheduling actions remain outside the LLM retry loop and are owned by deterministic services:

- `SchedulingService`
- `AppointmentHoldService`
- `AppointmentBookingService`
- `EmailJobService`

## Metadata

Assistant message `llm_shadow_analysis` metadata includes reliability fields such as:

- `provider`, `primary_provider`, `fallback_provider`
- `attempt_count`, `primary_attempt_count`, `fallback_attempt_count`
- `used_repair`, `used_fallback`, `used_fallback_provider`
- `failure_category`, `failure_reason`
- `prompt_version`

Raw prompts, raw provider output, and extracted patient identity are not stored in conversation metadata.

## Offline Evaluation Harness

The chat scheduling evaluation harness (`evals/chat_scheduling.jsonl`, `python -m scripts.evaluate_chat_scheduling`) validates deterministic-first chat behavior across multi-turn scenarios after LLM interpretation, retry/repair, and deterministic-fallback changes.

It checks booking state, semantic reply constraints, and safe public wording. It does not call real providers and does not make the LLM the primary orchestrator. See [Chat Scheduling Evaluation Harness](chat-scheduling-evaluation-harness.md).

## What Remains Future Work

Intentional production evolution, not missing MVP behavior for the current chat demo:

- chat message / client idempotency for duplicate `POST` requests
- durable action idempotency for duplicate chat scheduling actions
- dedicated `llm_runs` persistence table
- provider-mode chat scheduling evaluation against live Groq/Bedrock providers
- written chat cancellation and rescheduling flows
- LLM as primary orchestration mode, if ever desired

## Related Documentation

- [Chat Scheduling Evaluation Harness](chat-scheduling-evaluation-harness.md)
- [Chat API Foundation](chat-api-foundation.md)
- [Chat API](../api/chat.md)
- [LLM Reliability Orchestration](llm-reliability-orchestration.md)
- [LLM Provider Foundation](llm-provider-foundation.md)
- [Structured-Output-Assisted Slot Filling](structured-output-slot-filling.md)
- [Prompt Versioning and LLM Traceability](prompt-versioning.md)
