# Chat Appointment Management Manual Testing

Manual QA script for written-chat contextual appointment management and reliability guards.

Architecture: [Chat Appointment Management](../architecture/chat-appointment-management.md).

Focused checklists:

- [Chat Appointment Intake Manual Testing](chat-appointment-intake.md) — booking intake detail
- [Chat Cancellation Flow Manual Testing](chat-cancellation-flow.md) — cancellation detail

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

3. Use reproducible local mode in `.env`:

   ```env
   LLM_PROVIDER=fake
   EMAIL_PROVIDER=fake
   CHAT_TURN_UNDERSTANDING_INTERPRETER=fake
   RETELL_ENABLED=false
   ```

4. Start the API:

   ```bash
   python -m uvicorn app.main:app --reload
   ```

5. Send messages to `POST /api/v1/chat/messages`. Reuse `conversation_id` within each scenario.

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

## Demo seed patients

Use seeded patients from `python -m scripts.seed_demo_data`:

| Name | Date of birth | Email |
| --- | --- | --- |
| John Miller | 1985-04-12 | `john.miller@example.test` |
| Ava Thompson | 1992-09-03 | `ava.thompson@example.test` |

Slash-form DOB such as `1985/04/12` is accepted. Do not rely on names that are not in seed data (for example John Smith or Felipe Marques) unless you create a patient during the session.

## Written-chat reliability QA

Quick checks for routing and confirmation boundaries. Start a **new conversation** for each row unless noted.

| Check | Steps | Expected outcome |
| --- | --- | --- |
| Orphan booking confirmation | Fresh conversation → send `Yes` | No booking confirmation; no appointment or email job created |
| Contextual bare hour | Start booking → reach slot selection → send `Tuesday 15` when Tuesday slots are offered | Understood as `15:00` on Tuesday when unambiguous |
| Pre-hold revision | During intake before a hold → send `On second thought, I'd like for Wednesday` | Scheduling clarification or revised availability — **not** identity intake |
| Booking final decline | Complete hold + identity → at final confirmation send `No` | Booking aborted; hold released or cleared; no appointment; no confirmation email job |
| Reschedule slot decline | In reschedule new-slot selection → send `No` | No reschedule; original appointment unchanged |
| Human handoff priority | Mid-booking or mid-lookup → send `I need to speak to a person` | Handoff/escalation reply takes priority over the active flow |

## End-to-end demo script

Run these steps in order using one conversation where noted. Use fictional demo patient data only.

| Step | Message | Expected outcome |
| --- | --- | --- |
| 1 | `I'd like to schedule with a dermatologist` | Assistant asks for day/time; mentions dermatology |
| 2 | `Wednesday` then pick an offered time (`10`, `10h`, or `2pm` when offered) | Hold created; identity collection begins |
| 3 | Provide name + DOB (partial fields OK — assistant asks only for missing field) | Identity progresses |
| 4 | If DOB is ambiguous (`01/02/2000`), clarify format | Assistant asks MM/DD vs DD/MM before resolving |
| 5 | Provide email when asked | Email accepted without redundant confirmation step |
| 6 | Confirm booking (`yes`, `please book it`) | `booking_confirmed`; no internal IDs in reply |
| 6b | *(alternate)* At final confirmation send `No` | Booking aborted; no appointment or email job |
| 7 | `show my appointments` | Lists the new appointment in clinic-local time |
| 8 | `reschedule my appointment` | Lists appointment or asks which; guides to new slot |
| 9 | Pick a new offered time and confirm reschedule | Success message; original is superseded |
| 10 | `check my appointments` | Shows only the active rescheduled appointment |
| 11 | `cancel my appointment` | Confirmation gate before cancellation |
| 12 | `yes, cancel it` | Cancellation success; offers further help |
| 13 | `I want to book another appointment` | New booking intake starts in same conversation |

## Focused scenarios

### Single-slot yes/no hold

1. Reach availability with exactly one offered slot.
2. When assistant asks whether to hold that time, reply `yes` or `sure`.
3. **Expect:** hold created — not generic fallback.

### Multiple slots — no silent hold

1. Reach availability with two or more offered slots.
2. Reply `yes` without selecting a time.
3. **Expect:** reprompt to choose a specific time — no hold created.

### Partial identity memory

1. Start cancel or lookup: `check my appointments`.
2. Provide only full name.
3. **Expect:** assistant asks only for DOB (not full identity again).

### Resolved patient reuse

1. Complete identity in a lookup or cancel flow.
2. In the same conversation, say `reschedule my appointment`.
3. **Expect:** skips full identity re-entry when patient is already resolved.

### Selection and revision

1. Reach offered slots or offered appointments with multiple choices.
2. Select with `The one on Monday`, `The one at 14`, or an ordinal.
3. Revise with `On second thought, I want the one at 14` or `Actually, Wednesday` before final confirmation.
4. **Expect:** selection updates; no booking, cancel, or reschedule commit until explicit confirmation.

### Booking final decline

1. Complete hold and identity through to `booking_confirmation_required`.
2. Reply `No`.
3. **Expect:** polite abort; hold cleared or released; no `booking_confirmed`; no confirmation email job.

### Reschedule new-slot decline

1. Start reschedule for a seeded patient with an upcoming appointment.
2. Reach new-slot selection and reply `No`.
3. **Expect:** no reschedule; original appointment still listed on lookup.

### Post-completion new intent

1. Complete a cancellation.
2. Say `I'd like to schedule with a cardiologist`.
3. **Expect:** normal booking intake routing — not stuck in completed frame.

## Safety checks

- No UUIDs, hold IDs, or raw ISO timestamps in assistant replies.
- Cancellation and reschedule require explicit confirmation after selection.
- `RESCHEDULED` appointments do not appear in lookup or cancel lists.
- Booking never succeeds without a valid hold (or successful hold refresh at confirmation).

## Out of scope for this script

These are intentionally outside the written-chat demo scope (see architecture doc):

- Retell voice flows — test separately via Retell smoke docs
- Real Groq/Resend/Retell keys — optional; not required for this script
- Third-party patient management
- Patient profile updates (email, address, insurance, medical records)
- Clinical advice, emergency triage, unsafe/abusive message moderation
- Live human agent connection (escalation creates internal records only)

## Automated coverage

Run via `python -m pytest tests -q` when validating locally:

- `tests/test_chat_appointment_lookup.py`
- `tests/test_chat_appointment_rescheduling.py`
- `tests/test_chat_appointment_cancellation.py`
- `tests/test_chat_booking_identity_orchestration.py`
- `tests/test_chat_receptionist_service.py`
