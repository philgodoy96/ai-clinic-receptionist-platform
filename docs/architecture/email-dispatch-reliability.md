# Email Dispatch Reliability

## Context

The public demo can send appointment confirmation emails.

Email delivery uses durable jobs so booking does not depend on an external email provider being available.

## Delivery Model

Email delivery uses **at-least-once processing**.

The worker may attempt delivery more than once when:

- a worker crashes after a provider send but before Postgres records `sent`
- an expired processing lock is reclaimed
- a RabbitMQ wake-up message is delivered more than once
- a polling worker and RabbitMQ consumer race on the same due job (Postgres claim prevents duplicate concurrent sends, but retries can still occur across separate attempts)

**Exactly-once delivery across Postgres and external providers is not guaranteed.**

Design for idempotency at job creation and at the provider boundary, not for impossible end-to-end exactly-once semantics.

## Source of Truth

**Postgres `EmailJob` is the durable source of truth.**

RabbitMQ carries **wake-up messages only**. It does not store job payload, retry schedule, or delivery outcome.

RabbitMQ messages contain only:

```json
{
  "email_job_id": "<uuid>"
}
```

The worker always loads job state from Postgres before sending.

If RabbitMQ publish fails after booking commits, the durable job still exists and can be processed by the polling worker.

See also:

- [Email Job Worker](email-job-worker.md)
- [RabbitMQ Email Dispatch](rabbitmq-email-dispatch.md)
- [Email Confirmation Jobs](email-confirmation-jobs.md)

## Retry Scheduling

Retry scheduling is controlled by **`EmailJob.next_attempt_at`** in Postgres.

On provider failure with attempts remaining:

- `attempt_count` increments
- `status` returns to `pending`
- `next_attempt_at` is set using exponential backoff
- lock fields are cleared

On provider failure after max attempts:

- `status` becomes `failed`
- `next_attempt_at` is cleared
- `last_error` is preserved

**RabbitMQ TTL retry queues are not used for provider send failures.**

RabbitMQ is not the retry scheduler. Provider failures are persisted in Postgres, then the consumer acknowledges the wake-up message so RabbitMQ does not create a broker-level retry loop.

## Worker Transaction Boundaries

`EmailJobWorkerService` splits database work from external provider calls.

### Claim transaction

1. Open a database transaction.
2. Claim the job (`status=processing`, `locked_by`, `locked_until`).
3. Commit the claim transaction.

If claim fails (`not_found`, `already_sent`, `not_claimable`), the provider is not called.

### Provider call

4. Call `EmailDeliveryProvider.send()` **outside** the claim transaction.

### Finalize transaction

5. Open a second database transaction.
6. On success: mark `sent`, store `provider_message_id`, clear locks.
7. On provider failure: mark `pending` with `next_attempt_at`, or `failed` when attempts are exhausted.
8. Commit the finalize transaction.

### RabbitMQ acknowledgement

9. The RabbitMQ consumer acknowledges the wake-up message **only after** finalize state is durably committed.

If finalize fails with an unhandled database error, the message is **not** acked and is nacked with requeue so Postgres remains authoritative.

Both entry paths use the same durable processor:

- **Polling worker** — `process_due_email_jobs()` / `claim_next_available`
- **RabbitMQ consumer** — `process_email_job()` / `claim_by_id`

## Email Job State Machine

```
pending -> processing -> sent

processing -> pending when retryable failure remains

processing -> failed when max attempts are exhausted

failed -> pending only through manual retry/replay
```

`sent` jobs are not automatically replayed.

## Idempotency

### Job creation (Postgres)

Appointment confirmation jobs use a durable idempotency key:

```
appointment_confirmation:{appointment_id}
```

A partial unique index on `idempotency_key` prevents duplicate confirmation jobs for the same appointment.

Human escalation notification jobs use a similar key pattern at enqueue time.

### Provider send (Resend)

When `EMAIL_PROVIDER=resend`, the worker passes an idempotency key to Resend using the `Idempotency-Key` HTTP header.

Preferred source:

- `EmailJob.idempotency_key`

Fallback when no job key is set:

```
email_job:{email_job_id}
```

Keys longer than Resend's 256-character limit are normalized to a stable `hash:{sha256}` form.

This reduces duplicate-send risk across worker retries. It does not by itself guarantee exactly-once delivery.

## Failure Behavior

After `EMAIL_JOB_MAX_ATTEMPTS` is exhausted, jobs become **`failed`**.

Failed terminal jobs remain visible in:

- Postgres `email_jobs` records
- Email Job Debug API (`GET /api/v1/email-jobs`, filters by `status=failed`)
- operational metrics (`counts_by_status.failed`, `oldest_failed_created_at`)

Manual recovery controls (debug API):

| Endpoint | Applies to | Effect |
|----------|------------|--------|
| `POST /api/v1/email-jobs/{id}/retry` | `failed` | `pending` now, preserves `attempt_count` and `last_error` |
| `POST /api/v1/email-jobs/{id}/replay` | `failed` | `pending` now, resets `attempt_count` to 0, clears `last_error` |

Both endpoints publish a RabbitMQ wake-up message after commit when dispatch is enabled.

**Sent jobs are not automatically replayed** and are skipped safely on duplicate wake-up messages.

Malformed RabbitMQ payloads (invalid JSON, missing `email_job_id`) are acked without calling the provider so the consumer loop stays healthy. A dedicated broker DLQ is optional future work.

## Current Implementation

The implementation includes:

- fake email provider by default
- optional Resend email provider with idempotency keys
- durable `EmailJob` records with claim/lock fields
- split claim/finalize worker transactions
- RabbitMQ wake-up messages (`email_job_id` only)
- polling fallback worker (`scripts/run_email_job_worker.py`)
- RabbitMQ consumer (`scripts/run_email_job_consumer.py`)
- exponential backoff via `next_attempt_at`
- manual retry/replay controls
- Email Job Debug API and operational metrics

## Resend Provider

Resend is optional.

Local development and CI use the fake email provider.

Hosted public demo example:

```env
EMAIL_PROVIDER=resend
RESEND_API_KEY=...
EMAIL_FROM_ADDRESS=...
```

Secrets (`RESEND_API_KEY`, raw authorization headers) must not appear in logs or API responses. Only safe provider metadata such as `provider_message_id` is stored on the job record.

## Public Demo Safety

Public demo email quotas should be enabled before using a real provider.

See [Public Demo Guardrails](public-demo-guardrails.md).

## Future Work

- provider webhook and bounce handling
- dedicated RabbitMQ DLQ for malformed broker messages (invalid payloads are acked today)
- delivery metrics dashboard
- alerting for terminal `failed` jobs in production
- HTML email templates
- provider-specific rate limit classification
