# Audit Logs

## Context

The AI Clinic Receptionist Platform records durable audit events for important scheduling actions.

Audit logs help answer operational questions such as:

- Which call created an appointment hold?
- Why did a booking fail?
- Which appointment was confirmed?
- Did the action come from the generic API or a Retell tool?

Audit logs are not a replacement for structured application logs.

Structured logs are used for runtime debugging and observability.

Audit logs are durable business event records.

## Current Event Types

The current implementation records:

- appointment_hold_created
- appointment_hold_failed
- appointment_booking_confirmed
- appointment_booking_failed

## Stored Fields

Audit logs include:

- event_type
- outcome
- actor_type
- actor_id
- source
- request_id
- call_id
- conversation_id
- patient_id
- appointment_id
- availability_slot_id
- event_metadata
- created_at

## Privacy Boundary

Audit logs should not store:

- Diagnosis
- Clinical notes
- Unnecessary patient details
- Raw transcripts by default
- Payment data
- Insurance identifiers

Audit logs may store stable IDs and operational metadata needed for traceability.

## Pagination

Audit log listing is intentionally not included in this implementation phase.

A future implementation phase should expose audit logs using cursor pagination ordered by:

    created_at DESC, id DESC

Cursor pagination is preferred because audit logs grow over time and are naturally time-ordered.