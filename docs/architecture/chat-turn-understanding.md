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

- The **interpreter (CTU)** extracts candidate appointment meaning — specialty, doctor, date, time window, soonest intent, slot reference, and availability-range phrasing — from the latest user message in context.
- **Backend validation** accepts or rejects those candidates against scheduling catalogs, date/time parsers, offered-doctor and offered-slot lists, identity rules, and conversation invariants.
- **Scheduling services** decide actual availability and return candidate slots.
- **Domain services** perform holds, booking, cancellation, rescheduling, patient lookup, and emails only after validation and explicit state transitions pass.
- **Hold and booking** remain controlled by existing backend flows; no appointment is booked without explicit final confirmation.
- **Assistant replies** must not expose internal IDs, slot IDs, hold IDs, UUIDs, raw timestamps, or other backend implementation details.

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

### Appointment task frame (`appointment_intake_awaiting`)

Active appointment intake stores an explicit task marker in `chat_context.appointment_intake_awaiting`. This prevents generic fallback replies during scheduling and enables contextual follow-ups when CTU returns low confidence or `fallback` intent.

| Value | Set when | Meaning |
| --- | --- | --- |
| `date_or_time_preference` | Specialty or doctor is selected but no concrete date is set, or availability returned no slots and the assistant is waiting for a day/time preference | User should supply a date, weekday, or time-of-day window |
| `slot_selection` | Availability lookup returned one or more offered slots | User should choose one of the offered times |

The marker is cleared when intake completes, booking identity begins, or the conversation leaves appointment scheduling.

**Contextual follow-ups** use the task frame plus existing provider context:

- User: `What about Wednesday?` — backend keeps `selected_doctor_id` / `selected_specialty_id`, resolves the bare weekday against clinic-local today, clears stale `offered_slots` if the date changes, and re-runs availability.
- User: `afternoon` — backend applies a time-of-day window via `TimePreferenceParser` when doctor/specialty context is already present, then searches availability with the updated window.
- User: `What days do you have next week?` — backend recognizes the contextual availability range, resolves the clinic-local week window via `ClinicTimeService`, and searches the selected doctor or specialty across that range instead of re-prompting for a single day.

Bare weekday and time-window follow-ups run through backend parsers even when the interpreter returns `fallback`, as long as `is_appointment_intake_active(chat_context)` is true.

### Contextual availability range follow-ups

When appointment intake is active and provider context exists, the orchestrator recognizes a narrow set of week-range questions before the generic date/time re-prompt runs:

- `What days do you have next week?`
- `What do you have next week?` / `What about next week?`
- `Any availability next week?`
- `Do you have anything this week?` / `What days are available this week?`

This is intentionally limited to `this week` and `next week`. Broader calendar phrases (next month, early next week, end of the month, recurring weekdays, exclusions) are deliberately out of scope, and `NaturalLanguageDateParser` is **not** globally changed.

Resolution and routing:

- `ChatAppointmentIntakeOrchestrator._extract_contextual_availability_range` detects the range label, gated by `is_appointment_intake_active`.
- `_resolve_week_range` resolves clinic-local Monday–Sunday dates from `ClinicTimeService.clinic_today()` (the `this week` start is clamped to today).
- With a selected doctor, the backend searches doctor availability across the range; with only a selected specialty it searches specialty availability. Without provider context it asks which doctor or specialty to check instead of searching blindly.
- `ChatReceptionistService._handle_availability_range_flow` runs the existing scheduling availability methods (`check_availability_with_status` / `check_availability_for_specialty`), groups returned slots by date, stores `offered_slots`, clears stale `selected_availability_slot_id` / `selected_start_time`, and sets `appointment_intake_awaiting = slot_selection`. It optionally stores `availability_range_label` (`this_week` / `next_week`). When nothing is open it returns a natural no-availability message rather than a generic fallback. Replies never expose slot, doctor, or hold identifiers.

### Offered doctors

