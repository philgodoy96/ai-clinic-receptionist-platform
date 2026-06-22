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
- Retell voice tool-calling adapter (availability, hold, release)
- Voice conversation bridge linking Retell calls to shared `Conversation` state
- Chat receptionist flow
- Tool calling
- Appointment scheduling
- Appointment rescheduling
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
- FakeLLMProvider by default
- Optional Groq LLM provider adapter for hosted public demo
- Optional Bedrock LLM provider adapter
- FakeEmailProvider by default
- Optional Resend email provider
- Prometheus
- Grafana
- OpenTelemetry

## Main Capabilities

The receptionist will support:

- New appointment booking
- Existing patient lookup
- Lightweight patient registration
- Appointment lookup
- Appointment rescheduling
- Appointment cancellation
- Doctor information
- Specialty information
- Availability lookup
- Temporary appointment slot holding
- Retell voice tools: availability check, slot hold, and hold release (via verified `POST /api/v1/retell/tools`)
- Voice conversation bridge: link Retell calls to shared `Conversation` state with safe `voice_context`
- Human escalation case creation
- Confirmation email jobs

## Demo Clinic Scenario

The demo uses a fictional US clinic.

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

Implemented foundations include deterministic chat booking, scheduling tools, Redis holds, background email jobs with durable retry policy and optional Resend provider, human escalation, an LLM provider boundary with fake as the default provider and optional Groq (public demo) and Bedrock adapters, LLM reliability orchestration with bounded retries and optional fallback provider, an offline LLM evaluation dataset for structured receptionist analysis quality, optional provider-run evaluation mode for manual local checks, and Redis-backed public demo guardrails for bounded unauthenticated access.

Configuration reference:

- `docs/configuration.md`
- `.env.example`

Architecture docs:

- `docs/architecture/groq-llm-provider.md`
- `docs/architecture/real-llm-provider-adapter.md`
- `docs/architecture/llm-provider-foundation.md`
- `docs/architecture/llm-reliability-orchestration.md`
- `docs/architecture/llm-evaluation-dataset.md`
- `docs/architecture/provider-run-evaluation-mode.md`
- `docs/architecture/public-demo-guardrails.md`
- `docs/architecture/email-dispatch-reliability.md`
- `docs/architecture/retell-webhook-security.md`
- `docs/architecture/retell-call-lifecycle.md`
- `docs/architecture/retell-tool-calling-adapter.md`
- `docs/architecture/voice-conversation-bridge.md`

## Local Mode vs Public Demo Mode

**Local mode** is the default for development and CI:

- `PUBLIC_DEMO_MODE=false`
- `PUBLIC_DEMO_GUARDRAILS_ENABLED=false`
- `LLM_PROVIDER=fake` or `LLM_PRIMARY_PROVIDER=fake`
- `LLM_MAX_PRIMARY_ATTEMPTS=2`
- `LLM_FALLBACK_ENABLED=false`
- `EMAIL_PROVIDER=fake`
- `RETELL_ENABLED=false`
- no Groq, Bedrock, Resend, or Retell API keys required
- Docker Compose for PostgreSQL, Redis, and RabbitMQ

**Groq public demo mode** is intended for a hosted unauthenticated demo with real LLM analysis:

- `PUBLIC_DEMO_MODE=true`
- `PUBLIC_DEMO_GUARDRAILS_ENABLED=true`
- `LLM_PRIMARY_PROVIDER=groq`
- `GROQ_API_KEY=...` and `GROQ_MODEL=...`
- `GROQ_RESPONSE_FORMAT=json_schema`
- `LLM_MAX_PRIMARY_ATTEMPTS=2`
- `LLM_FALLBACK_ENABLED=false`
- optional `EMAIL_PROVIDER=resend` for real confirmation emails

Groq output still flows through the same parse, repair, validation, and safety checks as fake and Bedrock providers. LLM suggestions never create holds, appointments, emails, or escalations directly.

**Email mode:** `EMAIL_PROVIDER=fake` records outbound messages in memory for workers and tests. No Resend API key is required. Set `EMAIL_PROVIDER=resend` with `RESEND_API_KEY` and `EMAIL_FROM_ADDRESS` only for a hosted public demo that sends real mail.

Email delivery is at-least-once: Postgres `EmailJob` is the source of truth, RabbitMQ wake-up messages only trigger workers, and retry timing is controlled by `next_attempt_at`. Resend idempotency keys reduce duplicate-send risk but do not guarantee exactly-once delivery across the external provider.

**Public demo guardrails** apply when hosting the unauthenticated demo:

- `PUBLIC_DEMO_MODE=true`
- `PUBLIC_DEMO_GUARDRAILS_ENABLED=true`
- Redis-backed per-IP and global quotas on chat, Retell tools, appointments, and confirmation emails
- standardized `429` responses when limits are exceeded
- protected endpoints fail closed when guardrails are enabled but Redis is unavailable
- use `EMAIL_PROVIDER=resend` only with guardrails enabled and confirmation email quotas configured

**Retell voice integration** is disabled by default. Protected Retell tool routes and lifecycle webhook routes require signature verification when enabled for a hosted demo. Verified lifecycle events are persisted as durable `VoiceCall` and `VoiceCallEvent` records before any voice business actions. Supported voice tools are `check_availability`, `hold_appointment_slot`, and `release_appointment_hold` via `POST /api/v1/retell/tools`. Retell tools resolve safe voice conversation context through the voice conversation bridge. See `docs/architecture/retell-webhook-security.md` for the verification flow, `docs/architecture/retell-call-lifecycle.md` for lifecycle ingestion and inspection APIs, `docs/architecture/retell-tool-calling-adapter.md` for tool execution and safety boundaries, and `docs/architecture/voice-conversation-bridge.md` for `VoiceCall` to `Conversation` linkage and safe context rules.

See `docs/architecture/groq-llm-provider.md` for Groq provider details, `docs/architecture/public-demo-guardrails.md` for guardrail design, `docs/architecture/email-dispatch-reliability.md` for email job reliability, and `docs/configuration.md` for all environment variables.