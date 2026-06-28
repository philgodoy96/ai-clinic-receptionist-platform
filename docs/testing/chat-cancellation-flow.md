# Chat Cancellation Flow Manual Testing

Manual test guide for the contextual cancellation task frame on branch `feat/contextual-appointment-management-cancel-flow`.

Architecture and behavior: [Chat Appointment Management](../architecture/chat-appointment-management.md).

End-to-end QA (book → lookup → reschedule → cancel): [Chat Appointment Management Manual Testing](chat-appointment-management.md).

Configuration: [configuration.md](../configuration.md).

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

3. Use deterministic/local mode for reproducible runs. Cancellation identity intake may optionally use CTU when configured; the orchestrator also accepts deterministic field parsing. For intake-style CTU testing, see [Chat Appointment Intake Manual Testing](chat-appointment-intake.md).

4. Start the API:

   ```bash
   python -m uvicorn app.main:app --reload
   ```

5. Send messages to `POST /api/v1/chat/messages` with the same `conversation_id` across turns in each scenario.

   ```json
   {
     "message": "I want to cancel my appointment",
     "conversation_id": null
   }
   ```

## Core principle

```text
The LLM understands. The backend validates and decides. Domain services execute.
```

- **Backend** owns task-frame state, patient resolution, offered appointments, selection validation, and confirmation gates.
- **Cancellation executes** only through `AppointmentCancellationService` after explicit confirmation.
- **Assistant replies** must not expose internal IDs, UUIDs, or raw backend details.

## Manual test checklist

Reuse `conversation_id` within each numbered block. Start a **new conversation** where noted.

| # | Step | Message / action | Expected behavior |
| --- | --- | --- | --- |
| 1 | Entry | `I want to cancel my appointment` | Assistant asks for patient's full name and date of birth |
| 2 | Incomplete identity | Partial name or DOB only | Reprompts for full name and DOB |
| 3 | Valid identity, one appointment | Provide matching demo patient name + DOB with a single upcoming appointment | Asks for cancellation confirmation for that appointment (no numbered list) |
| 4 | Valid identity, multiple appointments | Use a demo patient with two or more upcoming cancelable appointments | Lists numbered appointments and asks which to cancel |
| 5 | Selection — ordinal | `the first one` | Selects the first offered appointment; asks for explicit confirmation |
| 6 | Ambiguous confirmation | `maybe` | Does **not** cancel; reprompts for confirmation |
| 7 | Reject confirmation | `no, don't cancel it` | Does **not** call cancellation service; declines politely |
| 8 | Confirm cancellation | *(new conversation through steps 1–5)* then `yes, cancel it` | Cancels only now; success message includes `Is there anything else I can help with?` |
| 9 | Post-cancel close | `no thanks` | Closes politely (`You're all set. Have a great day!`) |
| 10 | Post-cancel needs help | *(after successful cancel)* `yes` | Asks whether user wants to schedule, cancel, or reschedule |
| 11 | Post-cancel new schedule | *(after successful cancel)* `I want to schedule an appointment` | Allows normal scheduling routing (appointment intake) |
| 12 | Reply safety | Review all assistant replies in the run | No IDs, UUIDs, or raw timestamps exposed |
| 13 | JSON display | Inspect Postman/API JSON responses | `\n` in message content is normal JSON escaping for multi-line lists, not a backend bug |

### Additional selection signals (optional)

When multiple appointments are offered, also verify:

| Message | Expected |
| --- | --- |
| `1` or `number 1` | Selects first offered row when unambiguous |
| `the dermatology one` | Selects by specialty when unique among offered rows |
| `Dr. Emily` | Selects by doctor when unique among offered rows |
| `Wednesday` | Selects by weekday when unique among offered rows |
| `10 AM` | Selects by normalized time when unique among offered rows |

Each successful selection should move to explicit confirmation, not immediate cancellation.

## Safety checks

- Selecting an appointment never cancels it without a separate confirmation turn.
- Rejecting confirmation never calls `AppointmentCancellationService`.
- Appointments not in the offered list cannot be selected.
- Duplicate `yes, cancel it` in the same conversation should not produce duplicate cancellation side effects (idempotency key `chat-cancel:{conversation_id}:{appointment_id}`).

## Out of scope / follow-ups

- **Rescheduling** — implemented in written chat; see [Chat Appointment Management Manual Testing](chat-appointment-management.md).
- **Retell voice flow** — not modified.
- **Timezone/seed audit** — future slice; demo slot times may appear offset if UTC storage and clinic-local presentation are misaligned.
- **Public demo with real LLM providers** — requires auth, rate limits, and cost controls.
- **Frontend rendering** — clients may need to render `\n` in message text; API JSON escaping is expected.

## Automated coverage

Related tests (run via `python -m pytest tests -q`):

- `tests/test_chat_appointment_cancellation.py`
- `tests/test_post_cancellation_turn.py`
- `tests/test_chat_receptionist_service.py`