When the assistant lists doctors for a specialty, `offered_doctors` is stored in `chat_context`. Later messages resolve against that list only:

- `It can be Dr. Reed` or `Dr. Emily is fine` update `selected_doctor_id` / `selected_doctor_name` when exactly one offered doctor matches
- unknown or ambiguous references return clarification without mutating context
- doctor IDs are never exposed in assistant replies

### Offered slots

After a successful availability lookup, `offered_slots` is stored in `chat_context` and `appointment_intake_awaiting` is set to `slot_selection`. Each entry carries internal identifiers (`availability_slot_id`, `start_time`, optional doctor attribution) plus a user-facing `display_time` label.

Later user messages can select a slot by:

- **Reference** — `selected_slot_reference` from CTU (for example `second one`, or an internal reference the interpreter maps from ordinals)
- **Normalized clock time** — `extracted_fields.appointment_time` after time normalization (for example `3PM` → `15:00`)

The backend validates every selection in `_resolve_offered_slot_selection`:

- a `selected_slot_reference` is honored only when it exists in the current `offered_slots` list
- a normalized `appointment_time` selects a slot only when **exactly one** offered slot matches that `HH:MM` display time
- the backend never invents, holds, or books an unoffered time

Slot IDs and raw ISO timestamps are never shown to the user.

### Time normalization

`normalize_appointment_time_expression` (`app/services/appointment_time_normalization.py`) converts user clock-time phrases into internal `HH:MM` (24-hour) format for comparison against offered slot display times.

| User input | Normalized time | Notes |
| --- | --- | --- |
| `3PM` / `3 PM` | `15:00` | AM/PM with optional space and punctuation |
| `2:30 PM` | `14:30` | Minutes supported |
| `11am` | `11:00` | Case-insensitive meridiem |
| `15:00` | `15:00` | Colon 24-hour form |
| `15` | `15:00` | Bare hour only when slot-selection context makes it safe (hours 13–23); bare `3` is not treated as 3 o'clock to preserve ordinal option selection |

CTU should normalize time expressions into `extracted_fields.appointment_time` when possible. `FakeChatTurnUnderstandingInterpreter` follows the same intended CTU contract for reproducible tests and local demos. The orchestrator and receptionist service still validate normalized times against actually offered slots before creating a hold; the backend must not invent or hold an unoffered time.

### State-aware fallback prompts

When CTU returns `fallback` or low-confidence output, `ChatReceptionistService` checks `chat_context` before using the generic fallback reply (`_resolve_contextual_fallback_reply`).

| Active context | Fallback behavior |
| --- | --- |
| `appointment_intake_awaiting = date_or_time_preference`, or specialty/doctor selected without slots | Ask what day or time to check (for example `What day or time works best for Cardiology?`) |
| `offered_slots` present or `appointment_intake_awaiting = slot_selection` | Reprompt to choose one of the offered times |
| `booking_identity_step` active | Ask for the missing identity field for the current step |
| `booking_identity_step = await_final_booking_confirmation` | Ask for explicit final booking confirmation again |
| Confirmed `appointment_id` present (post-booking frame) | Closing phrases such as `no thanks` or `that's all` end politely; new actionable scheduling, cancel, or reschedule requests re-enter normal top-level routing |
| No active task context | Generic fallback |

This keeps scheduling conversations on track without exposing backend internals.

### Supported appointment-intake examples

Exact routing depends on current `chat_context` (whether doctors or slots were already offered, and which `appointment_intake_awaiting` value is set).

**Provider / specialty**

| Example | Typical behavior |
| --- | --- |
| `I'd like to schedule with a dermatologist` | Specialty resolved → earliest availability search or date/time prompt |
| `I want to see a cardiologist` | Specialty resolved from catalog → proceed toward availability |
| `What doctors do you have for cardiology?` | Doctor list for the specialty stored in `offered_doctors` → user picks a doctor next |
| `soonest cardiology appointment` | Specialty + soonest intent → search from clinic today |
| `cardiology next Monday` | Specialty + natural date → availability for that date |
| `Dr. Reed next Monday` | Doctor + natural date → availability for that doctor and date |
| `I want Dr. Emily soonest available` | Doctor + soonest intent → earliest doctor availability search |

