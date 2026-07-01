# Chat Appointment Management — Contextual Written-Chat Flows

Written chat supports end-to-end **appointment management** in a single conversation: booking, scheduled appointment lookup, rescheduling, and cancellation. These flows use a shared **appointment-management task frame** alongside the separate **appointment intake** frame used for new scheduling.

The written chat is a **deterministic simulator** with state guards around booking, cancellation, rescheduling, lookup, and human escalation. Routing, confirmation gates, and domain execution are backend-controlled. The backend validates conversation state before holds, bookings, cancellations, reschedules, or email jobs run. No destructive domain action occurs until explicit confirmation where required.

Manual testing: [Chat Appointment Management Manual Testing](../testing/chat-appointment-management.md).

Related:

- [Chat Turn Understanding Architecture](chat-turn-understanding.md) — CTU contract and appointment intake task frame
- [Chat Appointment Intake Manual Testing](../testing/chat-appointment-intake.md) — booking intake and slot selection
- [Chat Cancellation Flow Manual Testing](../testing/chat-cancellation-flow.md) — cancellation-focused checklist
- [Appointment Rescheduling Foundation](appointment-rescheduling-foundation.md) — shared `AppointmentReschedulingService`
- [Retell Voice Appointment Cancellation](retell-voice-cancellation.md) — voice channel uses the same cancellation domain service
- [Retell Voice Appointment Rescheduling](retell-voice-rescheduling.md) — voice channel uses the same rescheduling domain service
- [Appointment Slot Holds](appointment-holds.md) — Redis hold model and TTL policy

Retell/voice orchestration is separate from written chat. Voice remains provider-driven through Retell tool callbacks.

## Feature overview

The written-chat receptionist (`POST /api/v1/chat/messages`) supports these **demo flows**:

| Flow | Example user intent | Backend outcome |
| --- | --- | --- |
| **Schedule appointment** | `I'd like to schedule with a dermatologist` | Availability → hold → identity → confirmation → appointment + confirmation email job when applicable |
| **Reschedule appointment** | `reschedule my appointment`, `move my appointment to Wednesday` | Identity → appointment selection → new slot → confirmation → successor appointment + email job when applicable |
| **Cancel appointment** | `I want to cancel my appointment` | Identity → selection → confirmation → cancelled appointment |
| **Lookup appointments** | `show my appointments`, `what appointments do I have?` | Identity (if needed) → upcoming scheduled list; follow-up cancel/reschedule supported |
| **Human handoff request** | `I need to speak to a person` | Handoff priority over active flows; internal escalation record and notification job — no live human in the demo |
| **Continue after completion** | New intent after a flow completes | Post-completion routing starts the next flow in the same conversation |

All of the above are **written-chat** functionality. The backend orchestrates conversation state, validation, and domain execution. Retell voice uses a separate provider-orchestrated path documented under `docs/operations/retell-*` and `docs/architecture/retell-*`.

## Core principle

```text
The LLM understands. The backend validates and decides. Domain services execute.
```

In practice:

- **Turn understanding / LLM layer** — identifies user intent and extracts normalized candidates (date, time, specialty, doctor, patient name, DOB, email, slot references, yes/no answers).
- **Conversation orchestration layer** — maintains `chat_context`, preserves resolved patient context, tracks offered slots and appointments, aligns prompts with expected response types, and routes booking / lookup / cancel / reschedule intents.
- **Domain service layer** — validates patient ownership, appointment status transitions, and holds; executes booking, cancellation, and reschedule; records audit events and email jobs.
- **Persistence / infrastructure** — PostgreSQL for durable records; Redis for temporary appointment holds; RabbitMQ for email job wake-up messages; fake providers for local demo mode.

The interpreter and orchestrator never book, cancel, or reschedule appointments directly. Assistant replies must not expose internal IDs, UUIDs, raw timestamps, or other backend implementation details.

## Architecture overview

