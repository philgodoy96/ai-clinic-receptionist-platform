# Chat Turn Understanding Architecture

## Purpose

Chat Turn Understanding is the semantic interpretation boundary for written chat.

It answers:

> Given the current conversation state, what does the latest user message mean?

It does **not** execute actions. It produces structured candidates—intent, extracted fields, ambiguity signals, and clarification questions—that the backend validates before any durable side effect runs.

Architectural rationale: [ADR-004: Introduce Structured Chat Turn Understanding](../adr/004-structured-chat-turn-understanding.md).

## Core Principle

```text
The LLM understands. The backend validates and decides. Domain services execute.
```

In practice:

- The **interpreter** extracts intent and candidate fields from the latest user message in context.
- **Backend validation** accepts or rejects those candidates against scheduling catalogs, date/time parsers, identity rules, and conversation invariants.
- **Domain services** perform scheduling, holds, booking, cancellation, rescheduling, patient lookup, and emails only after validation and explicit state transitions pass.

The interpreter must never book, cancel, reschedule, create patients, send emails, or mutate conversation state directly.

## Why This Is Separate from ReceptionistLLMAnalysis

`ReceptionistLLMAnalysis` is a broad, general receptionist analysis schema used today for shadow metadata and structured-output-assisted slot filling. It classifies urgency, safety flags, and coarse extracted scheduling hints.

`ChatTurnUnderstandingResult` is a **state-aware, conversational** contract. It is shaped for backend-owned chat orchestration and includes:

- `expected_response_type` alignment (what the assistant was waiting for)
- `confirmation_decision` and `patient_status_answer`
- `missing_fields` and `ambiguous_fields` with explicit field issues
- `selected_slot_reference` for offered-slot selection
- `clarification_question` when the turn is unclear

`ChatTurnUnderstandingResult` should **not** replace or reuse `ReceptionistLLMAnalysis` as the primary chat turn contract. The two layers may coexist during migration: receptionist analysis for existing shadow/slot-filling paths; chat turn understanding for the new interpretation boundary.

See also: [Structured-Output-Assisted Slot Filling](structured-output-slot-filling.md), [Chat LLM Interpretation Reliability](chat-llm-reliability.md).

## Main Components

### Domain contract — `app/domain/chat_turn_understanding.py`

| Type | Role |
| --- | --- |
| `ChatTurnUnderstandingRequest` | Input snapshot: user message, conversation state, expected response type, context, catalogs, offered slots, allowed intents |
| `ChatTurnUnderstandingResult` | Output: intent, confirmation/patient-status answers, extracted fields, missing/ambiguous fields, slot selection, clarification, confidence, diagnostic `reason` |
| Enums | `ConversationState`, `ExpectedResponseType`, `ChatTurnIntent`, `ConfirmationDecision`, `PatientStatusAnswer` |
| `FieldIssue` | Structured ambiguity or missing-field representation with optional candidates and clarification question |
| `ExtractedTurnFields` | Normalized and raw candidate values (e.g. `date_of_birth` + `date_of_birth_raw`) |

### Interpreter protocol — `app/services/chat_turn_understanding_interpreter.py`

`ChatTurnUnderstandingInterpreter` defines the synchronous boundary:

```python
def interpret(
    self,
    request: ChatTurnUnderstandingRequest,
) -> ChatTurnUnderstandingResult:
    ...
```

All interpreters—fake, LLM-backed, or future Groq-backed—implement this protocol.

### Deterministic fake — `app/services/fake_chat_turn_understanding_interpreter.py`

