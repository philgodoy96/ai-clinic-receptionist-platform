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

Status: In progress

Goals:

- Add FastAPI application structure
- Add Docker Compose foundation
- Add configuration management
- Add health endpoint
- Add basic test setup

Implemented:

- FastAPI app with `/health` and `/health/dependencies`
- Pydantic settings with local and public demo env templates
- pytest suite
- Docker Compose for PostgreSQL, Redis, and RabbitMQ
- Production Docker image with separate API and email worker commands

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
- Implement appointment rescheduling foundation
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
- Receptionist response generator with `ResponsePlan`, deterministic templates, optional LLM phrasing, output validation, deterministic fallback, chat integration, and optional voice `suggested_response_text` (see `docs/architecture/receptionist-response-generator.md`)
- Public demo guardrails foundation with Redis-backed per-IP and global quotas, protected chat and Retell tool routes, and standardized `429`/`503` responses
- Retell webhook security with configuration validation, centralized signature verification, protected callback/tool routes, and standardized rejection for missing or invalid signatures
- Retell call lifecycle with durable `VoiceCall` and `VoiceCallEvent` records, idempotent verified webhook ingestion, conservative status transitions, safe event metadata storage, and internal/debug inspection APIs
- Retell tool-calling adapter with explicit supported-tool allowlist, scheduling and hold service delegation, side-effect idempotency, and provider-safe responses for `check_availability`, `hold_appointment_slot`, `release_appointment_hold`, `book_appointment`, `cancel_appointment`, and `reschedule_appointment`
- Voice conversation bridge with durable `VoiceCall` to `Conversation` linkage, safe `voice_context` storage, Retell tool context integration, and internal/debug conversation-context API
- Retell voice booking confirmation with active hold validation, patient identity validation, explicit caller confirmation, idempotent provider callback handling, and delegation to `AppointmentBookingService` (see `docs/architecture/retell-voice-booking-confirmation.md`)
- Retell voice appointment cancellation with explicit cancellation confirmation, cancelable status validation, idempotent provider callback handling, and delegation to `AppointmentCancellationService` (see `docs/architecture/retell-voice-cancellation.md`)
- Retell voice appointment rescheduling with explicit reschedule confirmation, original appointment and hold/slot validation, idempotent provider callback handling, and delegation to `AppointmentReschedulingService` (see `docs/architecture/retell-voice-rescheduling.md`)
- Appointment rescheduling foundation with shared `AppointmentReschedulingService`, original appointment validation, target slot/hold validation, explicit confirmation, idempotency, safe audit logging, and safe conversation metadata updates (see `docs/architecture/appointment-rescheduling-foundation.md`)
- Clinic time configuration with validated `CLINIC_*` settings, `ClinicTimeService`, structured date/time expressions, business-day and business-hours enforcement on scheduling tools, and `get_clinic_context` Retell tool (see `docs/architecture/clinic-time-context-and-tool-contracts.md`)
- Resend email dispatch foundation with durable EmailJob retry policy, appointment confirmation idempotency, RabbitMQ wake-up messages, and fake provider default
- Public demo deployment runbook, `.env.demo.example`, production configuration validation, and deployment safety tests
- Public web demo shell in `web/` (Next.js landing, backend-powered chat panel, Retell Web SDK voice demo behind feature flag, safe API error handling)
- Retell web call creation service and public demo voice session API (`POST /api/v1/demo/voice/retell-web-call`)

Upcoming:

- richer response-quality evaluation dataset for receptionist phrasing
- Prompt regression reports
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
- Retell tool-calling adapter with explicit allowlist, scheduling/hold delegation, clinic time enforcement, idempotency, and provider-safe responses (see `docs/architecture/retell-tool-calling-adapter.md`)
- Clinic time context and tool contracts with `get_clinic_context`, structured `date_expression`, and backend scheduling time enforcement (see `docs/architecture/clinic-time-context-and-tool-contracts.md`)
- Voice conversation bridge with `VoiceCall` to `Conversation` linkage, safe voice context, Retell tool integration, and internal/debug context endpoint (see `docs/architecture/voice-conversation-bridge.md`)
- Retell voice booking confirmation with hold, identity, and explicit confirmation validation (see `docs/architecture/retell-voice-booking-confirmation.md`)
- Retell voice appointment cancellation with explicit confirmation, cancelable status validation, and shared `AppointmentCancellationService` delegation (see `docs/architecture/retell-voice-cancellation.md`)
- Retell voice appointment rescheduling with explicit confirmation, original appointment and hold/slot validation, and shared `AppointmentReschedulingService` delegation (see `docs/architecture/retell-voice-rescheduling.md`)
- Appointment rescheduling foundation with shared `AppointmentReschedulingService` and attempt/audit support (see `docs/architecture/appointment-rescheduling-foundation.md`)
- Retell dashboard setup runbook with UX rules, patient intake, `end_call`, and ngrok guidance (`docs/operations/retell-dashboard-setup.md`)
- Retell conversation UX pack: master prompt v2, tool descriptions, voice smoke scenarios, conversation playbook (`docs/operations/retell-master-prompt-v2.md`, `retell-tool-descriptions.md`, `retell-voice-smoke-scenarios.md`, `retell-conversation-ux-playbook.md`)
- Voice patient intake mode (`VOICE_PATIENT_INTAKE_MODE`: `lookup_only` / `demo_auto_create`) for voice booking identity
- Softened deterministic `suggested_response_text` templates for Retell voice tool results (see `docs/architecture/receptionist-response-generator.md`)
- Retell Web SDK integration in `web/` with server-issued access tokens (see `web/README.md`)
- Retell web call activation safety tests and public voice demo documentation

Upcoming:

- written chat reschedule flow
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
- Structured JSON logs with request and correlation IDs
- Dependency health endpoint for PostgreSQL readiness

## Stage 10 — Production Hardening

Status: In progress

Implemented:

- Production-like settings validation for public demo deployments
- `.env.demo.example` deployment template
- [`docs/operations/public-demo-deployment.md`](operations/public-demo-deployment.md) runbook
- Production Docker commands for API and email worker
- Deployment configuration safety tests
- Public web demo shell (`web/`): Next.js landing, chat demo panel, Retell Web SDK voice demo, env safety check
- [`docs/operations/retell-dashboard-setup.md`](operations/retell-dashboard-setup.md) Retell dashboard runbook (UX rules, patient intake, Payload: args only OFF, ngrok)
- Retell conversation UX documentation (master prompt v2, tool descriptions, smoke scenarios, playbook)
- Retell web call activation safety tests and public voice demo deployment documentation

Potential work:

- Admin authentication
- X-Admin-Token for demo admin endpoints
- Provider budget limits
- DLQ support
- OpenAI provider adapter
- Grafana dashboard
- Demo reset / cleanup automation
