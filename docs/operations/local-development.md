# Local Development

## Services

The local Docker environment includes:

- PostgreSQL
- Redis
- RabbitMQ

RabbitMQ Management UI:

    http://localhost:15672

Default credentials:

    guest / guest

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

    RABBITMQ_URL=amqp://guest:guest@localhost:5672/

Queue name:

    EMAIL_JOB_QUEUE_NAME=email_jobs