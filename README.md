# AI Clinic Receptionist Platform

Production-style AI receptionist for clinics, supporting chat and Retell web voice calls, with appointment scheduling, rescheduling, cancellation, persistence, guardrails, observability, and background confirmation jobs.

## Project Positioning

This project is a portfolio-grade backend and applied AI systems project designed to demonstrate production-minded engineering for:

- AI Engineering
- Applied AI Engineering
- Voice AI Engineering
- Backend Engineering
- LLM Systems Engineering
- AI Agent Engineering

The system models a fictional US-based clinic receptionist that can help patients book, reschedule, cancel, and review appointments through chat and Retell-powered voice interactions.

## What This Project Is

This project focuses on:

- Voice AI architecture
- Retell web voice integration
- Retell voice call lifecycle persistence
- Retell voice tool-calling adapter (clinic context, availability with structured date expressions, hold, release, booking with explicit confirmation, cancellation with explicit confirmation, rescheduling with explicit confirmation)
- Clinic time context with backend date resolution, business hours enforcement, and `get_clinic_context` tool
- Voice conversation bridge linking Retell calls to shared `Conversation` state
- Chat receptionist flow
- Controlled natural-language response generation with deterministic default and optional LLM phrasing
- Tool calling
- Appointment scheduling
- Appointment rescheduling foundation (`AppointmentReschedulingService`)
- Appointment cancellation
- Patient lookup
- Temporary appointment slot holding
- Guardrails
- Persistence
- Auditability
- Observability
- Background jobs
- Reliability
- Professional Git history

## What This Project Is Not

This project intentionally does not implement:

- A full clinic CRM
- Billing
- Insurance workflows
- Medical diagnosis
- Electronic health records
- OAuth in the first version
- A custom voice WebSocket simulator
- A generic chatbot unrelated to clinic operations

## Core Architecture Principle

Chat UX and voice UX are not modeled the same way.

Chat conversations are backend-orchestrated.

Retell voice conversations are provider-orchestrated.

The backend exposes tools, validation, persistence, business rules, side effects, audit logs, and observability.

This separation is documented in:

- `docs/adr/001-chat-vs-voice-conversation-boundaries.md`

## Core Stack

Planned stack:

- Python
- FastAPI
- PostgreSQL
- SQLAlchemy 2.0
- Alembic
- Pydantic v2
- Redis
- RabbitMQ
- pytest
- Docker
- Docker Compose
- Retell Web Calls
- `fake` LLM provider for local development and CI
- `groq` LLM provider for real-provider demo validation
- `bedrock` LLM provider as an optional AWS enterprise-style adapter
- FakeEmailProvider by default
- Optional Resend email provider
- Prometheus
- Grafana
- OpenTelemetry

## Main Capabilities

The receptionist will support:

- New appointment booking (written chat and Retell voice)
- Existing patient lookup
- Lightweight patient registration
- Scheduled appointment lookup (written chat)
- Appointment rescheduling (written chat and Retell voice)
- Appointment cancellation (written chat and Retell voice)
- Doctor information
- Specialty information
- Availability lookup
- Temporary appointment slot holding
- Retell voice tools: `get_clinic_context`, availability check (structured `date_expression`), slot hold, hold release, booking with explicit confirmation, cancellation with explicit confirmation, and rescheduling with explicit confirmation (via verified `POST /api/v1/retell/tools`)
- Voice conversation bridge: link Retell calls to shared `Conversation` state with safe `voice_context`
- Human escalation case creation
- Confirmation email jobs

## Demo Clinic Scenario

The demo uses a fictional US clinic in the `America/New_York` timezone.

Business hours default to Monday–Friday, 09:00–17:00 clinic local time. The backend resolves relative dates and enforces business rules; voice agents should call `get_clinic_context` rather than inferring the current date.

The system avoids highly sensitive identifiers such as SSN.

Patient identity uses safer identifiers:

- Full name
- Date of birth
- Phone number
- Email

Seed data will include fictional doctors such as:

- Dr. Emily Carter — Dermatology
- Dr. Michael Reed — Cardiology
- Dr. Sarah Mitchell — Primary Care

## Development Methodology

This repository is built incrementally using the following process:

1. Project Context
2. System Design
3. Implementation
4. Testing
5. Engineering Review
6. Conceptual Engineering Review

The goal is to build a realistic engineering artifact, not a one-shot generated codebase.

## Current Status

Architecture and runtime implementation are in progress.

Implemented foundations include deterministic chat booking, **written-chat contextual appointment management** (booking, scheduled lookup, rescheduling, cancellation, patient identity reuse, and hold recovery), scheduling tools, clinic time configuration, Redis holds with channel-specific TTL, background email jobs, human escalation, LLM provider boundaries (fake default; optional Groq and Bedrock), LLM reliability orchestration, receptionist response generation, offline LLM evaluation, Redis-backed public demo guardrails, Retell webhook security and tool adapter, Retell web call service and public demo voice endpoint, production-oriented configuration validation, Docker service commands, a public demo deployment runbook, and a Next.js public web demo shell in `web/`.

Configuration and deployment:

- [`docs/configuration.md`](docs/configuration.md)
- [`.env.example`](.env.example) — local development
- [`.env.demo.example`](.env.demo.example) — hosted public demo template
- [`docs/operations/public-demo-deployment.md`](docs/operations/public-demo-deployment.md) — deploy runbook

Architecture docs:

