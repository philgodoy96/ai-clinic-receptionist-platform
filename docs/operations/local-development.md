# Local Development

## Services

The local Docker environment includes:

- PostgreSQL
- Redis
- RabbitMQ

RabbitMQ Management UI:

    http://localhost:15672

Default credentials:

    clinic / clinic

## Start Services

    docker compose up -d postgres redis rabbitmq

## Run Migrations

    python -m alembic upgrade head

## Seed Demo Data

    python -m scripts.seed_demo_data

Seeds specialties, doctors, patients, and availability slots. Availability is generated in **clinic local time** (`CLINIC_TIMEZONE`, default `America/New_York`) at 10:00, 11:00, 14:00, and 15:00 on seven rolling business days (today when future slots remain, otherwise starting tomorrow). Timestamps are stored in UTC per the scheduling schema. Re-running the script is idempotent.

## Refresh Demo Availability Through Booking Horizon

After seeding, extend future slots through the configured booking horizon (`SCHEDULING_BOOKING_HORIZON_DAYS`, default 14):

    python -m app.scripts.generate_demo_availability

Safe to run before local or public demo smoke tests. Re-running is idempotent (`slots_created=0` on subsequent runs when slots already exist). See [Demo Availability Generation](demo-availability-generation.md).

## Run API

Local development with auto-reload:

    python -m uvicorn app.main:app --reload

Production-style API command (also used as the Docker default):

    python -m uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}

## Run Email Job Worker Once

    python -m scripts.run_email_job_worker --once

## Run RabbitMQ Email Consumer

    python -m scripts.run_email_job_consumer

## Production Container Commands

The Docker image defaults to the API command above without dev reload. Use the same
image with a different `command` for background workers:

    python -m scripts.run_email_worker

For local polling fallback instead of the RabbitMQ consumer:

    python -m scripts.run_email_job_worker

See [Public Demo Deployment Runbook](public-demo-deployment.md) for hosted deploy steps, health checks, smoke tests, and troubleshooting.

## Email Dispatch Configuration

RabbitMQ dispatch is controlled by:

    EMAIL_JOB_DISPATCH_ENABLED

When false, the API uses a noop publisher.

When true, the API publishes a RabbitMQ dispatch message after booking commits.

RabbitMQ URL:

    RABBITMQ_URL=amqp://clinic:clinic@localhost:5672/

Queue name:

    EMAIL_JOB_QUEUE_NAME=email_jobs

## Email Provider Configuration

Email settings are documented in `docs/configuration.md`.

### Provider modes

| Mode | Setting | When to use |
|------|---------|-------------|
| Fake (default) | `EMAIL_PROVIDER=fake` | Local development, CI, and manual smoke tests. No Resend API key required. |
| Resend (opt-in) | `EMAIL_PROVIDER=resend` | Real outbound mail. Set `RESEND_API_KEY` and `EMAIL_FROM_ADDRESS` locally or in a secret manager — never commit real keys. |

Local development and CI should keep:

    EMAIL_PROVIDER=fake

The fake provider records outbound messages in memory and returns a synthetic `provider_message_id`. No external mail is sent.

Optional Resend configuration for controlled testing or a hosted public demo:

    EMAIL_PROVIDER=resend
    RESEND_API_KEY=re_...
    EMAIL_FROM_ADDRESS=Clinic <noreply@example.com>

Enable `PUBLIC_DEMO_GUARDRAILS_ENABLED` before using a real email provider on an internet-facing deployment.

### Runtime note

Creating a booking or reschedule only inserts a pending `EmailJob`. You must run a worker for delivery:

- **Polling (local default):** `python -m scripts.run_email_job_worker --once` or `python -m scripts.run_email_job_worker`
- **RabbitMQ dispatch:** `EMAIL_JOB_DISPATCH_ENABLED=true` on the API plus `python -m scripts.run_email_job_consumer` or `python -m scripts.run_email_worker`

See `docs/architecture/email-dispatch-reliability.md` for the full reliability model.

### Appointment confirmation smoke tests

#### Fake provider

1. Set `EMAIL_PROVIDER=fake`.
2. Confirm an appointment through chat or the scheduling API (patient must have an email).
3. Run `python -m scripts.run_email_job_worker --once`.
4. Inspect the `email_jobs` row (or Email Job Debug API): status `sent`, `recipient_email` set, subject/body rendered, `provider_message_id` present (for example `fake-0`).

#### Resend provider

1. Set `EMAIL_PROVIDER=resend`, `RESEND_API_KEY`, and `EMAIL_FROM_ADDRESS` in your local `.env` (do not commit).
2. Start the API and an email worker/consumer.
3. Confirm a booking to an inbox you control.
4. Verify the email arrives and the `EmailJob` is `sent` with a Resend `provider_message_id`.

Fake provider remains recommended for day-to-day local development.

## LLM Provider Configuration

LLM settings are documented in `docs/configuration.md`.

Local development and CI should keep:

    LLM_PROVIDER=fake

No Groq API key is required for the default local flow.

Optional Groq configuration is for hosted public demo only. See `docs/architecture/groq-llm-provider.md`.

Optional Bedrock configuration requires runtime AWS credentials and is not needed for the default demo flow.

## Retell Webhook Security

Retell settings are documented in `docs/configuration.md`.

Local development and CI should keep:

```env
RETELL_ENABLED=false
RETELL_ALLOW_INSECURE_WEBHOOKS=false
```

Protected Retell tool routes under `/api/v1/retell/tools/*` reject requests when Retell is disabled. Non-Retell routes such as `/health` and chat APIs do not require Retell signatures.

To exercise Retell tool routes locally without real Retell credentials:

```env
RETELL_ENABLED=true
RETELL_ALLOW_INSECURE_WEBHOOKS=true
RETELL_WEBHOOK_SECRET=test-webhook-secret
```

`RETELL_ALLOW_INSECURE_WEBHOOKS=true` is only valid when `APP_ENV` is `local`, `test`, or `development`.

See `docs/architecture/retell-webhook-security.md` for verification flow, safety boundary, and hosted demo configuration.

## Public Demo Guardrails

Public demo guardrails are **disabled by default** in local development.

Default local values:

```env
PUBLIC_DEMO_MODE=false
PUBLIC_DEMO_GUARDRAILS_ENABLED=false
```

When guardrails are disabled, chat and Retell tool routes work without Redis rate-limit enforcement. Redis is still used for appointment holds.

To test guardrails locally:

```env
PUBLIC_DEMO_GUARDRAILS_ENABLED=true
DEMO_CHAT_MESSAGES_PER_MINUTE_PER_IP=1
```

Then send two chat requests quickly — the first should succeed and the second should return `429`.

See `docs/configuration.md` for all guardrail variables and `docs/architecture/public-demo-guardrails.md` for design details.

## Written-Chat Appointment Management

The written-chat receptionist supports booking, scheduled appointment lookup, rescheduling, and cancellation through `POST /api/v1/chat/messages`.

Recommended local settings for reproducible manual testing:

```env
LLM_PROVIDER=fake
EMAIL_PROVIDER=fake
CHAT_TURN_UNDERSTANDING_INTERPRETER=fake
RETELL_ENABLED=false
APPOINTMENT_HOLD_TTL_SECONDS=300
CHAT_APPOINTMENT_HOLD_TTL_SECONDS=600
```

Never commit real API keys. Copy from `.env.example` only.

Manual QA script: [Chat Appointment Management Manual Testing](../testing/chat-appointment-management.md).

Architecture: [Chat Appointment Management](../architecture/chat-appointment-management.md).

Retell/voice remains a separate provider-driven path and is not required for written-chat testing.