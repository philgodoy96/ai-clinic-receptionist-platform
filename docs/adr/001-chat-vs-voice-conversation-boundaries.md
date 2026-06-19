# ADR-001: Voice UX must not be modeled as chat UX

## Status

Accepted

## Context

AI Clinic Receptionist Platform supports two interaction channels:

1. Chat
2. Retell web voice calls

A tempting but incorrect design would be to model both channels as the same backend-driven conversation loop.

That design would treat voice as if it were only chat with audio attached.

This is not how production voice AI systems should usually be modeled.

Voice interactions have different constraints:

- Lower latency requirements
- Turn-taking behavior
- Interruptions
- Speech recognition uncertainty
- Text-to-speech timing
- Provider-level runtime behavior
- Natural spoken conversation flow

Retell is designed to orchestrate real-time voice UX.

The backend is better suited for durable state, business rules, validation, side effects, auditability, and observability.

## Decision

Chat conversations are backend-orchestrated.

Retell voice conversations are provider-orchestrated.

The backend exposes tools, validation, persistence, business rules, side effects, audit logs, and observability.

The backend must not persist or query every voice turn just to decide the next conversational step.

Retell should call backend tools only when real data, validation, or side effects are required.

Examples of valid backend tool calls:

- lookup_patient
- create_patient
- list_specialties
- list_doctors
- check_availability
- hold_appointment_slot
- book_appointment
- list_upcoming_appointments
- reschedule_appointment
- cancel_appointment
- create_escalation_case

Examples of behavior that should stay in the voice runtime:

- Conversational turn-taking
- Natural acknowledgements
- Asking short follow-up questions
- Handling normal speech flow
- Managing real-time voice experience

## Consequences

### Positive Consequences

This design creates a clean separation of responsibilities.

Retell handles what it is optimized for:

- Voice UX
- Speech timing
- Conversation flow
- Real-time interaction

The backend handles what it is optimized for:

- Business rules
- Scheduling consistency
- Patient data protection
- Tool validation
- Persistence
- Audit logs
- Metrics
- Traces
- Background jobs

The system avoids unnecessary backend round trips for every spoken turn.

The backend remains easier to test because business tools can be tested independently from the voice provider.

The project demonstrates realistic Voice AI architecture rather than treating voice as a thin wrapper around chat.

### Negative Consequences

The system now has two different orchestration models:

- Backend orchestration for chat
- Provider orchestration for voice

This increases documentation needs.

Some behavior may need to be represented twice:

- Chat flow logic in backend services
- Voice guidance in Retell prompt/runtime configuration

Retell-specific integration tests require mocked provider requests.

## Alternatives Considered

### Alternative 1: Backend controls every voice turn

In this model, Retell would only stream audio or transcripts, and the backend would decide every conversational step.

This was rejected because it increases latency, complexity, and provider coupling.

It also ignores the strengths of Retell as a real-time voice orchestration platform.

### Alternative 2: Retell owns all business logic

In this model, the backend would only store final results, while Retell would decide scheduling behavior directly.

This was rejected because provider prompts are not a reliable enforcement layer.

The backend must enforce patient data protection, appointment validity, hold ownership, idempotency, and auditability.

### Alternative 3: Single generic conversation engine for chat and voice

In this model, both chat and voice would use the same backend conversation state machine.

This was rejected because chat and voice have different UX constraints and orchestration needs.

Shared business tools are useful.

Shared conversation orchestration is not the right abstraction.

## Implementation Guidance

The backend should expose Retell-compatible tool endpoints.

The backend should validate every tool payload.

The backend should persist tool calls and important side effects.

The backend should not require a persisted voice transcript to decide every next voice step.

Chat-specific conversation logic should live in the chat application layer.

Voice-specific runtime guidance should live in Retell configuration and documentation.

Shared business capabilities should live in application services behind stable interfaces.

## Related Future ADRs

- ADR-002: Retell is an adapter, not the business logic core
- ADR-003: Redis is used for appointment holds, not durable conversation memory
- ADR-004: Background email confirmation uses RabbitMQ
- ADR-005: Backend enforces guardrails, provider prompt only guides behavior