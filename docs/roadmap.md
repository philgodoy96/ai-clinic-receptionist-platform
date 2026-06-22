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
- Real LLM provider adapter with configurable `FakeLLMProvider` default, optional Groq adapter for hosted public demo, and optional Bedrock adapter
- Structured-output-assisted slot filling with validated merge into `chat_context`
- Conversation health and escalation signals with deterministic health metadata and soft handoff behavior
- Human escalation foundation with durable `HumanEscalation` records, handoff context, and internal listing/acknowledge/resolve API
- Human handoff notification job with durable `human_escalation_notification` email jobs, idempotent enqueue, chat integration, fake worker delivery, and post-commit RabbitMQ dispatch wake-up
- Escalation assignment workflow with assign/unassign service methods, priority-based due dates, assignment list filters, and internal/debug API endpoints
- Natural-language date parsing with deterministic `NaturalLanguageDateParser` for chat availability guidance and LLM slot filling
- Time-of-day preference parsing with deterministic `TimePreferenceParser` for chat availability filtering and LLM slot filling
- LLM evaluation dataset with synthetic JSONL fixtures, recorded-output comparison, offline evaluation runner, metrics grouped by prompt version, and optional provider-run evaluation mode
- Prompt versioning with receptionist prompt registry, runtime `prompt_version` metadata, and evaluation traceability
- LLM reliability orchestration with explicit failure taxonomy, local repair, bounded primary retries, optional fallback provider, deterministic fallback, and rich reliability metadata
- Public demo guardrails foundation with Redis-backed per-IP and global quotas, protected chat and Retell tool routes, and standardized `429`/`503` responses
- Retell webhook security with configuration validation, centralized signature verification, protected callback/tool routes, and standardized rejection for missing or invalid signatures
- Retell call lifecycle with durable `VoiceCall` and `VoiceCallEvent` records, idempotent verified webhook ingestion, conservative status transitions, safe event metadata storage, and internal/debug inspection APIs
- Retell tool-calling adapter with explicit supported-tool allowlist, scheduling and hold service delegation, side-effect idempotency, and provider-safe responses for `check_availability`, `hold_appointment_slot`, and `release_appointment_hold`
- Resend email dispatch foundation with durable EmailJob retry policy, appointment confirmation idempotency, RabbitMQ wake-up messages, and fake provider default

Upcoming:

- Prompt regression reports
- Clinic timezone settings
- Voice provider transfer integration
- StaffUser/RBAC
- Assignment notification job
- Staff notification provider adapter
- Cost tracking aggregation
- Hold expiration handling in chat
- Demo reset strategy

## Stage 7 — Retell Tool Integration

Status: In progress

Goals:

- Expose Retell-compatible tool endpoints
- Validate Retell tool payloads
- Record tool calls
- Add Retell integration documentation

Implemented:

- Retell call lifecycle persistence and verified webhook ingestion (see `docs/architecture/retell-call-lifecycle.md`)
- Retell tool-calling adapter with explicit allowlist, scheduling/hold delegation, idempotency, and provider-safe responses (see `docs/architecture/retell-tool-calling-adapter.md`)

Upcoming:

- Tool call recording

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
- `human_escalation_notification` worker rendering and fake delivery
- Resend email provider adapter with fake provider default
- Durable retry policy with exponential backoff, max attempts, and `next_attempt_at`
- Appointment confirmation idempotency key
- RabbitMQ wake-up messages containing only `email_job_id`
- Worker claim/lock behavior, split claim/finalize transactions, and polling fallback worker
- RabbitMQ ack after durable finalize commit; Resend idempotency keys

Future work:

- provider webhook/bounce handling
- RabbitMQ DLQ for malformed broker messages
- delivery metrics dashboard and production alerting for terminal `failed` jobs
- Admin auth/RBAC as future production hardening, outside current demo scope
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
- Provider budget limits
- DLQ support
- OpenAI provider adapter
- Grafana dashboard
- Deployment documentation
