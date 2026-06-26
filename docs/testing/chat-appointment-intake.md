# Chat Appointment Intake Manual Testing

Manual test guide for Chat Turn Understanding (CTU) appointment intake on branch `feat/chat-turn-understanding-appointment-intake`.

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

- **CTU** extracts candidate appointment meaning (specialty, doctor, date, time window, soonest intent, slot reference).
- **Backend** validates specialty, doctor, date, time, offered slot reference, and soonest intent against catalogs and parsers.
- **Scheduling services** decide actual availability and return candidate slots.
- **Hold and booking** remain controlled by existing backend flows.
- **No appointment is booked** without explicit final confirmation after identity collection.

The interpreter never books, holds, cancels, or reschedules directly.

## Appointment task frame

During active scheduling, `chat_context.appointment_intake_awaiting` records what the assistant is waiting for:

| Value | When set | User should provide |
| --- | --- | --- |
| `date_or_time_preference` | Provider selected but no date, or no slots found | A day, weekday, or time-of-day preference |
| `slot_selection` | Availability returned offered slots | One of the offered times |

This prevents generic fallback during scheduling and enables contextual follow-ups such as `What about Wednesday?` or `afternoon` while preserving `selected_doctor_id` / `selected_specialty_id`.

## Offered doctors and offered slots

- **`offered_doctors`** — stored when the assistant lists doctors for a specialty. Later messages like `It can be Dr. Reed` resolve against this list only.
- **`offered_slots`** — stored after availability results. Later messages like `3PM`, `15`, `15:00`, or `second one` resolve against offered slots only.
- **IDs are never exposed** — replies must not contain doctor IDs, slot IDs, hold IDs, or raw ISO timestamps.

## Time normalization

Supported user clock-time formats (normalized to `HH:MM` before slot matching):

| Input | Normalized |
| --- | --- |
| `3PM` / `3 PM` | `15:00` |
| `2:30 PM` | `14:30` |
| `11am` | `11:00` |
| `15` | `15:00` (only when slot context makes bare hour 13–23 unambiguous) |
| `15:00` | `15:00` |

CTU should return normalized times in `extracted_fields.appointment_time`. The backend still validates against actually offered slots and must not hold or book an unoffered time.

## State-aware fallback prompts

Generic fallback is used only when there is no active task context. Otherwise reprompts are contextual:

| Context | Expected reprompt |
| --- | --- |
| Waiting for date/time (`date_or_time_preference`) | Ask what day or time to check |
| Waiting for slot selection (`slot_selection` or `offered_slots` present) | Ask user to choose one of the offered times |
| Booking identity step active | Ask for the missing identity field |
| Awaiting final booking confirmation | Ask for explicit confirmation again |

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

## Fake-mode checklist

Set `CHAT_TURN_UNDERSTANDING_INTERPRETER=fake`, restart the API, and run this sequence in a **single conversation** (reuse `conversation_id`). After step 4, start a **new conversation** for steps 5–10 so doctor-list context is fresh.

| Step | Message | Expected behavior |
| --- | --- | --- |
| 1 | `I'd like to schedule with a dermatologist` | Dermatology selected; earliest availability search; offered times or natural no-slots guidance; no `YYYY-MM-DD` prompt |
| 2 | `Wednesday` | Bare weekday resolved in active intake context; availability rechecked for Wednesday |
| 3 | `What about afternoon?` | Afternoon time window applied; provider preserved; availability rechecked |
| 4 | *(new conversation)* `cardiology next Monday` | Cardiology + natural date; availability lookup for that Monday |
| 5 | `What about Wednesday?` | Contextual weekday follow-up; specialty preserved; date updated; stale slots cleared |
| 6 | `It can be Dr. Reed` | Doctor resolved from `offered_doctors` when list was shown; availability proceeds |
| 7 | *(after offered slots appear)* `3PM` | Time normalized to `15:00`; unique offered slot selected; hold flow begins when match is valid |
| 8 | *(after offered slots appear)* `15` | Bare hour normalized to `15:00` when unambiguous; same validation as step 7 |
| 9 | Unclear message during slot selection (for example `maybe later`) | Slot-selection reprompt — not generic fallback |
| 10 | Complete booking identity through hold | Final booking requires explicit confirmation; no `booking_confirmed: true` until confirmation step succeeds |

**Also verify:** `CHAT_TURN_UNDERSTANDING_INTERPRETER=disabled` still reaches availability via deterministic parsing (for example `cardiology next Monday`).

## Groq-mode checklist

1. Set Groq env vars (see [Groq mode](#groq-mode)) and restart the API.
2. Repeat the [Fake-mode checklist](#fake-mode-checklist) steps 1–10.
3. Explicitly confirm for every reply:
   - no UUIDs or internal IDs exposed
   - no `availability_slot_id`, `hold_id`, or `doctor_id` in user-visible text
   - no raw ISO timestamps such as `2026-07-03T14:00:00+00:00`
   - no backend error details or provider diagnostics
   - no unoffered times selected or held
   - booking still requires explicit final confirmation after identity collection
4. Confirm ambiguous or low-confidence Groq output falls back safely (clarification or state-aware reprompt) without unsafe context mutation.

## Additional spot-check examples

| Message | Expected behavior |
| --- | --- |
| `soonest cardiology appointment` | Cardiology + soonest search from clinic today |
| `Dr. Reed next Monday` | Doctor + natural date |
| `I want Dr. Emily soonest available` | Doctor + soonest intent |
| `Dr. Emily is fine` (after dermatology doctor list) | Contextual offered-doctor selection |
| `tomorrow morning` (provider already in context) | Natural date + morning window |
| `Sunday afternoon` (provider already in context) | Natural date + afternoon window |
| `15:00` (after offered slots) | Direct `HH:MM` match against offered display times |

## Safety boundaries

- **Cancel / reschedule / emergency** messages route through top-level deterministic flows, not appointment intake CTU.
- **Active hold + booking identity** turns do not invoke appointment intake CTU.
- **Slot selection after offered slots** reaches hold creation through the existing flow only after backend validation passes.
- **No booking** occurs without explicit confirmation after identity collection.

## Out of scope / follow-ups

- **Cancel/reschedule contextual task frame** — future slice; current cancel/reschedule paths unchanged.
- **Timezone/seed audit** — future slice; demo availability display may be offset if UTC storage and clinic-local time are not aligned. Do not treat as fixed in this branch.
- **No second LLM response composer** — reply phrasing remains deterministic by default.
- **Retell voice flow** — not modified.
- **Public demos with real LLM providers** — require auth, rate limits, and cost controls.

## Automated coverage

Related automated tests:

- `tests/test_chat_appointment_intake.py` — orchestrator validation, contextual follow-ups, slot reference validation, time normalization integration
- `tests/test_chat_appointment_intake_runtime.py` — `ChatReceptionistService` wiring, state-aware fallback, reply safety
- `tests/test_appointment_time_normalization.py` — clock-time normalization unit tests
- `tests/test_chat_receptionist_service.py` — end-to-end receptionist behavior including `appointment_intake_awaiting`