Written chat uses two parallel task frames:

| Frame | Marker | Purpose |
| --- | --- | --- |
| **Appointment intake** | `appointment_intake_awaiting` | New booking: specialty/doctor, date/time, slot selection, hold, identity, confirmation |
| **Appointment management** | `appointment_management_mode` + `appointment_management_awaiting` | Lookup, cancel, reschedule for an existing patient |

High-level layer responsibilities:

### Turn understanding / LLM layer

- Identifies intents such as scheduling, cancellation, reschedule, and appointment lookup.
- Extracts normalized fields from natural language (`It could be at 10`, `Monday 2pm`, `check my appointments`).
- Optional via `CHAT_TURN_UNDERSTANDING_INTERPRETER` (`disabled`, `fake`, `groq`). When disabled, deterministic parsers still handle many flows.

### Conversation orchestration layer

Primary module: `app/services/chat_receptionist.py`.

Specialized orchestrators:

| Module | Role |
| --- | --- |
| `app/services/chat_appointment_intake.py` | Specialty, doctor, date/time, slot selection for new bookings |
| `app/services/chat_booking_identity.py` | Patient identity collection, partial memory, DOB ambiguity, email intake |
| `app/services/chat_appointment_lookup.py` | Scheduled appointment listing |
| `app/services/chat_appointment_cancellation.py` | Cancellation identity, listing, selection, confirmation |
| `app/services/chat_appointment_rescheduling.py` | Reschedule identity, listing, new slot selection, confirmation |
| `app/services/chat_confirmation.py` | Typed yes/no confirmation for booking, cancellation, and reschedule |
| `app/services/post_completion_turn_classification.py` | Post-completion routing for new intents |

### Domain service layer

| Service | Side effects |
| --- | --- |
| `AppointmentBookingService` | Create `SCHEDULED` appointment, release hold, enqueue confirmation email |
| `AppointmentCancellationService` | Transition appointment to `CANCELLED`, audit, optional email job |
| `AppointmentReschedulingService` | Original → `RESCHEDULED`, successor → `SCHEDULED`, slot release/book, audit, email job |
| `PatientIdentityResolutionService` | Resolve patient by name + DOB |
| `AppointmentHoldService` | Redis hold create, validate, release |

### Persistence / infrastructure

- **PostgreSQL** — patients, appointments, conversations, audit logs, email jobs
- **Redis** — temporary appointment holds with channel-specific TTL
- **RabbitMQ** — wake-up messages for email worker (`email_job_id` only)
- **Fake providers** — `LLM_PROVIDER=fake`, `EMAIL_PROVIDER=fake`, `CHAT_TURN_UNDERSTANDING_INTERPRETER=fake` for local demo without external keys

## Appointment management task frame

### Modes

| `appointment_management_mode` | Purpose |
| --- | --- |
| `"lookup"` | List upcoming scheduled appointments |
| `"cancel"` | Cancel an existing appointment |
| `"reschedule"` | Move an appointment to a new slot |

### Awaiting states (shared pattern)

| `appointment_management_awaiting` | Meaning |
| --- | --- |
| `"patient_identity"` | Waiting for full name and/or date of birth |
| `"appointment_selection"` | Waiting for user to pick one of multiple offered appointments |
| `"cancellation_confirmation"` | Waiting for explicit cancel confirmation |
| `"reschedule_confirmation"` | Waiting for explicit reschedule confirmation |
| `"new_slot_selection"` | Reschedule only — waiting for a new time selection |
| `"completed"` | Flow finished; post-completion routing applies |

### Shared context keys

| Key | Purpose |
| --- | --- |
| `resolved_patient_id` | Backend-only patient UUID after successful identity resolution |
| `resolved_patient_name` | Display name for replies |
| `resolved_patient_email` | Email reused across flows when known |
| `patient_resolution_id` | Resolution record reference for possible-match flows |
| `offered_appointments` | List of `{ appointment_id, summary }` objects the user may choose from |
| `selected_appointment_id` | Backend-only UUID for the appointment pending action |
| `selected_appointment_summary` | Human-readable summary (specialty, doctor, weekday, time) |

