# Architecture Overview

## Business Context

AI Clinic Receptionist Platform is a production-style applied AI and backend systems project for a fictional US-based clinic.

The system allows patients to interact with an AI receptionist through:

1. A backend-orchestrated chat channel
2. A Retell-orchestrated voice channel

The receptionist supports appointment scheduling, rescheduling, cancellation, patient lookup, doctor and specialty information, temporary slot holding, human escalation, guardrails, auditability, observability, and background confirmation jobs.

## Non-Goals

The system does not implement:

- A full clinic CRM
- Billing
- Insurance workflows
- Medical diagnosis
- Electronic health records
- OAuth in V1
- A custom voice WebSocket simulator
- A generic chatbot experience unrelated to clinic workflows

## Core Architecture Decision

Chat UX and voice UX must not be modeled as the same interaction pattern.

Chat is backend-orchestrated.

Voice is Retell-orchestrated.

The backend exposes tools and business capabilities to Retell but does not manage every spoken turn.

## Channels

### Chat Channel

The chat channel is backend-orchestrated.

The backend manages:

- Conversation state
- Messages
- Slot collection
- Tool decisions
- LLM responses
- Persistence
- Guardrails
- Audit logs
- Observability

### Retell Voice Channel

The Retell voice channel is provider-orchestrated.

Retell manages:

- Real-time voice UX
- Turn-taking
- Speech-to-text
- Text-to-speech
- Low-latency conversation flow

The backend exposes tools for:

- Patient lookup
- Patient creation
- Specialty listing
- Doctor listing
- Availability lookup
- Appointment slot holding
- Appointment booking
- Upcoming appointment listing
- Appointment rescheduling
- Appointment cancellation
- Escalation case creation

## Backend Responsibilities

The backend is responsible for:

- Validating tool payloads
- Enforcing business rules
- Protecting patient data
- Persisting durable state
- Creating audit logs
- Publishing background jobs
- Exposing admin/demo endpoints
- Emitting metrics
- Creating traces
- Providing dependency health checks

## Core Entities

Initial domain entities:

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

## Durable State

PostgreSQL stores:

- Patients
- Doctors
- Specialties
- Availability slots
- Appointments
- Appointment hold history
- Conversations
- Messages or transcripts
- Tool calls
- Audit logs
- Escalation cases
- Job executions

## Temporary State

Redis stores:

- Appointment slot holds
- Rate limits
- Short-lived locks

Redis must not be treated as durable conversation memory.

## Background Jobs

RabbitMQ is used for:

- Appointment confirmation email jobs
- Reschedule confirmation email jobs
- Cancellation email jobs

The first implementation uses FakeEmailProvider.

A real provider such as ResendProvider can be added later behind the same provider interface.

## Appointment Hold Flow

The appointment hold flow protects against double booking.

1. Chat or Retell checks availability.
2. User selects a slot.
3. Backend creates a temporary hold in Redis.
4. Backend returns hold_id.
5. User confirms.
6. Backend validates hold ownership and expiration.
7. Backend creates appointment in PostgreSQL.
8. Backend removes hold.
9. Backend enqueues confirmation email job.

Example Redis key pattern:

    appointment_hold:{doctor_id}:{start_time}

Initial TTL:

    5 minutes

## Guardrails

Guardrails exist in two layers:

1. Provider prompt/runtime guidance
2. Backend validation and enforcement

Provider guidance should prevent unsafe conversational behavior.

Backend enforcement must protect data and business invariants.

Important principle:

    Provider prompt guides behavior. Backend enforces policy.

Public demo guardrails add a third operational layer for unauthenticated hosted demos:

- Redis-backed per-IP and global quotas
- Protected chat and Retell tool routes
- Standardized `429` responses when limits are exceeded

See: [Public Demo Guardrails](public-demo-guardrails.md)

## Observability

Observability is part of the system design.

The system should track:

- Structured logs
- request_id
- correlation_id
- conversation_id
- Audit logs
- Prometheus metrics
- OpenTelemetry traces
- Health endpoints
- Dependency health endpoint

Important events include:

- conversation_started
- conversation_completed
- patient_lookup_requested
- appointment_slot_held
- appointment_booked
- appointment_rescheduled
- appointment_cancelled
- escalation_created
- email_job_enqueued
- email_job_completed
- tool_call_failed
- guardrail_triggered
- rate_limit_blocked

## Scaling Considerations

The backend should support horizontal scaling by keeping durable and shared state outside application memory.

Shared components:

- PostgreSQL for durable records
- Redis for temporary operational state
- RabbitMQ for background jobs

Important scaling constraints:

- Appointment booking must avoid double booking.
- Redis holds must expire automatically.
- PostgreSQL should enforce uniqueness for active appointment slots.
- Workers must process jobs idempotently.
- Provider calls must be observable and rate-limited.

## Security Considerations

V1 keeps authentication intentionally simple for demo usage.

Public/demo routes:

- Chat demo
- Retell tool endpoints

Admin/demo routes may be open locally for easy testing.

Production hardening should include:

- Admin authentication
- X-Admin-Token
- Retell webhook signature validation
- Provider callback allowlisting
- Provider budget limits
- Stronger abuse protection

The system must avoid unnecessary sensitive identifiers such as SSN.