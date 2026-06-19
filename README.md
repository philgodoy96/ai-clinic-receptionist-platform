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
- FakeLLMProvider first
- GroqProvider or OpenAIProvider later
- FakeEmailProvider first
- ResendProvider later
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

Documentation foundation in progress.

Runtime implementation has not started yet.