# Retell Tool-Calling Adapter

## Context

The public demo supports voice interactions through Retell.

Retell may request backend tools, but provider tool calls must not bypass backend validation or business invariants.

## Design Principle

Retell may request tools, but the backend owns validation, authorization, idempotency, and business invariants.

## Current Implementation

The implementation includes:

- Retell tool-calling request schemas
- explicit supported tool allowlist
- RetellToolCallingAdapter
- provider-safe response serialization
- integration with existing scheduling and hold services
- voice conversation context resolution via `VoiceConversationBridgeService`
- tests for route verification, adapter dispatch, idempotency, and safety

See also:

- [Clinic Time Context and Tool Contracts](clinic-time-context-and-tool-contracts.md)
- [Retell Webhook Security](retell-webhook-security.md)
- [Retell Call Lifecycle](retell-call-lifecycle.md)
- [Voice Conversation Bridge](voice-conversation-bridge.md)
- [Retell Voice Booking Confirmation](retell-voice-booking-confirmation.md)
- [Retell Voice Appointment Cancellation](retell-voice-cancellation.md)
- [Retell Voice Appointment Rescheduling](retell-voice-rescheduling.md)
- [Public Demo Guardrails](public-demo-guardrails.md)
- [Retell Dashboard Setup](../operations/retell-dashboard-setup.md)
- [Appointment Slot Holds](appointment-holds.md)

## Supported Tools

Current supported tools:

- `get_clinic_context` (read-only clinic calendar and business hours)
- `check_availability` (prefers structured `date_expression`; legacy `start_from` / `start_to` supported)
- `hold_appointment_slot`
- `release_appointment_hold`
- `book_appointment` (requires active hold, validated patient identity, and explicit caller confirmation)
- `cancel_appointment` (requires explicit cancellation confirmation and a cancelable appointment reference)
- `reschedule_appointment` (requires explicit reschedule confirmation, original appointment reference, and target hold or new slot)

Unified tool execution is exposed at:

`POST /api/v1/retell/tools`

Legacy per-tool routes under `/api/v1/retell/tools/*` may remain for compatibility, but the unified adapter route is the supported execution boundary for voice scheduling tools.

## Safety Boundary

Retell tool calls cannot directly:

- create appointments without going through `AppointmentBookingService`
- cancel appointments without going through `AppointmentCancellationService`
- reschedule appointments without going through `AppointmentReschedulingService`
- send emails
- trigger LLM calls
- mutate patient records
- bypass appointment hold rules
- bypass scheduling validation
- bypass clinic business-day and business-hours enforcement on scheduling tools
- bypass explicit booking confirmation
- bypass explicit cancellation confirmation

The adapter delegates scheduling and hold work to existing services. `book_appointment` delegates to `VoiceBookingConfirmationService` and then `AppointmentBookingService` with strict hold, identity, and confirmation checks. `cancel_appointment` delegates to `AppointmentCancellationService` with strict appointment reference, cancelable status, and confirmation checks. `reschedule_appointment` delegates to `AppointmentReschedulingService` with strict original appointment reference, hold/slot, and confirmation checks. Email dispatch and LLM orchestration remain outside this boundary.

## Execution Flow

1. Retell sends a tool callback.
2. The backend verifies the Retell signature.
3. The backend parses and validates the tool request.
4. The adapter checks the explicit tool allowlist.
5. The adapter validates tool-specific arguments.
6. For scheduling tools, the adapter resolves dates and times through `ClinicTimeService` and `SchedulingAvailabilityResolver` when configured. Provider-supplied UTC windows are not trusted without clinic business-day and business-hours validation.
7. The adapter resolves or links the related voice `Conversation` through `VoiceConversationBridgeService`.
8. The adapter delegates to existing backend services using safe voice conversation context when needed.
9. The adapter merges safe scheduling context back into `conversation_metadata["voice_context"]` when appropriate.
10. The adapter returns a provider-safe response.

Public demo guardrails run after signature verification and before adapter execution on protected Retell tool routes.

## Dashboard Configuration

Retell custom functions must call the unified tool route with the exact allowlisted `tool_name` values above. **Backend tools are the source of truth** for scheduling outcomes; dashboard prompt text must not instruct the agent to calculate relative dates or confirm appointments without tool success.

Operational setup (agent, webhooks, web calls, smoke tests) is documented in [Retell Dashboard Setup](../operations/retell-dashboard-setup.md). Prompt and `get_clinic_context` contracts are documented in [Clinic Time Context and Tool Contracts](clinic-time-context-and-tool-contracts.md).

## Idempotency

Side-effecting tool calls use a durable execution identity:

`provider:provider_call_id:tool:tool_name:tool_call_id`

Provider delivery is **at-least-once**. The application does **not** claim globally exactly-once
messaging.

Before executing a protected domain side effect, the adapter atomically claims ownership on the
related `VoiceCall` event row (unique `idempotency_key`) with status `in_progress`. Only the
claim owner runs the mutation. Successful completions store a reusable provider-safe outcome on
the same row (`succeeded`). Concurrent duplicates do not independently execute the protected
mutation: they either reuse a stored succeeded outcome or receive a deterministic retryable
`tool_execution_in_progress` response.

Stale `in_progress` claims (lease default 120s) are **not** blindly reclaimed for every tool.
Reclaim-and-retry is allowed only when the tool is classified as safely retryable; otherwise the
adapter returns `tool_execution_ambiguous_recovery` and preserves claim correlation metadata.
See
[Durable Idempotency at External Voice Tool Boundaries](durable-idempotency-voice-tool-boundaries.md).

Booking additionally uses `VoiceBookingAttempt` as the domain pre-side-effect ownership record
(`pending` / `succeeded` / `failed`). Cancellation and reschedule attempt rows follow the same
ownership principle.

When `tool_call_id` is present, hold, release, booking, cancellation, reschedule, and patient
identity tool outcomes are recorded so repeated requests return the prior provider-safe result
instead of performing the side effect again.

Duplicate provider retries should not duplicate holds, releases, appointments, cancellations,
reschedules, or confirmation email jobs. Crash-after-side-effect-before-outcome remains an
ambiguous dual-write for Redis holds and identity resolution (not exactly-once).

## Relationship to Chat

Chat and voice use different channel adapters, but share the same business services.

The voice adapter must not create a parallel scheduling implementation.

Retell tools now resolve safe voice conversation context from the shared `Conversation` domain instead of maintaining a separate voice-only state store.

## Future Work

Future implementation phases may add:

- transcript summary persistence
- voice-specific operational metrics

Dashboard agent setup and web-call browser integration are documented in [Retell Dashboard Setup](../operations/retell-dashboard-setup.md) and the `web/` frontend.
