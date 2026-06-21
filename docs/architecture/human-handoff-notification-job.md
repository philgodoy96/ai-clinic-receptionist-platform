# Human Handoff Notification Job

## Context

The platform can create `HumanEscalation` records when the chat flow detects an immediate handoff signal.

This implementation phase adds a durable notification job so the escalation can become actionable by future staff workflows.

## Design Principle

Escalation creates an operational record.

Notification jobs make the handoff actionable.

The demo still does not include a real staff inbox, Slack integration, live human chat, or human dashboard.

## Current Implementation

The current implementation uses the existing email job/background dispatch pattern.

When an immediate chat escalation is created, the system creates a durable notification job for future staff notification.

RabbitMQ dispatch is best-effort after commit.

If RabbitMQ publish fails, the escalation and notification job remain durable in PostgreSQL.

## Notification Triggers

Notification jobs are created for immediate escalation reasons:

- explicit user request for a human
- medical emergency signal

Suggested escalation signals do not automatically create notification jobs yet.

## Idempotency

Notification job creation is idempotent per escalation.

Repeated user messages or retried requests should not create duplicate staff notification jobs.

## Payload Boundary

Notification payloads may include:

- human escalation id
- conversation id
- reason
- priority
- source
- safe summary
- safe handoff context

Notification payloads must not include:

- raw user message
- full patient identity
- raw LLM prompt
- raw LLM output
- clinical diagnosis

## Active Holds

Human escalation does not automatically release active appointment holds.

If a hold exists, safe hold context may be included so future staff workflows know a temporary hold was present.

The hold remains governed by Redis TTL or explicit release/cancel actions.

## Future Work

Future implementation phases may add:

- real staff email provider
- Slack notification provider
- staff assignment
- escalation dashboard
- voice provider call transfer
- escalation resolution audit expansion