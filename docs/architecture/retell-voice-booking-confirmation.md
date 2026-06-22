# Retell Voice Booking Confirmation

## Context

The system supports voice interactions through Retell.

Voice booking is a critical business action, so it requires stricter validation than read-only tool calls.

## Design Principle

Voice can initiate booking only after an active hold, validated patient identity, and explicit caller confirmation.

## Current Implementation

The implementation includes:

- `book_appointment` Retell tool support
- active hold validation
- patient identity validation
- explicit confirmation validation
- idempotent provider callback handling
- delegation to `AppointmentBookingService`
- safe provider responses
- safe conversation metadata updates
- tests for duplicate callbacks, missing confirmation, missing hold, and regression safety

See also:

- [Retell Tool-Calling Adapter](retell-tool-calling-adapter.md)
- [Voice Conversation Bridge](voice-conversation-bridge.md)
- [Retell Voice Appointment Cancellation](retell-voice-cancellation.md)
- [Appointment Rescheduling Foundation](appointment-rescheduling-foundation.md)
- [Appointment Slot Holds](appointment-holds.md)
- [Public Demo Guardrails](public-demo-guardrails.md)

## Execution Flow

1. Retell sends a verified `book_appointment` tool callback.
2. The backend resolves `provider_call_id` to `VoiceCall` and `Conversation`.
3. The backend validates active hold and patient identity.
4. The backend requires explicit caller confirmation.
5. The backend delegates booking to `AppointmentBookingService`.
6. The existing booking flow persists the appointment and queues confirmation email.
7. The backend clears active hold context and returns a provider-safe response.

Booked appointments can later be canceled through a separate `cancel_appointment` voice flow that requires explicit cancellation confirmation and delegates to `AppointmentCancellationService`. See [Retell Voice Appointment Cancellation](retell-voice-cancellation.md).

Rescheduling is implemented as a shared backend service in `AppointmentReschedulingService`. Voice and chat adapters are not wired to a Retell reschedule tool yet. See [Appointment Rescheduling Foundation](appointment-rescheduling-foundation.md).

## Safety Boundary

The Retell adapter does not create appointments directly.

It cannot bypass:

- hold validation
- scheduling validation
- patient validation
- booking idempotency
- email job boundaries
- public demo guardrails

## Idempotency

Provider retries use `provider_call_id` and `tool_call_id` when available.

Duplicate callbacks must not create duplicate appointments or duplicate confirmation email jobs.

## Patient Identity

The voice flow uses the shared patient identity rules.

Phone numbers are optional only if the shared booking rules allow them.

The voice path does not store raw transcripts or audio for identity verification.

## Relationship to Chat Booking

Voice and chat both use `AppointmentBookingService`.

The voice path is a channel adapter around the same business core.

## Future Work

Future implementation phases may add:

- Retell voice reschedule tool delegating to `AppointmentReschedulingService`
- transcript summary persistence
- Retell dashboard setup runbook
- real Retell smoke test
- public deployment configuration
- frontend demo
