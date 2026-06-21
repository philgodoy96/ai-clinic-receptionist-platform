# Human Escalation Foundation

## Context

The AI Clinic Receptionist Platform supports deterministic chat booking, LLM-assisted slot filling, and conversation health signals.

When the conversation should not continue as automation, the system creates an operational handoff record.

## Design Principle

Human escalation is operational handoff, not AI roleplay.

The system must not use another LLM pretending to be a human receptionist.

## Current Implementation

The current implementation includes:

- `HumanEscalation` persistence model
- `HumanEscalationService`
- idempotent active escalation creation
- internal/debug listing API
- acknowledge endpoint
- resolve endpoint
- chat integration for immediate escalation signals

## When Escalation Is Created

This implementation creates a HumanEscalation record for immediate escalation signals:

- explicit user request for a human
- medical emergency signal

Suggested escalation signals may be shown to the user but do not automatically create a record yet.

## Idempotency

Only one active escalation should exist per conversation.

If an active escalation already exists, repeated requests reuse the existing record.

This protects against duplicate user messages, retries, and future duplicate webhook delivery.

## Boundaries

Human escalation does not:

- create appointment holds
- create appointments
- send confirmation emails
- call an LLM
- notify real staff in this phase
- implement a human chat dashboard

## Operational States

Escalations may be:

- open
- acknowledged
- resolved
- cancelled

## Security Boundary

For this portfolio demo, authentication and RBAC are intentionally out of scope so the project can remain focused on backend reliability, AI receptionist workflows, observability, and operational handoff design.

In production, these endpoints must require staff authentication and role-based authorization.

## Future Work

Future implementation phases may add:

- staff notification job
- escalation listing dashboard
- escalation assignment
- voice provider call transfer
- audit trail expansion
- staff user identity integration