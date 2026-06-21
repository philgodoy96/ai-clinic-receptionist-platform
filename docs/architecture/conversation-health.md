# Conversation Health and Escalation Signals

## Context

The AI Clinic Receptionist Platform uses deterministic business services with optional LLM-assisted slot filling.

Once an AI assistant can participate in a multi-turn conversation, the system needs to detect when the conversation is no longer progressing safely or efficiently.

## Design Principle

Escalation is an operational handoff signal, not another LLM pretending to be human.

## Current Implementation

The current implementation computes deterministic conversation health signals and records them in assistant message metadata.

Immediate health signals now create durable `HumanEscalation` records through `HumanEscalationService`. The chat layer creates or reuses one active escalation per conversation when an immediate signal is detected.

Immediate escalation applies when:

- the user explicitly requests a human, or
- a medical emergency signal is detected

Suggested escalation alone does not create a `HumanEscalation` record.

The system does not notify a real staff member yet.

The system does not add a human-agent dashboard yet.

## HumanEscalation Handoff Context

When chat creates an immediate escalation, it builds `handoff_context` from conversational `chat_context`.

Safe operational fields may include:

- `active_hold_present`
- `hold_id`
- `hold_expires_at`
- `selected_doctor_name`
- `requested_date`
- `selected_start_time`

This is operational memory for staff handoff. It is not patient identity storage.

Escalation does not release Redis holds automatically. If a hold is active, the record only notes that fact so a human can decide next steps.

## Escalation Levels

### User-requested escalation

If the user explicitly asks for a human, receptionist, representative, or real person, the system should respect that request.

The chat assistant returns a handoff-style reply and marks the conversation as escalated when appropriate.

### Safety-required escalation signal

Emergency and safety signals are recorded immediately.

Emergency response remains deterministic and highest priority.

An urgent `HumanEscalation` record may also be created, but the emergency reply still wins over scheduling or booking behavior.

### Suggested escalation

The system may suggest human handoff when there are strong stuck-conversation signals, such as repeated fallback, repeated low confidence, repeated slot filling rejection, or repeated booking conflicts.

Message count alone is not enough to trigger escalation.

Suggested escalation does not create a `HumanEscalation` record in the current phase.

## Signals

Conversation health may consider:

- message count
- user message count
- assistant message count
- fallback count
- slot filling rejection count
- low confidence count
- provider fallback count
- emergency signal count
- repeated booking conflict count
- active hold presence
- booking confirmation state
- explicit human request

When an immediate signal is present, health evaluation triggers `HumanEscalationService.create_or_get_active_escalation`. Assistant message metadata includes a `human_escalation` summary when a record is created or reused.

## Boundaries

Conversation health does not:

- create appointment holds
- create appointments
- send emails
- call an LLM
- pretend to be a human
- release Redis holds automatically during escalation

## Future Work

Future implementation phases may add:

- staff notification job
- call transfer integration for voice providers
- admin dashboard
- escalation resolution workflow beyond the current internal debug API
