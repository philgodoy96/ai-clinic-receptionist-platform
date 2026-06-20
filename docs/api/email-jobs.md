# Email Job Debug API

## Context

The Email Job Debug API exposes operational state for durable confirmation email jobs.

It is intended for local development, debugging, and future admin/operator workflows.

## Security Boundary

This endpoint is intended for local development and portfolio demonstration.

It currently does not include authentication or RBAC.

In a production deployment, this endpoint must be protected behind admin authentication, role-based authorization, and network-level access controls.

## Endpoints

List jobs:

    GET /api/v1/email-jobs

Get one job:

    GET /api/v1/email-jobs/{email_job_id}

Retry failed job:

    POST /api/v1/email-jobs/{email_job_id}/retry

Replay dead-letter job:

    POST /api/v1/email-jobs/{email_job_id}/replay

Operational metrics:

    GET /api/v1/email-jobs/metrics

## Metrics

Example response:

    {
      "total_jobs": 10,
      "counts_by_status": {
        "pending": 2,
        "processing": 1,
        "sent": 5,
        "failed": 1,
        "dead_letter": 1
      },
      "locked_count": 1,
      "expired_lock_count": 0,
      "overdue_pending_count": 2,
      "oldest_pending_created_at": "2026-07-01T10:00:00Z",
      "oldest_failed_created_at": "2026-07-01T10:05:00Z",
      "newest_dead_letter_created_at": "2026-07-01T10:10:00Z"
    }

Metrics help inspect:

- worker backlog
- failed jobs
- dead-letter accumulation
- expired locks
- jobs ready for processing

The metrics endpoint returns aggregate counts and timestamps only. It does not expose job payload, email body, subject, recipient details, or other clinical or patient-identifying data. Use the list or get-by-id endpoints when per-job detail is required for debugging.

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

## Manual Controls

Retry failed job:

    POST /api/v1/email-jobs/{email_job_id}/retry

Replay dead-letter job:

    POST /api/v1/email-jobs/{email_job_id}/replay

Retry is only valid for jobs with status `failed`.

Replay is only valid for jobs with status `dead_letter`.

Retry reuses the same job and schedules it for another attempt.

Replay creates a new pending job and preserves the original dead-letter job for investigation.

## Retry vs Replay

Retry:

- Applies to failed jobs
- Reuses the same job ID
- Preserves attempts
- Preserves last_error until the next successful send
- Schedules the job immediately

Replay:

- Applies to dead_letter jobs
- Creates a new job
- Preserves the original dead_letter job
- Resets attempts to 0
- Adds replay metadata to payload

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

The metrics endpoint is intentionally narrower: it exposes only operational aggregates (status counts, lock health, backlog signals, and created-at timestamps). It never returns payload, body, subject, recipient_email, or appointment/patient identifiers. This keeps operator visibility useful without widening the clinical data surface.

## Current Limitations

This endpoint does not yet include:

- Authentication
- Role-based authorization
- Admin UI
- Export support
- Date range filters