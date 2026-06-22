# Retell Voice Appointment Cancellation

## Context

The system supports voice interactions through Retell.

Appointment cancellation is a critical state transition and must use shared backend business rules.

## Design Principle

Voice can request cancellation, but the shared backend cancellation service owns validation, idempotency, status transitions, and auditability.

## Current Implementation

The implementation includes:

- shared `AppointmentCancellationService`
- `cancel_appointment` Retell tool support
- explicit cancellation confirmation validation
- appointment status transition validation
- idempotent provider callback handling
- safe conversation metadata updates
- safe audit logging
- provider-safe cancellation responses

See also:

- [Retell Tool-Calling Adapter](retell-tool-calling-adapter.md)
- [Retell Voice Booking Confirmation](retell-voice-booking-confirmation.md)
- [Appointment Rescheduling Foundation](appointment-rescheduling-foundation.md)
- [Voice Conversation Bridge](voice-conversation-bridge.md)
- [Public Demo Guardrails](public-demo-guardrails.md)

## Execution Flow

1. Retell sends a verified `cancel_appointment` tool callback.
2. The backend resolves `provider_call_id` to `VoiceCall` and `Conversation`.
3. The backend resolves the target appointment.
4. The backend requires explicit cancellation confirmation.
5. The backend delegates cancellation to `AppointmentCancellationService`.
6. The appointment row is updated, not deleted.
7. Conversation metadata is updated safely.
8. The backend returns a provider-safe response.

## Safety Boundary

The Retell adapter does not update `Appointment` directly.

It cannot bypass:

- signature verification
- appointment lookup
- cancelable status validation
- explicit confirmation
- idempotency
- audit logging
- public demo guardrails

## Idempotency

Provider retries use `provider_call_id` and `tool_call_id` when available.

Duplicate callbacks must not duplicate cancellation side effects.

## Relationship to Chat

Cancellation is implemented as a shared backend service.

Voice is only one channel into that service.

Rescheduling is implemented as a shared backend service in `AppointmentReschedulingService`. Voice rescheduling is wired through `reschedule_appointment` and delegates to that service. See [Retell Voice Appointment Rescheduling](retell-voice-rescheduling.md) and [Appointment Rescheduling Foundation](appointment-rescheduling-foundation.md).

## Future Work

Future implementation phases may add:

- cancellation notification email if supported by the notification system
- cancellation through written chat
- Retell dashboard setup runbook
- public deployment configuration
- real Retell smoke test
