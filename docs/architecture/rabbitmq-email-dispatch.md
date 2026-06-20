# RabbitMQ Email Dispatch

## Context

The platform creates durable email jobs after appointment booking.

RabbitMQ is used as a dispatch mechanism to wake workers after a job is committed.

RabbitMQ is not the source of truth for job state.

PostgreSQL remains responsible for:

- job status
- attempts
- locked_by
- locked_until
- sent_at
- last_error
- dead_letter state

## Flow

1. Booking creates an appointment.
2. Booking creates an audit log.
3. Booking creates a pending email job.
4. PostgreSQL transaction commits.
5. The API attempts to publish an email job dispatch message to RabbitMQ.
6. If publish fails, the booking still succeeds because the job is durable.
7. RabbitMQ consumer receives the message.
8. Consumer invokes the email job worker.
9. Worker claims an available job from PostgreSQL.
10. Worker sends email through the provider.
11. Worker marks the job as sent, failed, or dead_letter.

## Message Shape

RabbitMQ dispatch messages contain:

    {
      "type": "email_job_ready",
      "email_job_id": "..."
    }

The email_job_id is included for observability.

The worker still claims jobs from PostgreSQL because PostgreSQL is the durable source of truth.

## Why Publish After Commit

Publishing before commit can wake a worker before the job exists durably.

The correct order is:

    create appointment
    create audit log
    create email job
    commit PostgreSQL transaction
    publish RabbitMQ dispatch message

## Best-Effort Publishing

Publishing is best-effort.

If RabbitMQ is unavailable after the booking commits, the durable job still exists in PostgreSQL.

A polling worker, manual worker run, or future reconciliation process can still process the job.

This avoids failing a successful booking because a dispatch optimization failed.

## Consumer Ack/Nack

The consumer acknowledges messages after the worker completes one processing attempt.

Unexpected consumer failures use nack with requeue.

The worker itself handles provider failures by marking jobs as failed or dead_letter in PostgreSQL.

## Current Limitations

This implementation does not yet include:

- Dedicated DLQ exchange/queue
- Retry queues
- Exponential backoff
- Real email provider
- Provider idempotency keys
- Metrics
- Worker health checks