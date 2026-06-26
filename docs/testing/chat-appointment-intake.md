# Chat Appointment Intake Manual Testing

Manual test guide for Chat Turn Understanding (CTU) appointment intake.

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

3. Choose an interpreter mode in `.env` (see sections below).

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

The interpreter extracts candidate meaning. The backend validates specialty, doctor, date, time, and soonest intent. Scheduling services decide availability. The LLM never books, holds, cancels, or reschedules directly.

## Interpreter modes

| Mode | Use case |
| --- | --- |
| `disabled` | Baseline — preserves deterministic fallback behavior without CTU appointment intake |
| `fake` | Reproducible local/demo testing without provider calls |
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

Use only for controlled local testing. Do not expose public Groq-powered demos without authentication, rate limits, and cost controls.

## Shared scenario checklist

Run these scenarios in both `fake` and `groq` modes when validating a change.

For each message, confirm:

- the assistant reply is conversational and does **not** ask for `YYYY-MM-DD`
- replies do **not** expose doctor IDs, slot IDs, hold IDs, backend internals, or raw timestamps
- `offered_slots` appears in conversation metadata after successful availability lookup
- no `appointment_id` or `booking_confirmed: true` until the full hold → identity → confirmation flow completes

| Step | Message | Expected behavior |
| --- | --- | --- |
| 1 | `I'd like to schedule with a dermatologist` | Specialty resolved; earliest availability search runs; offered times returned or natural no-slots guidance |
| 2 | `soonest cardiology appointment` | Cardiology selected; soonest search from clinic today; availability results or natural no-slots guidance |
| 3 | `cardiology next Monday` | Specialty + natural date applied; availability lookup for that date |
| 4 | `Dr. Reed next Monday` | Doctor + natural date applied; availability lookup |
| 5a | `cardiology` | Doctor list for cardiology; `offered_doctors` stored in context |
| 5b | `It can be Dr. Reed` | `selected_doctor_id` / `selected_doctor_name` updated from offered doctors; availability proceeds without re-asking for specialty |

### Additional examples worth spot-checking

| Message | Expected behavior |
| --- | --- |
| `I want Dr. Emily soonest available` | Doctor + soonest intent; earliest doctor availability search |
| `Dr. Emily is fine` (after dermatology doctor list) | Contextual offered-doctor selection for Dr. Emily Carter |
| `tomorrow morning` (with doctor/specialty already in context) | Natural date + morning time window applied |
| `Sunday afternoon` (with doctor/specialty already in context) | Natural date + afternoon time window applied |

## Fake-mode checklist

1. Set `CHAT_TURN_UNDERSTANDING_INTERPRETER=fake` and restart the API.
2. Run the [shared scenario checklist](#shared-scenario-checklist).
3. Confirm `disabled` baseline still works by switching to `CHAT_TURN_UNDERSTANDING_INTERPRETER=disabled`, restarting, and sending `cardiology next Monday` — deterministic parsing should still reach availability without CTU.

## Groq-mode checklist

1. Set:

   ```env
   CHAT_TURN_UNDERSTANDING_INTERPRETER=groq
   GROQ_API_KEY=...
   GROQ_MODEL=...
   GROQ_RESPONSE_FORMAT=json_schema
   ```

2. Restart the API.
3. Repeat the [shared scenario checklist](#shared-scenario-checklist).
4. Explicitly verify replies contain **no**:
   - UUIDs or internal IDs
   - `availability_slot_id`, `hold_id`, or `doctor_id` values
   - raw ISO timestamps such as `2026-07-03T14:00:00+00:00`
   - backend error details or provider diagnostics
5. Confirm ambiguous or low-confidence Groq output falls back safely (clarification or deterministic continuation) without unsafe context mutation.

## Safety boundaries to verify

- **Cancel / reschedule / emergency** messages still route through top-level deterministic flows, not appointment intake CTU.
- **Active hold + booking identity** turns do not invoke appointment intake CTU.
- **Slot selection after offered slots** still reaches hold creation through the existing flow.
- **No booking** occurs without explicit confirmation after identity collection.

## Known follow-up

There is a known follow-up to audit demo availability timezone/seed behavior. Displayed availability may appear several hours later than expected when UTC storage and clinic-local presentation are not aligned. Do not treat this as fixed in the appointment-intake slice.

## Automated coverage

Related automated tests:

- `tests/test_chat_appointment_intake.py` — orchestrator validation and search-criteria behavior
- `tests/test_chat_appointment_intake_runtime.py` — `ChatReceptionistService` wiring and reply safety
