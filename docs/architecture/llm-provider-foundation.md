# LLM Provider Foundation

## Context

The AI Clinic Receptionist Platform introduces an LLM provider boundary after the deterministic chat booking flow is already working.

This avoids building the product around unvalidated model behavior.

## Current Implementation

The current implementation includes:

- `LLMProvider` protocol
- `LLMRequest`
- `LLMResponse`
- `FakeLLMProvider`
- `ReceptionistLLMAnalysis` structured output schema
- structured output parser
- local JSON object extraction repair
- LLM reliability failure reasons
- `LLMReceptionistAnalysisService`
- optional chat shadow analysis metadata

No real LLM provider is called in this implementation phase.

No API key is required.

## Design Principle

LLM output is untrusted input.

Structured output makes it parseable.

Validation makes it usable.

Business services make it real.

## Why Fake Provider First

The fake provider makes AI behavior:

- deterministic
- testable
- free to run
- safe for CI
- independent from external outages
- independent from vendor-specific APIs

## Reliability Boundary

The LLM analysis flow is bounded:

1. One provider call per chat message.
2. Local JSON extraction repair is allowed.
3. A second LLM retry is not enabled by default.
4. Invalid output falls back to safe fallback analysis.
5. Safety violations are rejected.
6. Low confidence is recorded but does not create side effects.

## Observability

Each LLM analysis result records:

- model
- input tokens
- output tokens
- estimated cost in micros
- latency in milliseconds
- attempt count
- fallback usage
- failure reason
- confidence
- safety flags

This prepares the system for future real-provider cost and latency tracking without adding a full billing or analytics subsystem.

## Shadow Mode

Chat may record LLM analysis metadata, but the deterministic chat flow remains the source of behavior.

The LLM does not:

- create appointment holds
- create appointments
- send emails
- bypass patient identity validation
- bypass explicit confirmation
- provide clinical diagnosis

LLM output is advice, not business truth.

## Business Boundary

Durable business operations remain owned by deterministic services:

- `SchedulingService`
- `AppointmentHoldService`
- `AppointmentBookingService`
- `EmailJobService`

## Safety Boundary

Emergency handling remains deterministic and highest priority.

The LLM provider must not be trusted to handle medical emergencies without deterministic guardrails.

## Human Escalation Direction

Human escalation should be modeled as operational handoff, not as another LLM pretending to be human.

Future escalation work should create auditable escalation records and optional notification jobs.

## Future Work

Future implementation phases may add:

- structured-output-assisted slot filling
- conversation health and escalation signals
- human escalation foundation
- AWS Bedrock provider
- provider timeouts
- fallback models
- cost tracking aggregation
- prompt versioning
- trace/span metadata
- model evaluation fixtures