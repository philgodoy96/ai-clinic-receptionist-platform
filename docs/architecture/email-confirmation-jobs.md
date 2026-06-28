# Confirmation Email Jobs

## Context

After an appointment is confirmed or rescheduled, the platform should notify the patient by email.

The booking or reschedule request does **not** send email directly and does **not** call Resend. Instead, the request creates a durable `EmailJob` that a background worker processes through `EmailDeliveryProvider`.

## Architectural Boundary

| Layer | Responsibility |
|-------|----------------|
| Chat / API / Retell | Validate, commit appointment, enqueue `EmailJob` |
| `EmailJobService` | Durable job creation with idempotency |
| `EmailJobWorkerService` | Claim, render content, call provider, finalize state |

This matches the platform principle: the LLM understands; the backend validates and decides; domain services execute.

## Current Implementation

The implementation includes:

- `EmailJob` domain model and migration (`email_jobs` table)
- `EmailJobService` with `get_or_create_appointment_confirmation_email_job`
- Enqueue after written-chat booking, scheduling API booking, Retell voice booking, and reschedule confirmation
- `FakeEmailProvider` (default) and optional `ResendEmailProvider`
- `EmailJobWorkerService` with claim/finalize transactions, retries, and locking
- RabbitMQ wake-up dispatch (`EMAIL_JOB_DISPATCH_ENABLED`) and polling fallback worker
- Clinic-local plain-text rendering at send time (`email_job_delivery_content`)
- Provider idempotency keys for Resend

## Flow

1. Patient confirms booking or reschedule (explicit confirmation required).
2. Backend validates hold, business rules, and identity as applicable.
3. Backend creates or updates the appointment in Postgres.
4. Backend creates a pending `appointment_confirmation` `EmailJob` in the same transaction where appropriate.
5. Transaction commits; Redis hold is released.
6. (Optional) API publishes a RabbitMQ wake-up message when dispatch is enabled.
7. Worker claims the job, renders subject/body, calls the email provider.
8. Worker marks the job `sent` or schedules retry / terminal `failed`.

The patient-facing HTTP response returns after step 5. Steps 6–8 are asynchronous.

## Job Payload

Appointment confirmation jobs store operational fields on the job row and in `payload`:

- `recipient_email` — patient email when known
- `patient_name`, `doctor_name` — display names when known
- `appointment_start_time` — ISO timestamp for clinic-local rendering
- `source` — channel hint (`chat`, Retell, API, etc.)
- `confirmation_reason` — `reschedule` when the job follows a reschedule
- optional metadata such as `specialty_name`, `conversation_id`, `hold_id`, `call_id`

Payload must not include diagnosis, clinical notes, raw transcripts, payment data, or insurance identifiers.

## Idempotency

Key pattern:

```
appointment_confirmation:{appointment_id}
```

- Reschedule confirmation uses the **new** active appointment id.
- A partial unique index on `idempotency_key` prevents duplicate jobs for the same appointment.
- Concurrent or retried booking flows that call enqueue again receive the existing job.
- Resend receives the same key as `Idempotency-Key` when `EMAIL_PROVIDER=resend`.

## Failure Behavior

- **Missing recipient email:** job fails delivery with `recipient_email_missing`, retries until max attempts, then `failed`. Appointment remains confirmed.
- **Provider failure:** exponential backoff via `next_attempt_at`; terminal `failed` after `EMAIL_JOB_MAX_ATTEMPTS`.
- **Success:** `sent` with `provider_message_id` when the provider returns one.

Email delivery is best-effort and does not roll back confirmed appointments.

## Provider Configuration

| Mode | Setting | Notes |
|------|---------|-------|
| Local / CI / smoke tests | `EMAIL_PROVIDER=fake` | Default; no API keys |
| Real delivery | `EMAIL_PROVIDER=resend` | Requires `RESEND_API_KEY`, `EMAIL_FROM_ADDRESS`; opt-in only |

See [Configuration](../configuration.md) and [Email Job Worker](email-job-worker.md).

## RabbitMQ

When `EMAIL_JOB_DISPATCH_ENABLED=true`, the API publishes a minimal wake-up message (`email_job_id` only) after job creation. Postgres remains the source of truth for payload, retries, and delivery state.

When dispatch is disabled, the polling worker (`scripts/run_email_job_worker`) processes due jobs without broker wake-ups.

See [RabbitMQ Email Dispatch](rabbitmq-email-dispatch.md) and [Email Dispatch Reliability](email-dispatch-reliability.md).

## Current Limitations

Not implemented in this slice:

- cancellation confirmation emails
- rich branded HTML templates (plain text only today)
- provider bounce/webhook handling
- guaranteed exactly-once delivery across Postgres and Resend

Production Resend usage should use a verified sending domain. Public demos should enable guardrails before enabling real email.
