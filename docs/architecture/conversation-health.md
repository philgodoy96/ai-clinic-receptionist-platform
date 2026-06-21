# Conversation Health and Escalation Signals

## Context

The AI Clinic Receptionist Platform uses deterministic business services with optional LLM-assisted slot filling.

Once an AI assistant can participate in a multi-turn conversation, the system needs to detect when the conversation is no longer progressing safely or efficiently.

## Design Principle

Escalation is an operational handoff signal, not another LLM pretending to be human.

## Current Implementation

The current implementation computes deterministic conversation health signals and records them in assistant message metadata.

It does not create a human escalation record yet.

It does not notify a real staff member yet.

It does not add a human-agent dashboard yet.

## Escalation Levels

### User-requested escalation

If the user explicitly asks for a human, receptionist, representative, or real person, the system should respect that request.

### Safety-required escalation signal

Emergency and safety signals are recorded immediately.

Emergency response remains deterministic and highest priority.

### Suggested escalation

The system may suggest human handoff when there are strong stuck-conversation signals, such as repeated fallback, repeated low confidence, repeated slot filling rejection, or repeated booking conflicts.

Message count alone is not enough to trigger escalation.

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

## Boundaries

Conversation health does not:

- create appointment holds
- create appointments
- send emails
- call an LLM
- pretend to be a human
- create a human escalation record in this phase

## Future Work

Future implementation phases may add:

- HumanEscalation table
- internal escalation listing API
- staff notification job
- call transfer integration for voice providers
- admin dashboard
- escalation resolution workflow