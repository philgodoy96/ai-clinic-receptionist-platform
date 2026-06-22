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

## Run API

    python -m uvicorn app.main:app --reload

## Run Email Job Worker Once

    python -m scripts.run_email_job_worker --once

## Run RabbitMQ Email Consumer

    python -m scripts.run_email_job_consumer

## Email Dispatch Configuration

RabbitMQ dispatch is controlled by:

    EMAIL_JOB_DISPATCH_ENABLED

When false, the API uses a noop publisher.

When true, the API publishes a RabbitMQ dispatch message after booking commits.

RabbitMQ URL:

    RABBITMQ_URL=amqp://clinic:clinic@localhost:5672/

Queue name:

    EMAIL_JOB_QUEUE_NAME=email_jobs

## LLM Provider Configuration

LLM settings are documented in `docs/configuration.md`.

Local development and CI should keep:

    LLM_PROVIDER=fake

Optional Bedrock configuration requires runtime AWS credentials and is not needed for the default demo flow.

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