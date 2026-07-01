# AI Clinic Receptionist Platform

Portfolio demo of a production-minded clinic receptionist backend: written chat appointment workflows, optional Retell voice integration, persistence, guardrails, structured observability, and durable background email jobs.

**Repository:** [github.com/philgodoy96/ai-clinic-receptionist-platform](https://github.com/philgodoy96/ai-clinic-receptionist-platform)

## Watch the demo

**Watch the demo:** [https://youtu.be/v2MyZqSqsJ8](https://youtu.be/v2MyZqSqsJ8)

The recording walks through the fictional clinic receptionist end to end:

- voice appointment booking through Retell
- written-chat booking
- rescheduling in the same conversation
- appointment cancellation
- backend state handling, holds, confirmation boundaries, and provider integration boundaries

The video is the canonical public demo. Run the stack locally (see below) to explore the same flows interactively.

## Demo scope

This project models a fictional clinic for portfolio demonstration. It is not for clinical advice, diagnosis, emergency triage, or medical decision support. Do not enter real patient data. The demo focuses on appointment workflow reliability, state handling, confirmation boundaries, provider boundaries, and backend execution safety.

## Project positioning

This project is a portfolio-grade backend and applied AI systems demo designed to show production-minded engineering for:

- AI Engineering
- Applied AI Engineering
- Voice AI Engineering
- Backend Engineering
- LLM Systems Engineering
- AI Agent Engineering

The system models a fictional US-based clinic receptionist. Patients can book, reschedule, cancel, and review appointments through **written chat** (default local demo) and, when explicitly configured, **Retell-powered voice**.

## What this project is

- **Written chat** — backend-orchestrated deterministic appointment workflow simulator with optional LLM-assisted analysis and phrasing
- **Voice (optional)** — Retell integration boundary for web calls, tool routes, and call lifecycle when enabled; chat-first by default locally
- Appointment scheduling, rescheduling, cancellation, and lookup
- Temporary appointment slot holding (Redis)
- Simulated human escalation as a durable internal record plus staff notification email job path (no live operator console)
- Durable email jobs with fake provider by default; Resend optional
- Structured JSON logging, request/correlation IDs, audit logs, and health endpoints
- Public demo guardrails for hosted deployments
- Professional Git history and incremental engineering methodology

## What this project is not

This project intentionally does not implement:

- Clinical advice, diagnosis, emergency triage, or medical decision support
- A full clinic CRM, billing, insurance workflows, or electronic health records
- Patient profile updates
- Advanced unsafe/abusive message moderation policy
- Auth/RBAC on public demo routes
- A live human operator console or real-time handoff queue
- OpenAI, Vapi, or Twilio integrations
- Prometheus/OpenTelemetry runtime export or Grafana dashboards (extension points only)
- Cancellation confirmation emails

## Core architecture principle

Chat UX and voice UX are not modeled the same way.

**Chat** conversations are backend-orchestrated. The backend manages state, routing, validation, and domain execution. Optional LLM layers assist turn understanding and response phrasing; default local/demo behavior uses fake/disabled providers.

**Voice** conversations are Retell-orchestrated when enabled. The backend exposes tools, validation, persistence, business rules, side effects, and audit logs.

See [`docs/adr/001-chat-vs-voice-conversation-boundaries.md`](docs/adr/001-chat-vs-voice-conversation-boundaries.md).

## Stack

- Python, FastAPI, PostgreSQL, SQLAlchemy 2.0, Alembic, Pydantic v2
- Redis (holds, guardrails)
- RabbitMQ (email job dispatch when enabled)
- pytest, Docker, Docker Compose
- Next.js public web demo (`web/`)
- **LLM providers:** `fake` (default local/CI), optional `groq` and `bedrock`
- **Email providers:** FakeEmailProvider (default), optional Resend
- **Voice:** Retell web calls, webhooks, and tool adapter (optional; disabled by default)

## Main capabilities

The receptionist supports:

- New appointment booking (written chat; Retell voice when enabled)
- Existing patient lookup and lightweight registration
- Scheduled appointment lookup, rescheduling, and cancellation (written chat; Retell voice when enabled)
- Doctor and specialty information, availability lookup, temporary slot holding
- Retell voice tools when configured: `get_clinic_context`, availability, hold/release, booking/cancel/reschedule with explicit confirmation
- Simulated human escalation case creation and durable staff notification email jobs
- Booking and reschedule confirmation email jobs (fake provider by default)

## Demo clinic scenario

The demo uses a fictional US clinic in the `America/New_York` timezone.

Business hours default to Monday–Friday, 09:00–17:00 clinic local time. The backend resolves relative dates and enforces business rules.

Patient lookup in the demo uses simplified identity matching based on name and date of birth, with email or phone used to disambiguate when needed. This is sufficient for the fictional scheduling workflow, but it is not production-grade healthcare identity verification. A real clinic deployment would require clinic-specific verification rules, additional identifiers, and manual review or human escalation for ambiguous matches. Human escalation is simulated in this demo. Patient records avoid highly sensitive identifiers such as SSN.

**Seeded patients for manual testing:** John Miller (1985-04-12) and Ava Thompson (1992-09-03).

**Seeded doctors:** Dr. Emily Carter (Dermatology), Dr. Michael Reed (Cardiology), Dr. Sarah Mitchell (Primary Care).

## Current status

This repository is a **portfolio demo with production-minded backend patterns**. Written chat appointment flows, persistence, email workers, human escalation, health endpoints, structured logging, and the public web shell are implemented and demo-ready. Retell voice, Groq/Bedrock LLM, and Resend email are optional integration boundaries—not active in the default local demo.

The system was validated against a managed-service deployment setup (API, worker, Postgres, Redis, RabbitMQ, and optional provider integrations). The **portfolio presentation uses the recorded demo video** above rather than a permanently hosted live deployment. That is intentional: free-tier hosting introduces cold starts and does not provide a practical always-on background worker for email jobs. The production-style architecture remains in the codebase and docs for API + worker + Postgres + Redis + RabbitMQ + provider integrations when you deploy with managed services.

**Architecture overview:** [`docs/architecture/overview.md`](docs/architecture/overview.md)

**Configuration and deployment:**

- [`docs/configuration.md`](docs/configuration.md)
- [`.env.example`](.env.example) — local development
- [`.env.demo.example`](.env.demo.example) — hosted public demo template
- [`docs/operations/local-development.md`](docs/operations/local-development.md)
- [`docs/operations/public-demo-deployment.md`](docs/operations/public-demo-deployment.md)

**Key architecture docs:**

- [`docs/architecture/chat-appointment-management.md`](docs/architecture/chat-appointment-management.md) — written-chat booking, lookup, reschedule, cancel
- [`docs/architecture/public-demo-guardrails.md`](docs/architecture/public-demo-guardrails.md)
- [`docs/roadmap.md`](docs/roadmap.md) — engineering scope ledger

## Deployment and demo modes

| | **Local mode** | **Managed-service deployment** |
|---|---|---|
| **Purpose** | Development, CI, and interactive exploration | Validated portfolio deployment pattern (not maintained as the primary public demo) |
| **Config template** | [`.env.example`](.env.example) | [`.env.demo.example`](.env.demo.example) |
| **Providers** | Fake LLM and fake email by default | Optional Groq / Resend / Retell when configured |
| **Guardrails** | Disabled | Redis-backed rate limits and quotas |
| **Patient data** | Fictional demo clinic only | Fictional demo clinic only — no real PHI |
| **Email worker** | RabbitMQ consumer or polling fallback | Separate worker process when `EMAIL_JOB_DISPATCH_ENABLED=true` |

**Local mode** needs no external API keys. Use Docker Compose for PostgreSQL, Redis, and RabbitMQ. After migrations and `python -m scripts.seed_demo_data`, refresh demo availability with `python -m app.scripts.generate_demo_availability`.

**Managed-service deployment** (documented for reference) enables bounded internet-facing access with `PUBLIC_DEMO_MODE=true` and guardrails enabled. Deploy as **two processes** from the same Docker image when email dispatch is enabled:

- **API:** `python -m uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}`
- **Email worker:** `python -m scripts.run_email_worker`

An API-only deployment on free tiers can disable background email dispatch (`EMAIL_JOB_DISPATCH_ENABLED=false`); durable `EmailJob` records are still created, but outbound mail requires the worker and RabbitMQ path described in the architecture docs.

Health checks: `GET /health` and `GET /health/dependencies`.

Full deploy steps: [`docs/operations/public-demo-deployment.md`](docs/operations/public-demo-deployment.md).

## Public web demo

The `web/` app is a minimal Next.js frontend. Chat and scheduling rules stay on the FastAPI backend.

- Landing page with demo disclaimer
- Backend-powered chat panel (`POST /api/v1/chat/messages` via same-origin proxy)
- Feature-flagged voice demo (`NEXT_PUBLIC_VOICE_DEMO_ENABLED`; off by default)
- Public env vars only — no Retell or provider keys in the browser

**Quick start** (backend on port 8000):

```bash
cd web
cp .env.example .env.local
npm install
npm run dev
```

Open [http://localhost:3000](http://localhost:3000). Details: [`web/README.md`](web/README.md).

## Validation

Manual checks before sharing the portfolio:

- **Backend:** `pytest` (full suite)
- **Frontend:** `cd web && npm run build` (and `npm run lint` / `npm run typecheck` as needed)
- **Demo flow:** follow the [recorded demo](https://youtu.be/v2MyZqSqsJ8) scenarios locally, or replay the video for the public portfolio presentation

Do not commit `.env`, provider API keys, or webhook secrets. Use `.env.example` and `.env.demo.example` as templates only.
