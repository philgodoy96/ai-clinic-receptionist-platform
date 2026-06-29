# Architecture Overview

## Business context

AI Clinic Receptionist Platform is a portfolio demo of a production-minded applied AI and backend system for a fictional US-based clinic.

Patients interact through:

1. **Written chat** (default local demo) — backend-orchestrated deterministic appointment workflow simulator with optional LLM-assisted analysis and phrasing
2. **Retell voice** (optional) — provider-orchestrated when explicitly enabled and configured

The receptionist supports appointment scheduling, rescheduling, cancellation, patient lookup, temporary slot holding, human escalation records, guardrails, auditability, structured observability, and durable background email jobs.

## Demo scope and non-goals

This demo is not clinical advice, diagnosis, emergency triage, or medical decision support. Do not enter real patient data.

Outside demo scope (intentional boundaries or production-hardening concerns):

- Full clinic CRM, billing, insurance workflows, electronic health records
- Patient profile updates
- Advanced unsafe/abusive message moderation policy
- Auth/RBAC on public demo routes
- Live human operator console or real-time handoff queue
- OpenAI, Vapi, or Twilio integrations
- Cancellation confirmation emails
- Prometheus/OpenTelemetry runtime export and Grafana dashboards

## Core architecture decision

Chat UX and voice UX must not be modeled as the same interaction pattern.

**Chat** is backend-orchestrated. The backend manages conversation state, routing, validation, and domain execution.

**Voice** is Retell-orchestrated when enabled. Retell manages real-time voice UX; the backend exposes tools and enforces business rules.

## Integration readiness matrix

| Component / provider | Status | Default demo behavior | Production / optional integration note |
|---|---|---|---|
| FastAPI | Implemented | API server for chat, scheduling, health, optional Retell routes | Deploy API and email worker as separate processes |
| PostgreSQL | Implemented | Durable patients, appointments, conversations, email jobs, escalations | Managed Postgres required for hosted demo |
| Redis | Implemented | Appointment holds; guardrails when enabled | Required for holds and public demo quotas |
| RabbitMQ / email worker | Implemented | Wake-up dispatch when `EMAIL_JOB_DISPATCH_ENABLED=true`; polling fallback available | Worker processes durable `EmailJob` records |
| Fake email | Implemented (default) | Records sends in memory; no outbound mail | Local development and CI default |
| Resend | Implemented (optional) | Not active unless `EMAIL_PROVIDER=resend` | Verified sending domain required for hosted demo |
| Fake LLM | Implemented (default) | Deterministic chat orchestration; fake CTU interpreter | Local development and CI default |
| Groq | Implemented (optional) | Not active unless configured | Hosted demo LLM analysis/phrasing when enabled |
| Bedrock | Implemented (optional) | Not active unless configured | Enterprise-style AWS adapter |
| Retell | Implemented (optional) | Disabled by default (`RETELL_ENABLED=false`) | Voice tools, webhooks, web calls when configured |
| Human escalation | Implemented | Durable `HumanEscalation` record + staff notification email job | No live operator console; fake email by default |
| Structured logging | Implemented | JSON logs, `request_id`, `correlation_id`, audit log correlation | stdout by default |
| Health endpoints | Implemented | `GET /health`, `GET /health/dependencies` | Readiness checks PostgreSQL |
| Prometheus / OpenTelemetry | Extension point | Not runtime-wired in the app | Production-hardening export; dependencies present but not active |
| Auth / RBAC | Outside demo scope | Public routes unauthenticated | Production hardening: admin auth, webhook signatures, guardrails |

## Channels

### Chat channel

The chat channel is backend-orchestrated.

The backend manages:

- Conversation state and messages
- Deterministic routing and state guards for booking, cancellation, rescheduling, lookup, and human escalation
- Slot collection and appointment management
- Optional LLM-assisted turn understanding and response phrasing (fake provider by default)
- Persistence, guardrails, audit logs, and structured logging

Written-chat appointment flows follow:

    The optional LLM understands. The backend validates and decides. Domain services execute.

Selection and revision are supported before final confirmation. Destructive actions require explicit confirmation where applicable.

