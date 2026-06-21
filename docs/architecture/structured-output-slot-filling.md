# Structured-Output-Assisted Slot Filling

## Context

The platform already has a deterministic chat booking flow and a fake LLM provider foundation.

This implementation phase allows validated LLM structured output to assist slot filling without giving the LLM control over business side effects.

## Design Principle

LLM extracts candidates.

Backend validates candidates.

Conversation stores validated context.

Business services own side effects.

## What the LLM Can Suggest

The LLM analysis may suggest:

- specialty
- doctor name
- date
- time
- patient identity fields

These values are treated as untrusted candidates.

## Validation Boundary

Before a field is applied to `conversation_metadata.chat_context`:

- specialty must exist in `SchedulingService`
- doctor must exist in `SchedulingService`
- date must be valid `YYYY-MM-DD`
- time must be valid `HH:MM`
- patient identity fields must pass deterministic validation

Unknown or conflicting values are rejected and recorded in metadata.

## Chat Context

Validated fields may be stored in `conversation_metadata.chat_context`.

This context is conversational memory only.

It is not scheduling truth, hold truth, appointment truth, or email truth.

## Side Effect Boundary

The LLM does not:

- create Redis holds
- create appointments
- send emails
- bypass patient identity completeness
- bypass explicit confirmation
- bypass hold ownership
- provide clinical diagnosis

Durable operations remain owned by deterministic services:

- `SchedulingService`
- `AppointmentHoldService`
- `AppointmentBookingService`
- `EmailJobService`

## Metadata

Assistant message metadata may include:

- LLM shadow analysis
- slot filling applied fields
- slot filling rejected fields
- rejection reasons

Raw prompts and raw provider outputs are not stored.

Patient identity is not duplicated inside LLM shadow metadata.

## Failure Behavior

If LLM analysis is fallback, low-confidence, unsafe, or invalid, no fields are applied.

The deterministic chat flow continues.

## Future Work

Future implementation phases may add:

- natural-language date parsing
- conversation health and escalation signals
- human escalation records
- real provider adapters
- model evaluation fixtures