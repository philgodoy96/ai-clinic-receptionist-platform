# Engineering Scope Ledger

This document records what the repository implements for the portfolio demo and what sits outside demo scope or in production-hardening territory. It is not a promise of future work.

## Demo scope (implemented and demo-ready)

The default local/portfolio demo includes:

- FastAPI backend with PostgreSQL persistence
- Redis appointment holds and public demo guardrails (when enabled)
- RabbitMQ email dispatch and email worker / polling worker path
- Fake email provider by default; Resend optional and implemented
- Written-chat appointment flows: booking, lookup, reschedule, cancel
- Deterministic backend orchestration with optional LLM layers (fake provider by default)
- Human escalation record and durable staff notification email job path
- Frontend chat demo (`web/`)
- Seed demo data (patients **John Miller**, 1985-04-12; **Ava Thompson**, 1992-09-03)
- Health endpoints at `/health` and `/health/dependencies`
- Structured JSON logging and request/correlation IDs
- Docker / local development setup
- Public demo deployment runbook and configuration validation

## Optional / prepared integrations

These boundaries exist in code but are not active in the default local demo:

| Integration | Notes |
|---|---|
| Groq LLM provider | Optional analysis and phrasing for hosted demo |
| Bedrock LLM provider | Optional AWS enterprise-style adapter |
| Fake LLM provider | Default local/CI behavior |
| Chat turn understanding modes | Fake interpreter default; Groq optional |
| Receptionist response generation | Deterministic default; optional LLM phrasing |
| Retell tools / webhooks / web calls | When `RETELL_ENABLED=true` and configured |
| Resend outbound mail | When `EMAIL_PROVIDER=resend` and configured |
| Observability export | Prometheus/OpenTelemetry as extension points—not runtime-wired |

## Completed engineering areas

### Architecture and scaffold

- Project scope, ADRs, and engineering methodology documentation
- FastAPI application structure, Pydantic settings, pytest suite
- Docker Compose (PostgreSQL, Redis, RabbitMQ)
- Production Docker image with separate API and email worker commands
- Health endpoints and configuration templates (`.env.example`, `.env.demo.example`)

### Domain and scheduling

- Clinic scheduling domain entities and persistence models
- Patient lookup, doctor/specialty listing, availability lookup
- Appointment booking, rescheduling foundation, cancellation
- Redis appointment holds with channel-specific TTL and degradation policy
- Clinic time configuration, business hours, natural-language date parsing
- Rolling demo availability generation CLI

### Written chat

- Backend-orchestrated chat API (`POST /api/v1/chat/messages`)
- Deterministic routing with optional LLM-assisted slot filling and phrasing
- Written-chat appointment management: booking, lookup, reschedule, cancel
- Conversation health signals and human escalation integration
- LLM provider boundaries (fake default; Groq and Bedrock optional)
- LLM reliability orchestration, evaluation dataset, and scheduling evaluation harness
- Public demo guardrails (Redis-backed quotas)

### Email and background jobs

- Durable `EmailJob` records with retry policy and idempotency
- RabbitMQ wake-up dispatch and polling fallback worker
- Fake email provider (default); Resend adapter (optional)
- Booking and reschedule confirmation emails; human escalation notification jobs

### Human escalation

- `HumanEscalation` persistence and service layer
- Idempotent escalation creation and staff notification email jobs
- Internal listing, acknowledge, resolve, and assignment workflow APIs
- No live operator console or real-time handoff queue

### Retell voice (optional integration boundary)

- Retell webhook security and tool-calling adapter
- Call lifecycle persistence and voice conversation bridge
- Voice booking, cancellation, and rescheduling with explicit confirmation
- Retell web call service and public demo voice session API
- Retell Web SDK integration in `web/` behind feature flag
- Retell dashboard setup and conversation UX documentation

### Observability baseline

- Structured JSON logs with request and correlation IDs
- Audit log correlation
- API error response standardization
- Dependency health endpoint

### Public demo and frontend

- Production configuration validation for hosted demo
- Public demo deployment runbook
- Next.js public web demo shell (chat panel, optional voice panel)
- Deployment configuration safety tests

## Outside demo scope / production hardening

These items are intentional boundaries or concerns for a production deployment—not missing demo requirements:

| Area | Notes |
|---|---|
| Auth / RBAC | Public demo routes are unauthenticated by design |
| Live human operator console | Escalation is record + notification job only |
| Patient profile updates | Not implemented |
| Clinical advice / triage / diagnosis | Explicitly out of scope |
| Advanced unsafe/abusive moderation | Outside demo scope |
| Cancellation confirmation emails | Not implemented |
| OpenAI / Vapi / Twilio | Not implemented |
| Prometheus / OpenTelemetry runtime wiring | Extension points only; not active in app |
| Grafana dashboards | Not implemented |
| Provider webhook bounce handling | Production email hardening |
| RabbitMQ DLQ for malformed messages | Production messaging hardening |
| Demo reset / cleanup automation | Operational convenience, not demo blocker |
| Dynamic doctor schedule engine | Production scheduling complexity |
| Admin schedule management UI | Production operations |
| Dedicated LLM run persistence table | Operational analytics |
| Chat message/client idempotency | Production reliability enhancement |
| Voice provider call transfer | Production voice operations |
| CAPTCHA / edge protection | Hosted abuse hardening |

## Reference documentation

- [Architecture Overview](architecture/overview.md) — canonical architecture truth and integration matrix
- [Chat Appointment Management](architecture/chat-appointment-management.md)
- [Public Demo Guardrails](architecture/public-demo-guardrails.md)
- [Local Development](operations/local-development.md)
- [Public Demo Deployment](operations/public-demo-deployment.md)
