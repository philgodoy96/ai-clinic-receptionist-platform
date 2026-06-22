# Email Dispatch Reliability

## Context

The public demo can send appointment confirmation emails.

Email delivery uses durable jobs so booking does not depend on an external email provider being available.

## Design Principle

Postgres is the source of truth.

RabbitMQ wakes workers up.

Email delivery must be idempotent, retryable, and safe to run in a public demo.

## Current Implementation

The implementation includes:

- fake email provider by default
- optional Resend email provider
- durable EmailJob records
- appointment confirmation idempotency key
- RabbitMQ wake-up messages
- worker claim/lock behavior
- retry policy with exponential backoff
- max attempts
- `next_attempt_at`
- manual retry/replay controls

See also:

- [Email Job Worker](email-job-worker.md)
- [RabbitMQ Email Dispatch](rabbitmq-email-dispatch.md)
- [Email Confirmation Jobs](email-confirmation-jobs.md)

## Email Job State Machine

```
pending -> processing -> sent

processing -> pending when retryable failure remains

processing -> failed when max attempts are exhausted

failed -> pending only through manual retry/replay
```

## Idempotency

Appointment confirmation email jobs use:

```
appointment_confirmation:{appointment_id}
```

This prevents duplicate confirmation emails for the same appointment.

## RabbitMQ Boundary

RabbitMQ messages should contain only the durable `email_job_id`:

```json
{
  "email_job_id": "<uuid>"
}
```

The worker always reads the job from Postgres before sending.

Duplicate RabbitMQ messages are safe because sent jobs are ignored.

## Resend Provider

Resend is optional.

Local development and CI use the fake email provider.

Hosted public demo can use:

```env
EMAIL_PROVIDER=resend
RESEND_API_KEY=...
EMAIL_FROM_ADDRESS=...
```

## Public Demo Safety

Public demo email quotas should be enabled before using a real provider.

See [Public Demo Guardrails](public-demo-guardrails.md).

## Future Work

Future implementation phases may add:

- HTML email templates
- bounce/webhook handling
- provider-specific rate limit classification
- email status events
- delivery metrics dashboard
- dead-letter queue
