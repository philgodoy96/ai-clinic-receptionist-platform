# Engineering Guide

This guide defines how this repository should be developed.

The project must look like a production-style backend and applied AI systems project built incrementally by an engineer, not generated in a single session.

## Methodology

Every meaningful feature should follow:

1. Project Context
2. System Design
3. Implementation
4. Testing
5. Engineering Review
6. Conceptual Engineering Review

Implementation should not start before the relevant system responsibilities, invariants, failure modes, API boundaries, scaling considerations, security considerations, and testing strategy are defined.

## Language Rules

Project communication may happen in Portuguese.

Technical artifacts must be written in English:

- README
- Docs
- ADRs
- Branch names
- Commit messages
- PR descriptions
- File names
- API documentation
- Code comments
- Internal documentation

## Git Workflow

Use small, meaningful commits.

Do not group unrelated changes.

Each implementation slice should define:

1. Branch name
2. Branch objective
3. Files likely to change
4. Commit sequence
5. Professional commit messages
6. What to test before each commit
7. Documentation updates
8. Suggested PR title
9. Suggested PR description
10. Merge steps
11. Branch deletion steps

## Branch Naming Examples

Examples:

- chore/project-scaffold
- docs/architecture-overview
- feat/conversation-domain
- feat/scheduling-tools
- feat/appointment-holds
- feat/retell-tools
- feat/rabbitmq-email-worker
- feat/observability-metrics
- test/scheduling-invariants

## Commit Message Examples

Good commit messages:

- chore: initialize FastAPI project structure
- docs: add voice architecture overview
- docs: add ADR for chat and voice boundaries
- feat: add patient and appointment domain entities
- feat: implement availability lookup service
- feat: add Redis appointment slot holds
- feat: implement appointment booking tool
- feat: add Retell tool endpoint adapter
- feat: enqueue appointment confirmation emails
- test: cover appointment hold expiration
- docs: document Retell integration flow

Bad commit messages:

- update
- changes
- fixes
- final
- misc

## Architecture Principles

### Chat and Voice Are Different Interaction Models

Chat conversations are backend-orchestrated.

Retell voice conversations are provider-orchestrated.

The backend should not try to manage every spoken turn in a Retell call as if voice were a chat transcript.

### Provider Adapters Must Stay Outside Core Business Logic

Retell, LLM providers, and email providers must be integrated through interfaces and adapters.

Application services should depend on abstractions, not external SDKs directly.

### Backend Enforces Policy

Provider prompts guide behavior.

Backend validation enforces policy.

The backend must validate payloads, protect patient data, enforce scheduling rules, and persist audit logs.

### Redis Is Operational State, Not Durable Memory

Redis is used for:

- Appointment slot holds
- Rate limiting
- Short-lived locks

PostgreSQL is used for durable data.

### RabbitMQ Is Used for Side Effects

RabbitMQ is used for background work such as email confirmation jobs.

Request/response flows should avoid doing slow external side effects inline when possible.

## Testing Expectations

The project should include:

- Unit tests for domain logic
- Unit tests for appointment holds
- Unit tests for scheduling invariants
- Unit tests for guardrail enforcement
- Unit tests for tool payload validation
- Integration tests for booking
- Integration tests for rescheduling
- Integration tests for cancellation
- Integration tests for email job flow
- Integration tests for audit logs
- Integration tests for rate limiting
- Fake provider tests
- Retell adapter tests with mocked requests

Paid providers must not be required during tests.

## Engineering Reviews

After meaningful implementation milestones, run engineering review questions such as:

- What happens under concurrency?
- What happens if Retell sends invalid payloads?
- What happens if Redis expires a hold?
- What happens if RabbitMQ is down?
- What happens if the email worker crashes?
- Is appointment booking idempotent?
- Where are the bottlenecks?
- What would need to change for production?
- What data should be persisted?
- What data should remain temporary?

## Conceptual Engineering Reviews

After major milestones, run conceptual interview-style review questions covering:

- Chat vs voice architecture
- Retell integration
- Provider adapters
- Tool calling
- Appointment holds
- Idempotency
- Redis
- RabbitMQ
- Observability
- Guardrails
- Audit logs
- Reliability
- Scaling
- Testing strategy