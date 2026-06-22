# Appointment Rescheduling Foundation

## Context

The system supports booking and cancellation.

Rescheduling is a critical shared business transition.

A reschedule must validate the original appointment, reserve a new target slot or hold, require explicit confirmation, preserve historical traceability, and return a safe idempotent result without deleting the original appointment row.

## Design Principle

Rescheduling is a shared business transition, not a voice-specific shortcut.

Channel adapters may initiate a reschedule request, but `AppointmentReschedulingService` owns validation, durable transition, idempotency, auditability, and safe conversation metadata updates.

## Current Implementation

The implementation includes:

- shared `AppointmentReschedulingService`
- original appointment validation
- new slot/hold validation
- explicit confirmation validation
- idempotency through `AppointmentRescheduleAttempt`
- safe audit logging
- safe conversation metadata updates

Supporting domain and persistence pieces include:

- `AppointmentReschedulingRequest` and `AppointmentReschedulingResult`
- reschedulable status rules for `SCHEDULED` and `RESCHEDULED` appointments
- `rescheduled_from_appointment_id` traceability on the successor appointment
- audit events: `appointment_reschedule_requested`, `appointment_reschedule_succeeded`, `appointment_reschedule_rejected`, and `appointment_reschedule_duplicate`
- confirmation email enqueue for the new appointment through the existing `EmailJobService` boundary

See also:

- [Scheduling Domain](scheduling-domain.md)
- [Appointment Slot Holds](appointment-holds.md)
- [Retell Voice Booking Confirmation](retell-voice-booking-confirmation.md)
- [Retell Voice Appointment Cancellation](retell-voice-cancellation.md)
- [Voice Conversation Bridge](voice-conversation-bridge.md)

## Flow

1. Receive request from a channel/application adapter.
2. Validate original appointment.
3. Validate target slot/hold.
4. Require explicit confirmation.
5. Perform durable transition.
6. Preserve traceability.
7. Update metadata safely.
8. Return safe result.

### Durable Transition Rules

- The original appointment row is updated to `RESCHEDULED`, not deleted.
- A new `SCHEDULED` appointment is created for the target slot.
- The successor appointment stores `rescheduled_from_appointment_id` pointing to the original appointment.
- The previous availability slot may be released when the original appointment referenced one.
- Duplicate requests with the same idempotency key return the prior result without duplicating appointments, audit side effects, or confirmation email jobs.

## Safety Boundary

Channel adapters must call `AppointmentReschedulingService`.

They must not update appointment records directly.

They must not bypass:

- original appointment lookup
- reschedulable status validation
- target slot or hold validation
- explicit confirmation
- idempotency
- audit logging
- email job boundaries

## Idempotency

Reschedule attempts are keyed by `idempotency_key`.

The repository records attempt lifecycle states (`pending`, `succeeded`, `rejected`, `failed`) and handles unique-constraint races safely.

Duplicate requests must not:

- create a second successor appointment
- duplicate confirmation email jobs
- duplicate successful audit transitions

## Relationship to Channels

Voice, chat, and API adapters are expected to remain thin.

They resolve channel context, build `AppointmentReschedulingRequest`, and delegate to the shared service.

Voice and chat may update `voice_context` or `chat_context` only through the existing safe conversation metadata merge rules after the service succeeds.

## Future Work

Future implementation phases may add:

- Retell voice reschedule tool
- written chat reschedule flow
- reschedule notification email if supported
- public demo deployment configuration
- Retell dashboard setup runbook