**Offered-doctor selection** (after doctor list)

| Example | Typical behavior |
| --- | --- |
| `It can be Dr. Reed` | Resolve against `offered_doctors` → update selected doctor → availability |
| `Dr. Emily is fine` | Same as above for a unique partial match |

**Date and time preference**

| Example | Typical behavior |
| --- | --- |
| `What about Wednesday?` | Contextual weekday follow-up; preserves provider; rechecks availability |
| `tomorrow morning` | Natural date + morning time window |
| `Sunday afternoon` | Natural date + afternoon time window |
| `Wednesday` / `afternoon` | Bare weekday or time window when intake task frame is active |

**Range availability** (provider context required; intake task frame active)

| Example | Typical behavior |
| --- | --- |
| `What days do you have next week?` | Clinic-local week range resolved → availability across range → `offered_slots` grouped by day |
| `What do you have next week?` | Same as above |
| `Any availability next week?` | Same as above |
| `Do you have anything this week?` | `this week` from clinic today through Sunday → grouped availability |

**Offered-slot selection** (after availability results)

| Example | Typical behavior |
| --- | --- |
| `3PM` | Normalize to `15:00` → validate against offered slots → hold flow if unique match |
| `15` | Normalize to `15:00` when unambiguous bare hour is safe in slot context |
| `15:00` | Direct `HH:MM` match against offered slot display times |
| `second one` | Ordinal/reference selection via `selected_slot_reference` when unambiguous |

### Expected behavior

- **Specialty without date** can trigger earliest availability search instead of asking for `YYYY-MM-DD`.
- **Doctor without date** can trigger earliest doctor availability search.
- **Doctor selection from offered doctors** updates `selected_doctor_id` / `selected_doctor_name` and proceeds toward availability.
- **Natural date prompts** should ask conversationally (`What day works best?`) rather than requesting `YYYY-MM-DD`.
- **Offered slots** are stored in `chat_context.offered_slots` and are later used by the existing hold flow.
- **Hold and booking** remain controlled by existing backend flows; CTU and the orchestrator never create holds or book directly.
- **No appointment is booked** until the existing final confirmation flow succeeds (hold → identity → explicit confirmation → `AppointmentBookingService`).
- **Ambiguous CTU output** (low confidence, `ambiguous_fields`, or unsupported intents) returns clarification or state-aware fallback without unsafe context mutation.
- **Conflicting context changes** (for example switching doctors after one was already selected) return clarification instead of silent overwrite.

### Out of scope and follow-ups

| Item | Status |
| --- | --- |
| Written-chat appointment management (lookup, cancel, reschedule) | Implemented — see [Chat Appointment Management](chat-appointment-management.md) |
| Timezone/seed audit for demo availability | Future slice — displayed slots may appear offset when UTC storage and clinic-local presentation are misaligned; not fixed in this branch |
| Second LLM response composer | Not added — replies remain deterministic via `RECEPTIONIST_RESPONSE_MODE=deterministic` by default |
| Retell voice flow | Not modified |
| Public demos with real LLM providers | Require auth, rate limits, and cost controls before exposure |
| Post-booking lifecycle | Post-completion routing supports new booking, lookup, cancel, and reschedule in the same conversation |

See [Chat Appointment Intake Manual Testing](../testing/chat-appointment-intake.md) and [Chat Appointment Management Manual Testing](../testing/chat-appointment-management.md) for reproducible test scenarios.

Appointment management uses a separate task frame (`appointment_management_mode`, `appointment_management_awaiting`). CTU may assist identity extraction during `patient_identity` steps, but listing, selection, confirmation, and execution are backend-owned. See [Chat Appointment Management](chat-appointment-management.md).

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