`FakeChatTurnUnderstandingInterpreter` is scenario-driven and deterministic. See [Fake Interpreter](#fake-interpreter).

### Prompt/schema foundation

| Module | Role |
| --- | --- |
| `app/ai/chat_turn_understanding_schema.py` | `build_chat_turn_understanding_openai_json_schema()` — OpenAI-style strict JSON schema from `ChatTurnUnderstandingResult` |
| `app/ai/chat_turn_understanding_prompt.py` | Prompt accessor; resolves current prompt version and system prompt text |
| `app/ai/prompts/chat_turn_understanding_v1.py` | Versioned prompt text (`chat-turn-understanding-v1`) |
| `app/ai/prompt_versions.py` | Registry entry: name `chat-turn-understanding`, schema `ChatTurnUnderstandingResult` |

### LLM-backed foundation — `app/services/llm_chat_turn_understanding_interpreter.py`

`LLMChatTurnUnderstandingInterpreter` builds provider requests, parses structured output, and applies retry/repair/fallback. It is tested with fake providers only and is **not** wired to Groq or chat runtime yet. See [LLM-Backed Interpreter](#llm-backed-interpreter).

## Request / Result Flow

```text
1. Backend builds ChatTurnUnderstandingRequest
      (state, expected response type, context, catalogs, offered slots, user message)

2. Interpreter reads user message + state + expected response type + context

3. Interpreter returns ChatTurnUnderstandingResult
      (intent, extracted candidates, ambiguity/missing signals)

4. Backend validates extracted fields
      (catalog lookup, date/time normalization, identity rules, allowed intents)

5. Backend decides next state / assistant action

6. Domain services execute side effects only after invariants pass
      (holds, booking, patient creation, emails, etc.)
```

The `reason` field on `ChatTurnUnderstandingResult` is **diagnostic only**. Backend logic must not branch on `reason`; it validates fields and applies policy deterministically.

## Appointment Intake Orchestration

Appointment intake is the first production wiring of `ChatTurnUnderstandingResult` into scheduling context collection.

`ChatAppointmentIntakeOrchestrator` (`app/services/chat_appointment_intake.py`) runs inside `ChatReceptionistService` **before** the existing deterministic availability routing. It is active only when `CHAT_TURN_UNDERSTANDING_INTERPRETER` is not `disabled`.

### Flow

```text
1. User sends a scheduling message during appointment intake
      (not during active booking identity, hold confirmation, or cancel/reschedule)

2. ChatAppointmentIntakeOrchestrator builds ChatTurnUnderstandingRequest
      (state, expected response type, known specialties/doctors, offered doctors/slots)

3. Interpreter returns ChatTurnUnderstandingResult
      (intent, extracted specialty/doctor/date/time candidates, ambiguity signals)

4. Orchestrator validates candidates against backend catalogs and parsers
      (specialty catalog, doctor catalog, NaturalLanguageDateParser, TimePreferenceParser)

5. Orchestrator returns ChatAppointmentIntakeResult
      (context updates, optional earliest-search criteria, or clarification)

6. ChatReceptionistService merges context updates and continues the existing flow
      (availability lookup, offered slots in chat_context, hold, identity, confirmation)

7. Domain services execute durable side effects only after hold + identity + confirmation pass
```

### Responsibilities

| Layer | Responsibility |
| --- | --- |
| **Interpreter (CTU)** | Extract candidate meaning from the latest user message in context |
| **Appointment intake orchestrator** | Validate specialty, doctor, date, time, and soonest intent; resolve offered-doctor selection; build search criteria |
| **Chat receptionist** | Route to availability, store `offered_slots`, create holds, collect identity, require explicit confirmation |
| **Scheduling domain services** | Decide availability, create holds, and book appointments |

The interpreter and orchestrator must never book, hold, cancel, or reschedule directly.

### Runtime gating

Appointment intake CTU is skipped when:

- `CHAT_TURN_UNDERSTANDING_INTERPRETER=disabled`
- the conversation already has a confirmed `appointment_id`
- booking identity collection is active (`booking_identity_step`)
- an active hold is in the booking-identity flow
- the message is a hold request

Cancel, reschedule, and emergency messages continue through the existing top-level deterministic routes.

### Earliest / soonest availability search

When the user names a specialty or doctor without an explicit date, or explicitly asks for the soonest opening, the orchestrator sets an earliest-search window in `chat_context`:

- `search_start_date` — clinic-local today from `ClinicTimeService`
- `search_end_date` — clinic today + `EARLIEST_AVAILABILITY_SEARCH_HORIZON_DAYS` (14)
- `soonest_requested` — `true` when markers such as `soonest`, `earliest`, `asap`, or `first available` appear

`ChatReceptionistService` uses these fields to call scheduling availability services and store returned slots in `offered_slots` for later hold selection.

### Offered-doctor selection

When `offered_doctors` is present in `chat_context` (for example after listing doctors for a specialty), doctor validation is **contextual**:

- only doctors from the offered list are accepted
- a unique partial match such as `Dr. Reed` or `Dr. Emily` updates `selected_doctor_id` / `selected_doctor_name`
- unknown or ambiguous offered-doctor references return clarification without mutating context

### Supported appointment-intake examples

These are representative natural-language inputs the slice is designed to handle:

| Example | Typical extraction |
| --- | --- |
| `I'd like to schedule with a dermatologist` | specialty → earliest availability search |
| `soonest cardiology appointment` | specialty + soonest intent |
| `cardiology next Monday` | specialty + natural date |
| `Dr. Reed next Monday` | doctor + natural date |
| `I want Dr. Emily soonest available` | doctor + soonest intent |
| `It can be Dr. Reed` | offered-doctor selection (after doctor list) |
| `Dr. Emily is fine` | offered-doctor selection (after doctor list) |
| `tomorrow morning` | natural date + time-of-day window |
| `Sunday afternoon` | natural date + time-of-day window |

Exact routing still depends on current `chat_context` (for example whether doctors or slots were already offered).

### Expected behavior

- **Specialty without date** can trigger earliest availability search instead of asking for `YYYY-MM-DD`.
- **Doctor without date** can trigger earliest doctor availability search.
- **Doctor selection from offered doctors** updates `selected_doctor_id` / `selected_doctor_name` and proceeds toward availability.
- **Natural date prompts** should ask conversationally (`What day works best?`) rather than requesting `YYYY-MM-DD`.
- **Offered slots** are stored in `chat_context.offered_slots` and are later used by the existing hold flow.
- **No appointment is booked** until the existing final confirmation flow succeeds (hold → identity → explicit confirmation → `AppointmentBookingService`).
- **Ambiguous CTU output** (low confidence, `ambiguous_fields`, or unsupported intents) returns clarification or falls back to deterministic parsing without unsafe context mutation.
- **Conflicting context changes** (for example switching doctors after one was already selected) return clarification instead of silent overwrite.

### Known follow-up

There is a known follow-up to audit demo availability timezone/seed behavior. Displayed availability may appear several hours later than expected when UTC storage and clinic-local presentation are not aligned. This slice does not fix that behavior.

See [Chat Appointment Intake Manual Testing](../testing/chat-appointment-intake.md) for reproducible fake and Groq test scenarios.

## Reliability Behavior

`LLMChatTurnUnderstandingInterpreter` follows the same reliability patterns as `LLMReceptionistAnalysisService` and [Chat LLM Interpretation Reliability](chat-llm-reliability.md).

| Situation | Behavior |
| --- | --- |
| Valid provider JSON | Parsed and validated into `ChatTurnUnderstandingResult` |
| Invalid JSON | Local repair via structured-output helpers when possible (e.g. fenced or wrapped JSON) |
| Schema-invalid output | Primary retry with repair instruction when policy allows |
| Retryable provider error | Primary provider retried once |
| Primary exhausted | Fallback provider attempted when configured and eligible |
| All paths fail | Safe fallback result returned; errors do not escape to chat runtime |

**Safe fallback result:**

- `intent = fallback`
- `confidence = 0.0`
- neutral `clarification_question`
- diagnostic `reason` (observability only)
- no side effects

When wired into production, provider and parse failures must be absorbed by the interpreter and surfaced only as a safe fallback turn understanding—not as unhandled exceptions in the chat path.

## Ambiguity Policy

The backend owns final date, catalog, and slot resolution. The interpreter represents uncertainty explicitly rather than guessing.

| Input / situation | Policy |
| --- | --- |
| `19/09/1996` | May be accepted as day-first when day > 12 makes month-first impossible → `1996-09-19` |
| `09/10/1996` | Ambiguous numeric date unless locale/date policy resolves it; emit `ambiguous_fields` with candidates |
| `2pm` with offered slots | Select a slot only if **exactly one** offered slot matches `14:00` |
| `2pm` with multiple 2pm slots | Do not guess; emit ambiguous `selected_slot_reference` issue |
| Partial doctor name | Resolve only if **exactly one** known doctor matches |
| Multiple doctor matches | Emit `ambiguous_fields` for `doctor_name` |
| Specialty alias `derm` | Resolve through backend-provided `known_specialties` (e.g. alias → `Dermatology`) |

The fake interpreter encodes these scenarios deterministically for tests and future local demos.

## Fake Interpreter

`FakeChatTurnUnderstandingInterpreter` is:

- **Deterministic** — same input + request context → same result
- **Scenario-driven** — targeted phrase and state patterns, not a general NLP parser
- **Side-effect free** — no provider calls, no repository calls, no request mutation
- **Test/demo oriented** — supports patient identity, confirmation, slot selection, appointment request, catalog alias, and doctor partial-match scenarios

Use it in unit tests and future local demo flows without calling real LLM providers.

## LLM-Backed Interpreter

`LLMChatTurnUnderstandingInterpreter`:

- Uses `build_chat_turn_understanding_system_prompt()` and the chat turn understanding prompt version from the registry
- Renders a deterministic context snapshot (`conversation_state`, `expected_response_type`, `allowed_intents`, catalogs, offered slots, etc.) plus the latest user message
- Parses provider output through existing structured-output helpers into `ChatTurnUnderstandingResult`
- Applies retry, repair prompt, fallback provider, and safe fallback as described above
- Does **not** execute actions or persist durable scheduling side effects directly

When `CHAT_TURN_UNDERSTANDING_INTERPRETER=groq`, `build_chat_turn_understanding_interpreter_from_settings()` wires a `GroqLLMProvider` with `GROQ_RESPONSE_FORMAT=json_schema` into appointment intake and booking identity orchestrators. Use Groq only for controlled local testing with authentication, rate limits, and cost controls on any public demo.

## Persistence (Existing Slice)

A separate persistence slice records per-turn diagnostics in `chat_turn_understandings` for auditability. That data is **diagnostic only**—it does not drive replies or execute side effects.

| Data | Location |
| --- | --- |
| Raw user text | `conversation_messages.content` |
| Assistant reply text | `conversation_messages.content` |
| Structured turn understanding | `chat_turn_understandings` |
| Legacy LLM shadow summary | `conversation_messages.message_metadata.llm_shadow_analysis` (compatibility) |
| Durable business outcomes | `audit_logs` |

Record lifecycle today (via `ChatReceptionistService`):

1. User message appended.
2. `LLMReceptionistAnalysisService` optionally produces `ReceptionistAnalysisResult`.
3. `LLMChatSlotFillingService` optionally validates extracted candidates.
4. Deterministic chat logic generates reply and side effects.
5. `ChatTurnUnderstandingRecordService.record_best_effort(...)` persists one row.
6. Chat route commits the transaction.

Appointment intake and booking identity orchestrators consume `ChatTurnUnderstandingResult` at runtime, but per-turn persistence still records receptionist analysis / slot-filling metadata rather than full appointment-intake CTU payloads.

## What Is Intentionally Not Changed

- **Public Chat API schema** — response fields such as `intent`, `reply`, `appointment_id`, and `booking_confirmed` are unchanged
- **Hold and booking confirmation flow** — holds, identity collection, explicit confirmation, and `AppointmentBookingService` rules are unchanged
- **Retell voice channel** — no Retell prompt, tool, or adapter changes in this slice
- **LLM shadow analysis path** — `ReceptionistLLMAnalysis` and slot filling remain separate observability layers

## Related Documents

- [ADR-004: Introduce Structured Chat Turn Understanding](../adr/004-structured-chat-turn-understanding.md)
- [Chat Appointment Intake Manual Testing](../testing/chat-appointment-intake.md)
- [Structured-Output-Assisted Slot Filling](structured-output-slot-filling.md)
- [Chat LLM Interpretation Reliability](chat-llm-reliability.md)
- [Natural-Language Date Parsing Boundary](natural-language-date-parsing.md)
- [Time-of-Day Preference Parsing Boundary](time-of-day-preference-parsing.md)
- [Prompt Versioning and LLM Traceability](prompt-versioning.md)
- [Chat API Foundation](chat-api-foundation.md)
- [Audit Logs](audit-logs.md)