See [Chat Appointment Management](chat-appointment-management.md).

### Retell voice channel (optional)

When `RETELL_ENABLED=true`, the Retell voice channel is provider-orchestrated.

Retell manages real-time voice UX, turn-taking, speech-to-text, and text-to-speech.

The backend exposes tools for patient lookup, scheduling, holds, booking, cancellation, rescheduling, and escalation case creation.

Default local demo is chat-first; voice requires explicit configuration.

## Backend responsibilities

The backend is responsible for:

- Validating tool payloads and enforcing business rules
- Persisting durable state and creating audit logs
- Publishing background email jobs
- Exposing health and demo endpoints
- Structured JSON logging with request and correlation IDs
- Dependency health checks

The backend does not provide auth/RBAC on public demo routes.

## Core entities

- Patient, Doctor, Specialty, AvailabilitySlot, Appointment, AppointmentHold
- Conversation, Message, ToolCall, AuditLog
- EscalationCase, EmailJob, JobExecution

## Durable state

PostgreSQL stores patients, doctors, specialties, availability slots, appointments, hold history, conversations, messages, tool calls, audit logs, escalation cases, email jobs, and job executions.

## Temporary state

Redis stores appointment slot holds, rate limits, and short-lived locks. Redis is not durable conversation memory.

## Background jobs

RabbitMQ dispatches email job wake-up messages when enabled. The email worker processes durable `EmailJob` records from PostgreSQL.

Job types include booking/reschedule confirmation emails and `human_escalation_notification` staff alerts.

- **Default:** FakeEmailProvider (no outbound mail)
- **Optional:** ResendProvider when `EMAIL_PROVIDER=resend`

Cancellation confirmation emails are outside demo scope.

## Appointment hold flow

1. Chat or Retell checks availability.
2. User selects a slot.
3. Backend creates a temporary hold in Redis.
4. User confirms.
5. Backend validates hold ownership and expiration.
6. Backend creates appointment in PostgreSQL, removes hold, enqueues confirmation email job.

Channel-specific TTL: `APPOINTMENT_HOLD_TTL_SECONDS` (default 300) for voice; `CHAT_APPOINTMENT_HOLD_TTL_SECONDS` (default 600) for written chat.

## Guardrails

1. Provider prompt/runtime guidance
2. Backend validation and enforcement
3. Public demo guardrails (Redis-backed quotas when enabled)

See [Public Demo Guardrails](public-demo-guardrails.md).

## Observability

### Implemented

- Structured JSON logs to stdout
- `request_id` and `correlation_id` on requests, errors, and audit logs
- Audit logs for important domain events
- Health endpoints: `GET /health`, `GET /health/dependencies`

Important events include conversation lifecycle, scheduling actions, escalation creation, email job enqueue/complete, tool call failures, and rate-limit blocks.

See [Request Correlation and Structured Logging](request-correlation-logging.md).

### Extension points (not active in default demo runtime)

- Prometheus metrics export
- OpenTelemetry traces and distributed tracing across workers
- Log shipping and dashboards (for example Grafana)

Dependencies for Prometheus and OpenTelemetry exist in the project but are not wired into the running application.

## Human escalation

Human escalation creates a durable internal record and enqueues a staff notification email job. Local/demo mode uses the fake email provider; hosted demos may use Resend when configured.

This demo does not include a live operator console or real-time human handoff queue.

See [Human Escalation Foundation](human-escalation.md) and [Human Handoff Notification Job](human-handoff-notification-job.md).

## Security considerations

Public demo routes (chat, optional Retell tools) are intentionally unauthenticated. Auth/RBAC is outside demo scope.

Production hardening concerns include admin authentication, Retell webhook signature validation, provider budget limits, and stronger abuse protection.

The system avoids unnecessary sensitive identifiers such as SSN.

## Scaling considerations

Horizontal scaling relies on PostgreSQL (durable state), Redis (temporary state), and RabbitMQ (job dispatch). Booking must avoid double booking via Redis holds and PostgreSQL uniqueness. Workers must process jobs idempotently.
