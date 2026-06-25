# Structured-Output-Assisted Slot Filling

## Context

The platform already has a deterministic chat booking flow and an LLM provider foundation with fake as the default provider.

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

Real provider output is treated the same as fake provider output: untrusted candidates that must pass backend validation before merge into `chat_context`.

The offline evaluation dataset tests raw structured analysis quality—intent, urgency, safety flags, and extracted fields—before deterministic slot validation runs in chat.

Each evaluation case includes `prompt_version` so recorded outputs and accuracy metrics can be grouped by prompt. Runtime slot filling also records `prompt_version` in LLM shadow metadata, which helps trace which prompt version produced the extracted candidates under review.

## Validation Boundary

Before a field is applied to `conversation_metadata.chat_context`:

- specialty must exist in `SchedulingService`
- doctor must exist in `SchedulingService`
- date must be normalized to valid `YYYY-MM-DD` by `NaturalLanguageDateParser`
- time must be valid `HH:MM`, or a supported time-of-day preference normalized to `requested_time_window`
- patient identity fields must pass deterministic validation

Natural-language date candidates suggested by the LLM (for example `tomorrow` or `next Monday`) are normalized by the deterministic parser before they are applied to `chat_context`. Unsupported or ambiguous date phrases are rejected and recorded in metadata.

Broad time phrases such as `morning` or `afternoon` are normalized by `TimePreferenceParser` into `requested_time_window` with `label`, `start_time`, and `end_time`. Exact times such as `09:00` are stored as `requested_time`.

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

- LLM shadow analysis, including `prompt_version`
- slot filling applied fields
- slot filling rejected fields
- rejection reasons
- date parsing results when a date candidate was processed
- time preference parsing results when a time-of-day candidate was processed
- conversation health signals derived from recent message history

Rejected fields are not applied to `chat_context`, but their count can contribute to current or future conversation health signals such as repeated slot-filling rejection thresholds.

Raw prompts and raw provider outputs are not stored. The full prompt text is not stored in conversation metadata.

Patient identity is not duplicated inside LLM shadow metadata.

## Failure Behavior

If LLM analysis is fallback, low-confidence, unsafe, or invalid, no fields are applied.

The deterministic chat flow continues.

When model output fails structurally, `LLMReceptionistAnalysisService` retries internally with a repair prompt before deterministic fallback analysis is used. Safety violations and low-confidence valid outputs are not blindly retried as model failures.

See also: [Chat LLM Interpretation Reliability](chat-llm-reliability.md).

## Future Work

Future implementation phases may add:

- human escalation records

See also: [Real LLM Provider Adapter Boundary](real-llm-provider-adapter.md), [Natural-Language Date Parsing Boundary](natural-language-date-parsing.md), [Time-of-Day Preference Parsing Boundary](time-of-day-preference-parsing.md), [LLM Evaluation Dataset](llm-evaluation-dataset.md), [Prompt Versioning and LLM Traceability](prompt-versioning.md), [Chat LLM Interpretation Reliability](chat-llm-reliability.md).