# Audit Logs

## Context

The AI Clinic Receptionist Platform records durable audit events for important scheduling actions.

Audit logs help answer operational and product questions such as:

- Which call created an appointment hold?
- Why did a booking fail?
- Which appointment was confirmed?
- Did the request come from the generic API or a Retell tool?
- Which patient or slot was affected?

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

## Sources

Current sources include:

- scheduling_api
- retell_tool

## Actor Types

Current actor types include:

- api
- retell
- chat
- patient
- system

## Request Correlation

Audit logs include request_id.

When an audit log is recorded during an HTTP request and no explicit request_id is provided, AuditLogService reads the current request_id from request context.

This allows operators to correlate:

- API logs
- Retell tool calls
- Audit events
- Failure responses

Retell flows should also include call_id and conversation_id when available.

## Privacy Boundary

Audit logs should not store:

- Diagnosis
- Clinical notes
- Unnecessary patient details
- Raw transcripts by default
- Payment data
- Insurance identifiers

Audit logs may store stable IDs and operational metadata needed for traceability.

## Transaction Boundary

Successful booking audit events should be committed with the booking transaction when possible.

Failure audit events are best-effort and should not change the user-facing response if audit logging itself fails.

Redis and PostgreSQL do not participate in one distributed transaction.

Audit logs are stored in PostgreSQL as durable operational records.

## Cursor Pagination

Audit log listing uses cursor pagination ordered by:

    created_at DESC, id DESC

The cursor contains the last returned record's timestamp and ID encoded as an opaque string.

Cursor pagination is preferred over offset pagination because audit logs grow over time and new records may be inserted while an operator is paging through results.

## Current Limitations

The audit log API does not yet include:

- Authentication
- Role-based access control
- Date range filtering
- Export tooling
- Admin UI
- Retention policies