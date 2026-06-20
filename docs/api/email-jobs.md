# Email Job Debug API

## Context

The Email Job Debug API exposes operational state for durable confirmation email jobs.

It is intended for local development, debugging, and future admin/operator workflows.

This API currently does not include authentication or RBAC because the project is still in local/demo mode.

Production hardening should protect this endpoint.

## Endpoints

List jobs:

    GET /api/v1/email-jobs

Get one job:

    GET /api/v1/email-jobs/{email_job_id}

## Pagination

The list endpoint uses cursor pagination.

Default limit:

    50

Maximum limit:

    100

Example:

    GET /api/v1/email-jobs?limit=50

When `next_cursor` is returned, pass it to fetch the next page:

    GET /api/v1/email-jobs?limit=50&cursor=<next_cursor>

## Ordering

Email jobs are ordered by:

    created_at DESC, id DESC

The ID acts as a deterministic tie-breaker when multiple jobs share the same timestamp.

## Filters

Supported optional filters:

- job_type
- status
- appointment_id
- patient_id

Examples:

    GET /api/v1/email-jobs?status=pending

    GET /api/v1/email-jobs?status=dead_letter

    GET /api/v1/email-jobs?job_type=appointment_confirmation

## Response Fields

Each item includes:

- id
- job_type
- status
- appointment_id
- patient_id
- recipient_email
- subject
- body
- attempts
- max_attempts
- locked_by
- locked_until
- last_error
- payload
- scheduled_for
- sent_at
- created_at
- updated_at

## Debugging Use Cases

Use this API to inspect:

- pending jobs waiting for worker execution
- failed jobs waiting for retry
- dead_letter jobs requiring investigation
- processing jobs with active locks
- expired locks after worker crashes
- provider failure messages

## Privacy Boundary

Email job payload should not contain:

- Diagnosis
- Clinical notes
- Raw transcripts
- Payment data
- Insurance identifiers

The payload is intended for operational metadata such as source, hold_id, call_id, and conversation_id.

## Current Limitations

This endpoint does not yet include:

- Authentication
- Role-based authorization
- Admin UI
- Manual retry endpoint
- Manual dead-letter replay endpoint
- Export support
- Date range filters