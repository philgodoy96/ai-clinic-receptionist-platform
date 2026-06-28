# Email Job Worker

## Context

Appointment booking and reschedule confirmation create durable pending email jobs.

The scheduling API, Retell booking tool, and written-chat booking confirmation flow enqueue `appointment_confirmation` jobs after a booking or reschedule is committed. Immediate human escalations from chat can enqueue durable `human_escalation_notification` jobs after escalation creation.

The email job worker processes those jobs asynchronously through `EmailDeliveryProvider` (`FakeEmailProvider` by default, optional `ResendEmailProvider`).

**Chat, scheduling API, and Retell must not call Resend directly.** Only the worker invokes the provider. This keeps booking fast and avoids coupling user-facing latency to email provider availability.

## Delivery Guarantees

Email delivery uses **at-least-once processing**. Exactly-once delivery across Postgres and external providers is not guaranteed.

See [Email Dispatch Reliability](email-dispatch-reliability.md) for the full reliability model.

## Current Implementation

The current implementation includes:

- `OutboundEmailMessage` value object with optional `idempotency_key`
- `EmailDeliveryProvider` protocol
- `FakeEmailProvider` (default)
- optional `ResendEmailProvider` with idempotency keys
- `EmailJobWorkerService` with split claim/finalize transactions
- SQLAlchemy worker repository claim/lock methods
- polling CLI worker (`scripts/run_email_job_worker.py`)
- RabbitMQ dispatch consumer (`scripts/run_email_job_consumer.py`)
- retry policy with exponential backoff and `next_attempt_at`
- worker locking using `locked_by` and `locked_until`
- Email Job Debug API with cursor pagination
- manual retry/replay controls for `failed` jobs
- Email job operational metrics endpoint

## Supported Job Types

The worker currently handles these `job_type` values:

- `appointment_confirmation` — renders plain-text confirmation from job payload fields, then sends through the delivery provider
- `human_escalation_notification` — renders a safe demo staff notification from operational payload fields, then sends through the provider

Unknown job types fail with the existing retry and terminal `failed` behavior.

### Appointment confirmation

Created after:

- written-chat booking confirmation
- scheduling API booking
- Retell voice booking confirmation
- reschedule confirmation (for the **new** active appointment)

Jobs include `recipient_email`, `patient_name`, `doctor_name`, and `appointment_start_time` when the domain layer has them. Content is rendered at send time with clinic-local datetime formatting (`CLINIC_TIMEZONE`). Reschedule jobs may include `confirmation_reason=reschedule` for a distinct subject line.

Sending is **best-effort**. A confirmed appointment is not rolled back when email delivery fails.

### Human escalation notification

Human escalation notification rendering includes escalation id, conversation id, reason, priority, source, summary, and selected handoff context such as active hold presence and selected doctor/date/time.

It does not include raw user messages, patient identity, raw LLM output, or raw prompts.

## Worker Flow

### Claim transaction

1. Worker claims the next available job (polling) or a specific job id (RabbitMQ wake-up).
2. Repository eligibility rules skip `sent`, terminal `failed`, future `next_attempt_at`, and active locks held by another worker.
3. Repository claims an eligible job by setting:
   - `status = processing`
   - `locked_by = worker_id`
   - `locked_until = now + lock_duration`
4. **Commit the claim transaction.**

### Provider call (outside database transaction)

5. Build the delivery message (including idempotency key when available).
6. Call `EmailDeliveryProvider.send()`.

### Finalize transaction

7. On success: mark `sent`, store `provider_message_id`, clear locks.
8. On failure: increment `attempt_count`, set `last_error`, return to `pending` with `next_attempt_at`, or mark `failed` when attempts are exhausted.
9. **Commit the finalize transaction.**

### RabbitMQ consumer

10. Acknowledge the RabbitMQ wake-up message only after finalize state is durably committed.

## RabbitMQ Boundary

RabbitMQ is used to wake workers.

PostgreSQL remains the source of truth for job state.

The RabbitMQ message contains only `email_job_id`. The worker still claims jobs from PostgreSQL to preserve durable locking and retry semantics.

## State Transitions

Supported transitions:

```
pending -> processing -> sent
pending -> processing -> pending   (retryable provider failure)
pending -> processing -> failed  (attempts exhausted)
failed  -> pending                 (manual retry/replay only)
```

`sent` jobs are not automatically retried.

## Crash Recovery

If a worker crashes while processing a job, the job may remain in `processing` with `locked_by` and `locked_until`.

Once `locked_until` expires, another worker can reclaim the job.

Because delivery is at-least-once, a crash after a successful provider send but before finalize commit can still produce a duplicate send on reclaim unless provider idempotency prevents it.

## Manual Recovery Controls

When automatic worker retries are not enough, operators can use the Email Job Debug API.

