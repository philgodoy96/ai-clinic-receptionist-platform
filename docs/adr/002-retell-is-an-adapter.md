# ADR-002: Retell is an adapter, not the business logic core

## Status

Accepted

## Context

AI Clinic Receptionist Platform supports Retell web voice calls.

Retell is responsible for real-time voice UX, but the backend owns scheduling rules, patient data validation, persistence, auditability, and future side effects.

A risky design would put clinic business logic directly into Retell prompts or provider-specific configuration.

That would make scheduling behavior harder to test, version, audit, and reuse across channels.

## Decision

Retell is treated as an adapter.

The backend exposes Retell-compatible tool endpoints.

Retell may call backend tools for:

- Listing specialties
- Listing doctors
- Checking availability
- Looking up patients
- Listing upcoming appointments
- Future appointment holds
- Future booking
- Future rescheduling
- Future cancellation
- Future escalation

Application services remain provider-agnostic.

Retell-specific request and response shapes live at the adapter/API boundary.

Business rules remain in backend services and persistence layers.

## Consequences

### Positive Consequences

The backend remains testable without Retell credentials.

Scheduling logic can be reused by:

- Chat channel
- Retell voice channel
- Admin/demo API
- Future frontend

Retell prompt behavior cannot bypass backend validation.

Patient data rules are enforced centrally.

The project demonstrates a realistic voice AI architecture.

### Negative Consequences

The codebase now has an additional adapter layer.

Some request/response mapping is needed between Retell tool payloads and application services.

Retell-specific tool behavior must be documented separately from generic scheduling APIs.

## Alternatives Considered

### Alternative 1: Put scheduling logic in Retell prompts

This was rejected.

Provider prompts can guide behavior, but they should not enforce clinic scheduling invariants or patient privacy rules.

### Alternative 2: Make Retell call generic scheduling API endpoints directly

This was partially rejected.

Some generic endpoints are useful, but Retell benefits from tool-specific payloads that are easier for a voice agent to use.

For example, Retell may provide `doctor_name` or `specialty_name`, while internal APIs may use stable IDs.

The adapter translates voice-friendly payloads into backend service calls.

### Alternative 3: Build a separate voice-only scheduling service

This was rejected for V1.

A separate service would create duplicated business logic and increase operational complexity too early.

## Implementation Guidance

Retell endpoints should live under:

    /api/v1/retell/tools

Retell schemas should live separately from generic scheduling API schemas.

Retell adapters should call application services.

Retell adapters should not call SQLAlchemy repositories directly.

Retell adapters should not persist data unless the relevant application service requires it.

Retell should not become the source of truth for doctors, specialties, patients, appointments, or availability.

## Related ADRs

- ADR-001: Voice UX must not be modeled as chat UX
- ADR-005: Backend enforces guardrails, provider prompt only guides behavior