- `docs/architecture/chat-turn-understanding.md` — CTU contract, appointment intake orchestration, and state-aware fallback
- `docs/architecture/chat-appointment-management.md` — written-chat booking, lookup, reschedule, cancel, identity, holds, and post-completion routing
- `docs/testing/chat-appointment-management.md` — end-to-end manual QA script for appointment management flows
- `docs/testing/chat-appointment-intake.md` — manual fake/Groq appointment intake checklist
- `docs/testing/chat-cancellation-flow.md` — manual cancellation flow checklist
- `docs/architecture/groq-llm-provider.md`
- `docs/architecture/real-llm-provider-adapter.md`
- `docs/architecture/llm-provider-foundation.md`
- `docs/architecture/llm-reliability-orchestration.md`
- `docs/architecture/chat-llm-reliability.md`
- `docs/architecture/receptionist-response-generator.md`
- `docs/architecture/llm-evaluation-dataset.md`
- `docs/architecture/chat-scheduling-evaluation-harness.md`
- `docs/architecture/provider-run-evaluation-mode.md`
- `docs/architecture/public-demo-guardrails.md`
- `docs/architecture/email-dispatch-reliability.md`
- `docs/architecture/email-job-worker.md`
- `docs/architecture/retell-webhook-security.md`
- `docs/architecture/retell-call-lifecycle.md`
- `docs/architecture/retell-tool-calling-adapter.md`
- `docs/architecture/clinic-time-context-and-tool-contracts.md`
- `docs/architecture/voice-conversation-bridge.md`
- `docs/architecture/retell-voice-booking-confirmation.md`
- `docs/architecture/retell-voice-cancellation.md`
- `docs/architecture/retell-voice-rescheduling.md`
- `docs/architecture/appointment-rescheduling-foundation.md`

## Public Demo Deployment

This repo supports two deployment profiles:

| | **Local mode** | **Public demo mode** |
|---|---|---|
| **Purpose** | Development and CI | Hosted unauthenticated portfolio demo |
| **Config template** | [`.env.example`](.env.example) | [`.env.demo.example`](.env.demo.example) |
| **Providers** | Fake LLM and fake email by default | Optional real Groq / Resend; Retell backend routes ready |
| **Guardrails** | Disabled | Redis-backed rate limits and quotas |
| **Patient data** | Fictional demo clinic only | Fictional demo clinic only — no real PHI |

**Local mode** needs no external API keys. Use Docker Compose for PostgreSQL, Redis, and RabbitMQ. After migrations and `python -m scripts.seed_demo_data`, refresh demo availability through the booking horizon with `python -m app.scripts.generate_demo_availability`. See [`docs/operations/local-development.md`](docs/operations/local-development.md) and [`docs/operations/demo-availability-generation.md`](docs/operations/demo-availability-generation.md).

**Public demo mode** enables bounded internet-facing access with `PUBLIC_DEMO_MODE=true` and guardrails enabled. Startup validation rejects unsafe production configuration (for example missing managed Postgres/Redis URLs, disabled guardrails, or insecure Retell webhooks).

### API and worker

Deploy as **two processes** from the same Docker image:

- **API:** `python -m uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}`
- **Email worker:** `python -m scripts.run_email_worker`

The API handles chat, scheduling, and Retell routes. The worker consumes RabbitMQ wake-up messages and processes durable `EmailJob` records from PostgreSQL.

### Optional providers

- **fake** — default LLM for local development and CI (no API key)
- **groq** — optional real LLM for hosted demo analysis and phrasing
- **bedrock** — optional AWS enterprise-style LLM adapter (`BEDROCK_MODEL_ID`, `AWS_REGION`, standard AWS credentials)
- **Resend** — optional real confirmation email delivery; fake provider records jobs in memory
- **Retell** — voice tool routes, webhooks, server-side web calls, and call lifecycle; agent and webhook setup: [`docs/operations/retell-dashboard-setup.md`](docs/operations/retell-dashboard-setup.md)

### Demo safety

- Redis-backed **public demo guardrails** on chat, Retell tools, appointments, and confirmation emails
- **Fake/local providers** for development without real keys
- **No real patient data** — fictional US clinic scenario only
- No auth/RBAC for public routes in the current demo scope

Full deploy steps, health checks, smoke tests, rollback, and troubleshooting: [`docs/operations/public-demo-deployment.md`](docs/operations/public-demo-deployment.md).

## Public Web Demo

The `web/` app is a minimal Next.js frontend for the portfolio public demo. It does not implement clinic business logic; chat and scheduling rules stay on the FastAPI backend.

**Safety:** The demo models a **fictional US clinic**. Do not enter real patient names, contact details, or medical information.

**What it includes:**

- Landing page with demo disclaimer
- Backend-powered chat panel (`POST /api/v1/chat/messages` via same-origin proxy)
- Feature-flagged voice demo (`NEXT_PUBLIC_VOICE_DEMO_ENABLED`)
- Public env vars only — no Retell or provider keys in the browser

**Voice demo:** When `NEXT_PUBLIC_VOICE_DEMO_ENABLED=true` and the API has `RETELL_WEB_CALL_ENABLED=true`, **Call the clinic** requests a short-lived token from `POST /api/v1/demo/voice/retell-web-call`, then connects via the Retell Web SDK after the user clicks **Start call** (microphone permission at that point only). With the flag off, the panel shows a configuration preview and never requests the microphone. Retell agent setup: [`docs/operations/retell-dashboard-setup.md`](docs/operations/retell-dashboard-setup.md).

**Quick start** (backend must be running on port 8000):

```bash
cd web
cp .env.example .env.local
npm install
npm run dev
```

Open [http://localhost:3000](http://localhost:3000). Full setup, env reference, build, and deployment notes: [`web/README.md`](web/README.md).