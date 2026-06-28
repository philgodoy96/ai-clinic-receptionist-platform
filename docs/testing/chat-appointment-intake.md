# Chat Appointment Intake Manual Testing

Manual test guide for Chat Turn Understanding (CTU) appointment intake and written-chat scheduling reliability.

Architecture and behavior: [Chat Turn Understanding Architecture](../architecture/chat-turn-understanding.md).

Configuration: [Chat turn understanding](../configuration.md).

## Prerequisites

1. Start dependencies:

   ```bash
   docker compose up -d postgres redis rabbitmq
   ```

2. Run migrations and seed demo data:

   ```bash
   python -m alembic upgrade head
   python -m scripts.seed_demo_data
   python -m app.scripts.generate_demo_availability
   ```

3. Choose an interpreter mode in `.env` (see [Interpreter modes](#interpreter-modes)).

4. Start the API:

   ```bash
   python -m uvicorn app.main:app --reload
   ```

5. Send messages to `POST /api/v1/chat/messages` with the same `conversation_id` across turns.

   ```json
   {
     "message": "I'd like to schedule with a dermatologist",
     "conversation_id": null
   }
   ```

## Core principle

```text
The LLM understands. The backend validates and decides. Domain services execute.
```

- **CTU** extracts candidate appointment meaning (specialty, doctor, date, time window, soonest intent, slot reference, availability-range phrasing).
- **Backend** validates specialty, doctor, date, time, offered slot reference, availability range, and soonest intent against catalogs and parsers.
- **Scheduling services** decide actual availability and return candidate slots.
- **Hold and booking** remain controlled by existing backend flows.
- **No appointment is booked** without explicit final confirmation after identity collection.
- **Assistant replies** must not expose internal IDs, slot IDs, hold IDs, UUIDs, raw timestamps, or backend details.

The interpreter never books, holds, cancels, or reschedules directly.

## Appointment task frame

During active scheduling, `chat_context.appointment_intake_awaiting` records what the assistant is waiting for:

| Value | When set | User should provide |
| --- | --- | --- |
| `date_or_time_preference` | Provider selected but no date, or no slots found | A day, weekday, or time-of-day preference |
| `slot_selection` | Availability returned offered slots | One of the offered times |

This prevents generic fallback during scheduling and enables contextual follow-ups:

- **`What about Wednesday?`** — backend keeps `selected_doctor_id` / `selected_specialty_id`, resolves the weekday against clinic-local today, clears stale `offered_slots` when the date changes, and rechecks availability.
- **`afternoon`** — backend applies a time-of-day window via `TimePreferenceParser` when provider context exists, then rechecks availability.
- **`What days do you have next week?`** — backend keeps provider context, resolves the clinic-local week range, searches availability across the range, stores `offered_slots`, and asks the user to choose a time.

## Supported appointment-intake behaviors

High-level expected behavior by message group. Exact routing depends on current `chat_context` (offered doctors/slots and `appointment_intake_awaiting`).

### Provider / specialty

| Example | Expected behavior |
| --- | --- |
| `I'd like to schedule with a dermatologist` | Dermatology resolved → earliest search or date/time prompt |
| `I want to see a cardiologist` | Cardiology resolved from catalog |
| `What doctors do you have for cardiology?` | Doctor list stored in `offered_doctors` |
| `It can be Dr. Reed` | Unique match against `offered_doctors` → selected doctor updated |
| `Dr. Emily is fine` | Same for a unique partial match |

### Date / time

| Example | Expected behavior |
| --- | --- |
| `cardiology next Monday` | Specialty + natural date → availability for that Monday |
| `Dr. Reed next Monday` | Doctor + natural date |
| `I want Dr. Emily soonest available` | Doctor + soonest intent → earliest doctor search |
| `What about Wednesday?` | Contextual weekday follow-up; provider preserved |
| `tomorrow morning` | Natural date + morning window |
| `Sunday afternoon` | Natural date + afternoon window |
| `Tuesday 15` / `Tuesday at 14` | Contextual weekday + bare hour → `15:00` / `14:00` when slot context makes the hour unambiguous |

### Offered slot revision (before hold)

| Example | Expected behavior |
| --- | --- |
| `Actually, Wednesday` | Revises date preference; rechecks availability — does not start identity intake |
| `On second thought, I'd like for Wednesday` | Scheduling clarification or revised search — not identity collection |

### Offered slot selection

| Example | Expected behavior |
| --- | --- |
| `3PM` / `3 PM` | Normalize to `15:00` → validate against offered slots |
| `15` | Normalize to `15:00` when slot context makes bare hour 13–23 safe |
| `15:00` | Direct `HH:MM` match |
| `second one` | Ordinal/reference via `selected_slot_reference` when unambiguous |

### Range availability

| Example | Expected behavior |
| --- | --- |
| `What days do you have next week?` | Week range search; grouped day/time options |
| `What do you have next week?` | Same |
| `Any availability next week?` | Same |
| `Do you have anything this week?` | `this week` from clinic today through Sunday |

## Offered doctors and offered slots

- **`offered_doctors`** — stored when the assistant lists doctors for a specialty. Later messages like `It can be Dr. Reed` resolve against this list only.
- **`offered_slots`** — stored after availability results. Later messages like `3PM`, `15`, `15:00`, or `second one` resolve against offered slots only.
- **IDs are never exposed** — replies must not contain doctor IDs, slot IDs, hold IDs, UUIDs, or raw ISO timestamps.
- **Backend validation** — every selected slot reference or normalized time is validated against actually offered slots. The backend must never hold or book an unoffered time.
- **Identity-only openers** — a bare full name at conversation start does not start booking identity without scheduling context.
- **Revision/denial phrases** — phrases such as `On second thought` or `Actually, Wednesday` are not parsed as patient names.

## Time normalization

Supported user clock-time formats (normalized to `HH:MM` before slot matching):

| Input | Normalized |
| --- | --- |
| `3PM` / `3 PM` | `15:00` |
| `2:30 PM` | `14:30` |
| `11am` | `11:00` |
| `15` | `15:00` (only when slot context makes bare hour 13–23 unambiguous) |
| `15:00` | `15:00` |

CTU should return normalized times in `extracted_fields.appointment_time`. The fake interpreter follows the same intended CTU contract for reproducible tests. The backend still validates against actually offered slots and must not hold or book an unoffered time. Ambiguous values such as bare `3` preserve existing option/reference behavior rather than being guessed as a time.

## Availability range follow-ups

Supported range labels:

- **`this week`** — starts from clinic today (does not search past dates) through the current clinic-local Sunday.
- **`next week`** — next clinic-local Monday through Sunday.

Range handling is context-gated: it runs only when appointment intake is active and provider context (`selected_doctor_id` or `selected_specialty_id`) exists. Dates come from `ClinicTimeService`. The backend returns real availability grouped or summarized by day, stores `offered_slots`, and sets `appointment_intake_awaiting = slot_selection`.

**Out of scope for range handling:** next month, early next week, end of month, recurring weekdays, complex exclusions.

## State-aware fallback prompts

Generic fallback is used only when there is no active task context. Otherwise reprompts are contextual:

| Context | Expected reprompt |
| --- | --- |
| Waiting for date/time (`date_or_time_preference`) | Ask what day or time to check |
| Waiting for slot selection (`slot_selection` or `offered_slots` present) | Ask user to choose one of the offered times |
| Booking identity step active | Ask for the missing identity field |
| Awaiting final booking confirmation | Ask for explicit confirmation again |
| After booking confirmed (`appointment_id` set) | Closing phrases (`no thanks`, `that's all`, etc.) end politely; new actionable requests enter normal top-level routing |

## Interpreter modes

| Mode | Use case |
| --- | --- |
| `disabled` | Baseline — deterministic fallback without CTU appointment intake |
| `fake` | Reproducible local/manual testing without provider calls (**recommended**) |
| `groq` | Controlled local testing with a real Groq model and structured JSON output |

### Fake mode

```env
CHAT_TURN_UNDERSTANDING_INTERPRETER=fake
```

Recommended for repeatable manual runs. The fake interpreter is scenario-driven and does not call external providers.

### Groq mode

```env
CHAT_TURN_UNDERSTANDING_INTERPRETER=groq
GROQ_API_KEY=gsk_...
GROQ_MODEL=llama-3.3-70b-versatile
GROQ_RESPONSE_FORMAT=json_schema
```

Use only for controlled local testing.

**Warning:** Do not expose public Groq-powered demos without authentication, rate limits, and cost controls.

`GROQ_API_KEY`, `GROQ_MODEL`, and `GROQ_RESPONSE_FORMAT=json_schema` are required independently of `LLM_PRIMARY_PROVIDER`.

## Manual test checklist

### Fake mode

Set `CHAT_TURN_UNDERSTANDING_INTERPRETER=fake`, restart the API, and run the sequence below. Reuse `conversation_id` within each numbered block; start a **new conversation** where noted.

| Step | Message | Expected behavior |
| --- | --- | --- |
| 1 | `I'd like to schedule with a dermatologist` | Assistant mentions Dr. Emily Carter / Dermatology and asks what day or time works best |
| 2 | `Wednesday` | Assistant checks Wednesday availability using active provider context |
| 3 | `What about afternoon?` | Assistant keeps context and applies/rechecks the afternoon preference |
| 4 | *(new conversation)* `cardiology next Monday` | Assistant returns real cardiology availability for that Monday |
| 5 | `What about Wednesday?` | Assistant changes search date, replaces stale offered slots, and does not generic fallback |
| 6 | `What days do you have next week?` | Assistant searches the week range and returns grouped day/time options |
| 7 | `It can be Dr. Reed` | Assistant resolves Dr. Michael Reed from offered doctors when a doctor list was shown |
| 8 | *(after offered slots appear)* `3PM` | Assistant selects the offered `15:00` slot when present |
| 9 | *(after offered slots appear)* `15` | Assistant selects the offered `15:00` slot when present |
| 10 | Unclear message during slot selection: `banana` | Assistant asks user to choose one of the offered times — not generic fallback |
| 11 | Proceed through booking identity | Final booking still requires explicit confirmation |
| 12 | After booking confirmed: `no thanks` | Assistant closes politely |

**Also verify:** `CHAT_TURN_UNDERSTANDING_INTERPRETER=disabled` still reaches availability via deterministic parsing (for example `cardiology next Monday`).

### Groq mode

1. Set Groq env vars (see [Groq mode](#groq-mode)) and restart the API.
2. Repeat the [Fake mode](#fake-mode) checklist steps 1–12.
3. Explicitly confirm for every reply:
   - no IDs are exposed
   - no raw backend details are exposed
   - no unoffered times are selected
   - booking still requires explicit final confirmation

## Safety boundaries

- **Cancel / reschedule / emergency** messages route through top-level deterministic flows, not appointment intake CTU.
- **Active hold + booking identity** turns do not invoke appointment intake CTU.
- **Slot selection after offered slots** reaches hold creation through the existing flow only after backend validation passes.
- **No booking** occurs without explicit confirmation after identity collection.

## Out of scope for this script

- **Appointment management flows** — booking, lookup, cancel, and reschedule: [Chat Appointment Management Manual Testing](chat-appointment-management.md)
- **No second LLM response composer** — reply phrasing remains deterministic by default
- **Retell voice flow** — separate provider-orchestrated path
- **Public demos with real LLM providers** — require auth, rate limits, and cost controls
- **Clinical advice, moderation, and live human agents** — outside written-chat demo scope (see architecture doc)

## Automated coverage

Related automated tests:

- `tests/test_chat_appointment_intake.py` — orchestrator validation, contextual follow-ups, slot reference validation, time normalization integration
- `tests/test_chat_appointment_intake_runtime.py` — `ChatReceptionistService` wiring, state-aware fallback, reply safety
- `tests/test_appointment_time_normalization.py` — clock-time normalization unit tests
- `tests/test_chat_receptionist_service.py` — end-to-end receptionist behavior including `appointment_intake_awaiting`
- `tests/test_post_booking_turn.py` — post-booking turn classification
- `tests/test_chat_booking_identity_orchestration.py` — post-booking closing and new-request routing