Internal IDs exist in `chat_context` for backend validation and idempotency. They are **never** included in assistant replies.

## Booking flow behavior

Written-chat booking follows a strict sequence:

1. **Availability lookup** — specialty, doctor, date, and time preferences resolve to offered slots.
2. **Hold before final booking** — the user selects a specific offered time; the backend creates a Redis hold before identity collection.
3. **Identity collection only after valid booking context** — offered slots alone do not start identity intake. A bare name opener without scheduling context does not trigger booking identity. Revision or denial phrases are not parsed as patient names.
4. **Final confirmation** — after identity is complete, the assistant asks for explicit confirmation before creating the appointment.
5. **Decline at final confirmation** — replying `No` (or equivalent) aborts booking, releases or safely clears the hold, and does **not** create an appointment or confirmation email job.

For manual verification, use seeded demo patients (see [Demo seed data](#demo-seed-data)).

## Reschedule flow behavior

Written-chat rescheduling follows:

1. **Patient identity collection** — name + DOB (with partial memory and slash-form DOB support such as `1992/09/03`).
2. **Appointment selection** — when multiple upcoming appointments exist, the user may select by ordinal (`the second one`), weekday/date, time, clinician, or specialty.
3. **Progressive refinement** — ambiguous matches can be narrowed with follow-up turns before confirmation.
4. **New slot selection** — availability for the new time; a hold may be placed on the target slot.
5. **Final confirmation** — explicit confirmation before the domain service moves the appointment.
6. **Decline during new-slot selection** — replying `No` does **not** reschedule; the original appointment remains unchanged.

## Selection and revision behavior

Selection and revision are allowed **before final confirmation**. No hold refresh, booking, cancellation, or reschedule commit occurs until the user explicitly confirms the pending action.

| Example | Expected behavior |
| --- | --- |
| `Tuesday 15` / `Tuesday at 14` | Contextual weekday + bare hour interpreted as clinic-local time (`15:00`, `14:00`) when scheduling context makes the hour unambiguous |
| `The one on Monday` | Selects from offered appointments or slots by weekday when unambiguous |
| `The one at 14` | Selects by offered time when unambiguous |
| `On second thought, I want the one at 14` | Revises the pending slot or appointment selection before confirmation |
| `Actually, Wednesday` | Revises date preference during intake or offered-slot revision before a hold exists |

Offered-slot revision before a hold exists asks for scheduling clarification rather than jumping to identity intake.

## Patient identity behavior

Written chat collects patient identity using **full name + date of birth** for lookup, cancel, and reschedule flows. Booking identity intake may also collect email and new-patient details. Email or phone may be used to disambiguate when the backend finds multiple or uncertain matches. This is simplified demo matching, not production-grade healthcare identity verification.

| Behavior | Detail |
| --- | --- |
| **Partial identity memory** | If the user provides only name or only DOB, the assistant asks only for the missing field |
| **Slash-form DOB** | Values such as `1985/04/12` or `1992/09/03` are accepted alongside other supported formats |
| **Ambiguous numeric DOB** | Values such as `01/02/2000` trigger clarification (MM/DD vs DD/MM) before resolution |
| **Identity-only fresh openers** | A bare full name at conversation start does not start booking identity without scheduling context |
| **Resolved patient reuse** | After successful resolution, `resolved_patient_id` and related fields persist in `chat_context` for subsequent lookup, cancel, and reschedule turns in the same conversation |
| **Ownership validation** | Cancel and reschedule still verify `appointment.patient_id == resolved_patient_id` before domain execution |
| **Email without redundant confirmation** | When the user types a valid email in written chat, the backend accepts it and moves on — no separate “is that correct?” step for typed addresses |
| **Third-party patients** | Booking or managing appointments on behalf of family members or other patients is **not** supported as a product feature |

## Appointment hold behavior

Holds protect appointment slots from concurrent booking during an active scheduling flow.

| Setting | Default | Channel |
| --- | --- | --- |
| `APPOINTMENT_HOLD_TTL_SECONDS` | `300` (5 minutes) | Retell voice and general/default holds |
| `CHAT_APPOINTMENT_HOLD_TTL_SECONDS` | `600` (10 minutes) | Written chat holds |

Written chat uses a longer TTL so users can pause while typing identity and confirmation details. Retell tools do not control TTL; the backend applies the configured value.

Rules:

- **No booking without a valid hold** — `AppointmentBookingService` requires an active hold owned by the conversation.
- **Expired hold at final confirmation** — if the hold expired while the user was confirming, the backend attempts to create a fresh hold on the same slot when it is still available, then retries booking. If the slot cannot be re-held, stale hold state is cleared and the user is asked to choose another time.
- **Redis TTL on abandonment** — if the user leaves chat mid-flow, the hold expires automatically and the slot returns to availability.
- **Reschedule holds** — rescheduling creates channel-scoped holds on the new target slot using `CHAT_APPOINTMENT_HOLD_TTL_SECONDS`.

See [Appointment Slot Holds](appointment-holds.md) and [configuration.md](../configuration.md).

## Reschedule semantics

Rescheduling delegates to `AppointmentReschedulingService`:

1. Validate the original appointment is active and reschedulable.
2. Reserve the new target slot (hold).
3. Require explicit user confirmation.
4. Perform the durable transition atomically.

Status semantics:

| Record | Status after reschedule | Visible in list/cancel/reschedule? |
| --- | --- | --- |
| Original appointment | `RESCHEDULED` | **No** — historical/superseded |
| Successor appointment | `SCHEDULED` | **Yes** — the active appointment |

Only future active `SCHEDULED` appointments appear in lookup, cancellation, and reschedule listing. `RESCHEDULED`, `CANCELLED`, `COMPLETED`, and past appointments are excluded.

The domain service releases the old slot and books the new slot. Idempotency keys prevent duplicate successor appointments on replay.

## Scheduled appointment lookup

Written chat supports natural requests such as:

- `show my appointments`
- `check my appointments`
- `what appointments do I have?`
- `do I have any appointments scheduled?`

Behavior:

1. If the patient is already resolved in `chat_context`, list appointments directly.
2. Otherwise collect identity (name + DOB) first.
3. Query `list_upcoming_for_patient` — future appointments with status `SCHEDULED` only.
4. Present summaries in **clinic-local time** via `ClinicTimeService`.
5. Offer follow-up: cancel or reschedule any listed appointment.

Lookup does not expose internal appointment IDs. Selection in cancel/reschedule flows uses the same `offered_appointments` validation model as cancellation.

## Prompt / state alignment

Reliability rule: **the assistant prompt must match what the next turn accepts.**

| Situation | Expected behavior |
| --- | --- |
| Yes/no question asked | Next state accepts yes/no via `chat_confirmation` or `is_simple_affirmative` |
| Exactly one slot offered + hold prompt | `yes`, `sure`, `that works` create a hold — not generic fallback |
| Multiple offered slots | User must select explicitly (time, ordinal, or option number); bare `yes` does not silently pick a slot |
| Flow completed (`*_awaiting = completed`) | Post-completion classifier routes new booking, lookup, cancel, or reschedule intents |
| Human handoff during active flow | Explicit human request takes priority; active booking/management state yields to escalation |
| Confirmable intent switch | User may switch between booking, lookup, cancel, and reschedule with confirmation when another flow is active |
| Fresh conversation, bare `Yes` | Does not trigger orphan booking confirmation without active booking context |

Misaligned prompts are treated as reliability bugs. Backend validation remains authoritative even when turn understanding extracts a candidate.

## Demo seed data

After `python -m scripts.seed_demo_data`, manual written-chat patient lookup tests should use these seeded patients:

| Name | Date of birth | Email |
| --- | --- | --- |
| John Miller | 1985-04-12 | `john.miller@example.test` |
| Ava Thompson | 1992-09-03 | `ava.thompson@example.test` |

These are the canonical demo patients for written-chat lookup, cancel, reschedule, and returning-patient booking tests. Do not assume other sample names such as John Smith or Felipe Marques exist in seed data unless you create them during the session.

## Local demo behavior

Local development runs without real Groq, Retell, or Resend keys when using fake providers:

```env
LLM_PROVIDER=fake
EMAIL_PROVIDER=fake
CHAT_TURN_UNDERSTANDING_INTERPRETER=fake
RETELL_ENABLED=false
```

- Provider secrets belong in `.env` (from `.env.example`), never in committed config.
- `.env.example` and `.env.demo.example` contain safe placeholders only.
- Retell/voice remains separate from written-chat orchestration.
- Seed demo data with `python -m scripts.seed_demo_data` and refresh availability with `python -m app.scripts.generate_demo_availability`.

See [Local Development](../operations/local-development.md).

## Demo limitations and safety scope

The written-chat demo is intentionally scoped:

| Topic | Demo scope |
| --- | --- |
| **Conversational model** | Deterministic written-chat simulator with backend state guards — not a full production conversational AI |
| **Voice / agent layer** | Retell and LLM adapters represent the real integration boundary; voice remains provider-orchestrated separately |
| **Human escalation** | Simulated in this demo — creates an internal record and notification job path; does not connect to a live human agent |
| **Patient identity** | Simplified name + DOB matching with email or phone for disambiguation; not production-grade healthcare identity verification |
| **Clinical use** | Not intended for clinical advice, diagnosis, emergency triage, or medical decision support |
| **Patient profile updates** | Changing email, address, insurance, or medical record details is outside demo scope |
| **Moderation and recovery** | Advanced unsafe-message moderation, abusive-message policy, and repeated-failure conversational recovery are outside demo scope |
| **Demo focus** | Appointment workflow reliability, state handling, confirmation boundaries, and backend execution safety |
| **Third-party / family patients** | Not supported |
| **Patient portal** | Scheduled lookup only; richer portal features are outside demo scope |
| **Public frontend** | `web/` shell for portfolio demo; not a full patient portal |

Retell/voice integration, real provider keys on public routes, and hold-renewal policies remain separate production-hardening concerns documented under voice and operations guides.

## Relationship to appointment intake

| Concern | Appointment intake | Appointment management |
| --- | --- | --- |
| Task marker | `appointment_intake_awaiting` | `appointment_management_*` |
| CTU role | Specialty, doctor, date, slot extraction | Optional identity extraction during `patient_identity` |
| Side effect | Hold + booking | Lookup (read), cancel, or reschedule |
| Post-completion | Post-booking classifier | Post-completion classifier (shared phrase core) |

Active management frames suppress appointment intake routing until completed or superseded by a new top-level request after the post-completion frame.

## Safety boundaries

- No UUIDs or internal IDs in user-visible replies.
- No cancellation or reschedule without explicit confirmation after selection.
- No appointment selection outside `offered_appointments`.
- No direct appointment row mutation from the chat layer — only through domain services.
- Retell voice paths unchanged; voice and chat share domain services where appropriate.

## Automated coverage

- `tests/test_chat_appointment_cancellation.py`
- `tests/test_chat_appointment_rescheduling.py`
- `tests/test_chat_appointment_lookup.py`
- `tests/test_chat_booking_identity_orchestration.py`
- `tests/test_chat_receptionist_service.py`
- `tests/test_chat_booking_confirmation_flow.py` — hold refresh at confirmation
- `tests/test_post_cancellation_turn.py` / `tests/test_post_booking_turn.py`
