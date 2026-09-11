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

Booked appointments can later be rescheduled through a separate `reschedule_appointment` voice flow that requires explicit reschedule confirmation, original appointment reference, and target hold or new slot before delegating to `AppointmentReschedulingService`. See [Retell Voice Appointment Rescheduling](retell-voice-rescheduling.md).

Booked appointments can also be canceled through a separate `cancel_appointment` voice flow that requires explicit cancellation confirmation and delegates to `AppointmentCancellationService`. See [Retell Voice Appointment Cancellation](retell-voice-cancellation.md).

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

Provider delivery is at-least-once. Booking uses a durable `VoiceBookingAttempt` claim
(`pending` ownership before `book_appointment`) plus adapter-level tool-execution claims.
Duplicate callbacks must not create duplicate appointments or duplicate confirmation email
jobs. Concurrent in-progress duplicates receive `booking_in_progress` / 
`tool_execution_in_progress` rather than a second booking mutation. See
[Durable Idempotency at External Voice Tool Boundaries](durable-idempotency-voice-tool-boundaries.md).

## Patient Identity

Voice booking prefers an opaque `patient_resolution_id` from `resolve_patient_identity` when the Retell tool provides one. The token must belong to the current call and be `exact_match`, confirmed `possible_match`, or `created`. Inline `patient_name` / `patient_date_of_birth` / `patient_email` remain the fallback when no token is supplied.

Phone numbers are optional only if the shared booking rules allow them.

The voice path does not store raw transcripts or audio for patient identity resolution.

See also: [Voice Patient Identity Resolution](voice-patient-identity-resolution.md).

## Relationship to Chat Booking

Voice and chat both use `AppointmentBookingService`.

The voice path is a channel adapter around the same business core.

## Future Work

Future implementation phases may add:

- transcript summary persistence
- Retell dashboard setup runbook
- real Retell smoke test
- public deployment configuration
- frontend demo