### Failed job retry

    POST /api/v1/email-jobs/{email_job_id}/retry

Retry applies only to jobs with status `failed`.

The service reuses the same job record:

- `status` becomes `pending`
- `next_attempt_at` is set to now
- `locked_by` and `locked_until` are cleared
- `attempt_count` and `last_error` are preserved

After the database commit, the API publishes a RabbitMQ wake message when dispatch is enabled.

### Failed job replay

    POST /api/v1/email-jobs/{email_job_id}/replay

Replay applies only to jobs with status `failed`.

The service resets the same job record for another delivery attempt:

- `status` becomes `pending`
- `next_attempt_at` is set to now
- `attempt_count` resets to `0`
- `last_error` is cleared
- lock fields are cleared

After the database commit, the API publishes a RabbitMQ wake message when dispatch is enabled.

## Operational Metrics

The Email Job Debug API exposes aggregate operational metrics at:

    GET /api/v1/email-jobs/metrics

`counts_by_status.failed` and `oldest_failed_created_at` help detect terminal delivery failures that need operator attention.

`overdue_pending_count` counts `pending` jobs whose `next_attempt_at` is due now or earlier.

`expired_lock_count` counts `processing` jobs whose lock has expired, which often indicates a worker crash before finalize.

See `docs/api/email-jobs.md` for the response shape.

## Idempotency

Appointment confirmation jobs use:

```
appointment_confirmation:{appointment_id}
```

Postgres enforces uniqueness on `idempotency_key` for appointment confirmation jobs. Duplicate enqueue calls (for example idempotent booking retries) return the existing job instead of creating a second row.

When using Resend, the worker passes `EmailJob.idempotency_key` (or `email_job:{id}` fallback) as the Resend `Idempotency-Key` header.

Duplicate RabbitMQ wake-up messages for already `sent` jobs are ignored without calling the provider. Worker crash after a successful provider send but before finalize may reclaim the job; provider idempotency reduces duplicate-send risk on that path.

## Failure Behavior

| Outcome | Job state | Appointment |
|---------|-----------|-------------|
| Missing `recipient_email` | `last_error=recipient_email_missing`, retries then `failed` | Remains confirmed |
| Provider error | `pending` with `next_attempt_at`, or `failed` after max attempts | Remains confirmed |
| Provider success | `sent`, `provider_message_id` set when returned | Remains confirmed |

Terminal `failed` jobs are visible in Postgres and the Email Job Debug API. Operators can retry or replay through the debug API when dispatch is enabled.

## Manual Smoke Checklists

### Fake provider

1. Set `EMAIL_PROVIDER=fake`.
2. Confirm an appointment through chat or the scheduling API.
3. Run the email worker (`python -m scripts.run_email_job_worker --once` locally, or the hosted worker command).
4. Confirm the `EmailJob` moves to `sent` with `recipient_email`, rendered subject/body, and a `provider_message_id` (for example `fake-0`).

### Resend provider

1. Set `EMAIL_PROVIDER=resend`.
2. Set `RESEND_API_KEY` and `EMAIL_FROM_ADDRESS` in the deployment secret manager (not in Git).
3. Start API and worker/consumer processes.
4. Confirm a booking with a patient email that can receive mail.
5. Verify the message arrives in the inbox.
6. Confirm the `EmailJob` status is `sent` and `provider_message_id` is populated.

Do not use real API keys in documentation or committed env files.

## Runtime Requirements

| Component | Polling path | RabbitMQ dispatch path |
|-----------|--------------|------------------------|
| API | Creates `EmailJob` on booking/reschedule | Same, plus publishes wake-up when `EMAIL_JOB_DISPATCH_ENABLED=true` |
| Worker | `scripts/run_email_job_worker` | `scripts/run_email_worker` or `scripts/run_email_job_consumer` |
| RabbitMQ | Not required | Required; API and worker must share `EMAIL_JOB_QUEUE_NAME` |
| Resend | Required only when `EMAIL_PROVIDER=resend` | Same |

See [Configuration](../configuration.md) for all email-related environment variables.

## Current Limitations

This implementation does not yet include:

- **cancellation confirmation emails** — only booking and reschedule confirmations are enqueued today
- **rich branded HTML templates** — appointment mail is plain text rendered at send time
- provider webhook/bounce handling
- dedicated RabbitMQ DLQ exchange/queue (malformed payloads are acked today)
- delivery metrics dashboard
- production alerting for terminal `failed` jobs
- Prometheus/Grafana integration for metrics export

Production deployments should use a **verified sending domain** with Resend. Public demos should enable auth, rate limits, and cost controls (`PUBLIC_DEMO_GUARDRAILS_ENABLED`) before turning on real email. **Fake provider remains recommended for local development.**
