# Roadmap

This roadmap documents planned work honestly. Items are grouped by implementation stage.

## Stage 1 — Architecture Foundation

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

## Stage 2 — Project Scaffold

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

## Stage 3 — Domain Model

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

## Stage 4 — Scheduling Tools

Status: Planned

Goals:

- Implement patient lookup
- Implement doctor and specialty listing
- Implement availability lookup
- Implement appointment booking
- Implement rescheduling
- Implement cancellation

## Stage 5 — Redis Appointment Holds

Status: Planned

Goals:

- Use Redis for temporary slot holds
- Prevent double booking
- Enforce hold ownership
- Enforce hold expiration

## Stage 6 — Chat Channel

Status: In progress

Goals:

- Implement backend-orchestrated chat conversation
- Add FakeLLMProvider
- Persist conversations and messages
- Add guardrail-aware tool decisions

Implemented:

- Chat API foundation (`POST /api/v1/chat/messages`) with deterministic receptionist responses
- Conversation and message persistence via `Conversation` and `ConversationMessage`
- Scheduling-aware chat flow with read-only specialty and doctor responses
- Chat availability guidance
- Chat appointment hold flow
- Chat booking confirmation flow with patient identity parsing and confirmation email enqueue
- Fake LLM provider foundation with structured output parsing and shadow analysis metadata
- Structured-output-assisted slot filling with validated merge into `chat_context`
- Conversation health and escalation signals with deterministic health metadata and soft handoff behavior

Upcoming:

- Human escalation foundation
- Escalation listing API
- Human handoff notification job
- Natural-language date parsing
- Real provider adapter
- LLM reliability/fallbacks
- Cost tracking aggregation
- Hold expiration handling in chat

## Stage 7 — Retell Tool Integration

Status: Planned

Goals:

- Expose Retell-compatible tool endpoints
- Validate Retell tool payloads
- Record tool calls
- Add Retell integration documentation

## Stage 8 — Background Email Jobs

Status: In progress

Goals:

- Add RabbitMQ-based email job queue
- Add FakeEmailProvider
- Persist job execution records
- Add idempotency and retry readiness

Implemented:

- Email job debug API with cursor pagination
- Manual retry/replay endpoint
- Email job operational metrics

Future work:

- Admin auth/RBAC as future production hardening, outside current demo scope
- Provider-level idempotency keys
- Prometheus/Grafana integration (optional)

## Stage 9 — Observability

Status: Planned

Goals:

- Add structured logs
- Add request and correlation IDs
- Add audit logs
- Add Prometheus metrics
- Add OpenTelemetry traces
- Add dependency health checks

Implemented:

- API error response standardization

## Stage 10 — Production Hardening

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
