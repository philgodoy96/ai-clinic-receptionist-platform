# Retell Voice Appointment Rescheduling

## Context

The system supports Retell voice booking and cancellation.

Rescheduling is now exposed to the voice channel through the shared `AppointmentReschedulingService`.

## Design Principle

Retell can request rescheduling, but `AppointmentReschedulingService` owns validation, idempotency, status transitions, and auditability.

## Current Implementation

The implementation includes:

- `reschedule_appointment` Retell tool support
- explicit confirmation validation
- original appointment reference resolution
- hold/new slot validation
- idempotent provider callback handling
- delegation to `AppointmentReschedulingService`
- safe conversation metadata updates
- provider-safe responses

See also:

- [Retell Tool-Calling Adapter](retell-tool-calling-adapter.md)
- [Retell Voice Booking Confirmation](retell-voice-booking-confirmation.md)
- [Retell Voice Appointment Cancellation](retell-voice-cancellation.md)
- [Appointment Rescheduling Foundation](appointment-rescheduling-foundation.md)
- [Voice Conversation Bridge](voice-conversation-bridge.md)
- [Public Demo Guardrails](public-demo-guardrails.md)

## Execution Flow

1. Retell sends a verified `reschedule_appointment` tool callback.
2. The backend resolves `provider_call_id` to `VoiceCall` and `Conversation`.
3. The backend resolves the original appointment from tool arguments or safe `voice_context`.
4. The backend validates the target hold or new slot reference.
5. The backend requires explicit reschedule confirmation.
6. The backend delegates to `AppointmentReschedulingService`.
7. Conversation metadata is updated safely.
8. The backend returns a provider-safe response.

The original appointment row is updated to `RESCHEDULED`, not deleted. A new `SCHEDULED` successor appointment is created for the target slot.

## Durable state transition

Rescheduling is historical-preserving, not an overwrite:

```text
old appointment -> rescheduled
old slot -> available
new appointment -> scheduled
new slot -> booked
```

The transition is atomic at the `AppointmentReschedulingService` boundary. If validation or persistence fails, no partial durable state should remain.

Manual validation (June 2026): Retell voice rescheduling completed the full flow — identity resolution, appointment selection, new availability check, hold on the new slot, explicit confirmation, and `reschedule_appointment` success. The old slot became available and could be reused for a new booking.

## Safety Boundary

The Retell adapter does not update `Appointment` directly.

It cannot bypass:

- signature verification
- conversation context
- appointment validation
- hold/slot validation
- explicit confirmation
- idempotency
- audit logging
- public demo guardrails

## Idempotency

Provider retries use `provider_call_id` and `tool_call_id` when available.

Duplicate callbacks must not duplicate reschedule side effects, successor appointments, confirmation email jobs, or audit transitions.

## Conversation Metadata

On successful reschedule, the voice adapter updates safe `voice_context` and `last_reschedule_summary` through `VoiceConversationBridgeService`:

- the latest appointment summary points to the new successor appointment
- `rescheduled_from_appointment_id` records the original appointment reference
- active hold fields are cleared
- unrelated conversation metadata keys are preserved

On recoverable failure, useful hold context is preserved so the caller can retry without re-holding.

The adapter does not store raw transcripts, raw provider payloads, secrets, or full patient identity in conversation metadata.

## Relationship to Chat

Rescheduling is implemented as a shared backend service.

Voice is only one channel into that service.

Written chat reschedule is not wired yet. See [Appointment Rescheduling Foundation](appointment-rescheduling-foundation.md).

## Future Work

Intentional product evolution (separate from current voice slice):

- written chat reschedule flow
- patient-aware hold recovery for authenticated patient sessions
- reschedule notification email templates where not yet deployed
- dynamic doctor schedule rules and admin schedule management
