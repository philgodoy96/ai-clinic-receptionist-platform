# Human Handoff Notification Job

## Context

The platform creates `HumanEscalation` records when the chat flow detects an immediate handoff signal.

A durable notification job makes the escalation actionable for staff workflows without requiring a live operator console.

## Design principle

Escalation creates an operational record.

Notification jobs make the handoff actionable.

Staff notification is modeled as a durable email job. Local/demo mode uses the fake email provider; hosted demos may use Resend when configured. This demo does not include a live operator console, Slack integration, or real-time human handoff queue.

## Current implementation

The implementation uses the existing email job and background dispatch pattern.

When an immediate chat escalation is created, the system creates a durable `human_escalation_notification` email job.

- **Fake provider (default):** job is processed by the email worker and recorded in memory; no outbound mail
- **Resend (optional):** when `EMAIL_PROVIDER=resend` is configured, the worker sends real staff notification mail to `HUMAN_ESCALATION_NOTIFICATION_EMAIL`

RabbitMQ dispatch is best-effort after commit. If RabbitMQ publish fails, the escalation and notification job remain durable in PostgreSQL.

## Notification triggers

Notification jobs are created for immediate escalation reasons:

- explicit user request for a human
- medical emergency signal

Suggested escalation signals do not automatically create notification jobs yet.

## Idempotency

Notification job creation is idempotent per escalation.

Repeated user messages or retried requests should not create duplicate staff notification jobs.

## Payload boundary

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

## Active holds

Human escalation does not automatically release active appointment holds.

If a hold exists, safe hold context may be included so staff workflows know a temporary hold was present.

The hold remains governed by Redis TTL or explicit release/cancel actions.

## Outside demo scope

- Live operator console or real-time handoff queue
- Slack or non-email notification providers
- Voice provider call transfer
