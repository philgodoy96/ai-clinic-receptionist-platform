# Human Escalation Foundation

> Staff notification is modeled as a durable email job. Local/demo mode uses the fake email provider; hosted demos may use Resend when configured. Human escalation is simulated in this demo. This demo does not include a live operator console or real-time human handoff queue.

## Context

The AI Clinic Receptionist Platform supports deterministic chat booking, optional LLM-assisted slot filling, and conversation health signals.

When the conversation should not continue as automation, the system creates an operational handoff record.

## Design principle

Human escalation is operational handoff, not AI roleplay.

The system must not use another LLM pretending to be a human receptionist.

## Current implementation

The current implementation includes:

- `HumanEscalation` persistence model
- `HumanEscalationService`
- idempotent active escalation creation
- durable `human_escalation_notification` email jobs for immediate escalations
- `HumanHandoffNotificationService` with idempotent job creation per escalation
- internal/debug listing API
- acknowledge endpoint
- resolve endpoint
- escalation assignment workflow (assign, unassign, assignment list filters, priority-based due dates)
- chat integration for immediate escalation signals
- chat-triggered notification job enqueue and post-commit RabbitMQ dispatch wake-up

## When escalation is created

This implementation creates a HumanEscalation record for immediate escalation signals:

- explicit user request for a human
- medical emergency signal

Suggested escalation signals may be shown to the user but do not automatically create a record yet.

## Idempotency

Only one active escalation should exist per conversation.

If an active escalation already exists, repeated requests reuse the existing record.

The same idempotency applies to notification jobs: one durable `human_escalation_notification` email job is created per escalation, and repeated immediate signals reuse the existing job.

## Boundaries

Human escalation does not:

- create appointment holds
- create appointments
- send booking confirmation emails
- call an LLM
- connect the patient to a live staff member in real time
- provide a human chat dashboard or operator console

Assignment records operational ownership only. It does not connect the patient to a staff member in real time.

See also: [Escalation Assignment Workflow](./escalation-assignment-workflow.md)

## Operational states

Escalations may be:

- open
- acknowledged
- resolved
- cancelled

## Security boundary

For this portfolio demo, authentication and RBAC are outside demo scope so the project can remain focused on backend reliability, appointment workflows, structured observability, and operational handoff design.

In production, escalation management endpoints would require staff authentication and role-based authorization.

## Outside demo scope

- Live operator console or real-time handoff queue
- Voice provider call transfer
- Auth/RBAC on public routes
