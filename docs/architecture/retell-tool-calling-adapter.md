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

- [Retell Webhook Security](retell-webhook-security.md)
- [Retell Call Lifecycle](retell-call-lifecycle.md)
- [Voice Conversation Bridge](voice-conversation-bridge.md)
- [Retell Voice Booking Confirmation](retell-voice-booking-confirmation.md)
- [Retell Voice Appointment Cancellation](retell-voice-cancellation.md)
- [Retell Voice Appointment Rescheduling](retell-voice-rescheduling.md)
- [Public Demo Guardrails](public-demo-guardrails.md)
- [Appointment Slot Holds](appointment-holds.md)

## Supported Tools

Current supported tools:

- `check_availability`
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
- bypass explicit booking confirmation
- bypass explicit cancellation confirmation

The adapter delegates scheduling and hold work to existing services. `book_appointment` delegates to `VoiceBookingConfirmationService` and then `AppointmentBookingService` with strict hold, identity, and confirmation checks. `cancel_appointment` delegates to `AppointmentCancellationService` with strict appointment reference, cancelable status, and confirmation checks. `reschedule_appointment` delegates to `AppointmentReschedulingService` with strict original appointment reference, hold/slot, and confirmation checks. Email dispatch and LLM orchestration remain outside this boundary.

## Execution Flow

1. Retell sends a tool callback.
2. The backend verifies the Retell signature.
3. The backend parses and validates the tool request.
4. The adapter checks the explicit tool allowlist.
5. The adapter validates tool-specific arguments.
6. The adapter resolves or links the related voice `Conversation` through `VoiceConversationBridgeService`.
7. The adapter delegates to existing backend services using safe voice conversation context when needed.
8. The adapter merges safe scheduling context back into `conversation_metadata["voice_context"]` when appropriate.
9. The adapter returns a provider-safe response.

Public demo guardrails run after signature verification and before adapter execution on protected Retell tool routes.

## Idempotency

Side-effecting tool calls use `provider_call_id` and `tool_call_id` when available.

Duplicate provider retries should not duplicate holds, releases, appointments, cancellations, reschedules, or confirmation email jobs.

When `tool_call_id` is present, hold, release, booking, cancellation, and reschedule outcomes are recorded on the related `VoiceCall` event metadata (and `VoiceBookingAttempt` for booking, `AppointmentCancellationAttempt` for cancellation, `AppointmentRescheduleAttempt` for rescheduling) so repeated requests return the prior provider-safe result instead of performing the side effect again.

## Relationship to Chat

Chat and voice use different channel adapters, but share the same business services.

The voice adapter must not create a parallel scheduling implementation.

Retell tools now resolve safe voice conversation context from the shared `Conversation` domain instead of maintaining a separate voice-only state store.

## Future Work

Future implementation phases may add:

- transcript summary persistence
- voice-specific operational metrics
