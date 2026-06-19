# Roadmap

This roadmap documents planned work honestly. Items are grouped by implementation stage.

## Stage 1 â€” Architecture Foundation

Status: In progress

Goals:

- Define project scope
- Define architecture boundaries
- Document chat vs voice UX differences
- Create initial ADRs
- Define engineering methodology

Planned artifacts:

- README.md
- ENGINEERING_GUIDE.md
- docs/architecture/overview.md
- docs/adr/001-chat-vs-voice-conversation-boundaries.md

## Stage 2 â€” Project Scaffold

Status: Planned

Goals:

- Add FastAPI application structure
- Add Docker Compose foundation
- Add configuration management
- Add health endpoint
- Add basic test setup

Planned components:

- FastAPI app
- Pydantic settings
- pytest
- Docker Compose
- PostgreSQL service
- Redis service
- RabbitMQ service

## Stage 3 â€” Domain Model

Status: Planned

Goals:

- Define clinic scheduling domain entities
- Separate domain entities from persistence models
- Document invariants

Planned entities:

- Patient
- Doctor
- Specialty
- AvailabilitySlot
- Appointment
- AppointmentHold
- Conversation
- Message
- ToolCall
- AuditLog
- EscalationCase
- JobExecution

## Stage 4 â€” Scheduling Tools

Status: Planned

Goals:

- Implement patient lookup
- Implement doctor and specialty listing
- Implement availability lookup
- Implement appointment booking
- Implement rescheduling
- Implement cancellation

## Stage 5 â€” Redis Appointment Holds

Status: Planned

Goals:

- Use Redis for temporary slot holds
- Prevent double booking
- Enforce hold ownership
- Enforce hold expiration

## Stage 6 â€” Chat Channel

Status: Planned

Goals:

- Implement backend-orchestrated chat conversation
- Add FakeLLMProvider
- Persist conversations and messages
- Add guardrail-aware tool decisions

## Stage 7 â€” Retell Tool Integration

Status: Planned

Goals:

- Expose Retell-compatible tool endpoints
- Validate Retell tool payloads
- Record tool calls
- Add Retell integration documentation

## Stage 8 â€” Background Email Jobs

Status: Planned

Goals:

- Add RabbitMQ-based email job queue
- Add FakeEmailProvider
- Persist job execution records
- Add idempotency and retry readiness

## Stage 9 â€” Observability

Status: Planned

Goals:

- Add structured logs
- Add request and correlation IDs
- Add audit logs
- Add Prometheus metrics
- Add OpenTelemetry traces
- Add dependency health checks

## Stage 10 â€” Production Hardening

Status: Future hardening

Potential work:

- Admin authentication
- X-Admin-Token for demo admin endpoints
- Retell webhook signature validation
- Provider budget limits
- DLQ support
- ResendProvider
- GroqProvider or OpenAIProvider
- Grafana dashboard
- Deployment documentation
