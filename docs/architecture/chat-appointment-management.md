# Chat Appointment Management — Contextual Cancellation

Branch: `feat/contextual-appointment-management-cancel-flow`

Written chat handles **appointment management** (cancel, and eventually reschedule) as a separate **task frame** from **appointment intake** (scheduling). Cancellation is the first completed slice of this frame.

Manual testing: [Chat Cancellation Flow Manual Testing](../testing/chat-cancellation-flow.md).

Related:

- [Chat Turn Understanding Architecture](chat-turn-understanding.md) — CTU contract and appointment intake task frame
- [Retell Voice Appointment Cancellation](retell-voice-cancellation.md) — shared `AppointmentCancellationService` on the voice channel
- [Appointment Rescheduling Foundation](appointment-rescheduling-foundation.md) — future chat reschedule slice

## Core principle

```text
The LLM understands. The backend validates and decides. Domain services execute.
```

In practice:

- **Chat / CTU** may interpret natural language (for example patient identity fields during cancellation intake).
- **Backend orchestration** owns task-frame state, patient resolution, offered-appointment lists, selection validation, confirmation gates, and ownership checks.
- **Domain services** execute durable side effects only after validation passes — cancellation runs exclusively through `AppointmentCancellationService`.
- **Assistant replies** must not expose internal IDs, UUIDs, raw timestamps, or other backend implementation details.

The interpreter and orchestrator never cancel appointments directly.

## Architecture overview

Cancellation is not part of appointment intake (`appointment_intake_awaiting`). It uses a parallel **appointment-management task frame** keyed by `appointment_management_mode` and `appointment_management_awaiting`.

High-level flow:

1. **Chat detects cancellation intent** — top-level keywords (`cancel`, `cancellation`) enter the cancellation task frame.
2. **Backend enters cancellation mode** — `appointment_management_mode = "cancel"` and `appointment_management_awaiting = "patient_identity"`.
3. **Patient identity is resolved** before any appointment lookup — full name and date of birth are required; `PatientIdentityResolutionService` resolves the patient.
4. **Appointments are listed only for the resolved patient** — `AppointmentRepository.list_cancelable_for_patient` returns upcoming cancelable appointments from clinic-local now.
5. **User selection is validated against offered appointments** — selection signals resolve only against `offered_appointments` stored in `chat_context`.
6. **Cancellation requires explicit confirmation** — selecting an appointment does not cancel it; confirmation uses `ConfirmationType.CANCELLATION_CONFIRMATION`.
7. **Domain cancellation execution** — on confirmed intent, `AppointmentCancellationService.cancel_appointment` runs with an idempotency key and audit metadata.

Primary modules:

| Module | Role |
| --- | --- |
| `app/services/chat_appointment_cancellation.py` | `ChatAppointmentCancellationOrchestrator` — identity intake, listing, selection, confirmation, service delegation |
| `app/services/chat_receptionist.py` | Task-frame routing, entry on cancel keywords, post-cancellation follow-up |
| `app/services/chat_confirmation.py` | `ConfirmationType.CANCELLATION_CONFIRMATION` phrase matching |
| `app/services/appointment_cancellation.py` | Shared domain cancellation service (also used by Retell voice) |
| `app/services/post_cancellation_turn.py` | Post-cancellation semantic decision classification |
| `app/services/post_completion_turn_classification.py` | Shared phrase lists for post-completion turns (booking and cancellation) |

## Cancellation task frame

### Mode and awaiting keys

| `appointment_management_mode` | `appointment_management_awaiting` | Meaning |
| --- | --- | --- |
| `"cancel"` | `"patient_identity"` | Waiting for full name and date of birth |
| `"cancel"` | `"appointment_selection"` | Waiting for user to pick one of multiple offered appointments |
| `"cancel"` | `"cancellation_confirmation"` | Waiting for explicit cancel confirmation |
| `"cancel"` | `"completed"` | Cancellation flow finished (cancelled, declined, or terminal error) |

When `appointment_management_awaiting` is not `"completed"`, the receptionist routes all turns through the cancellation orchestrator instead of appointment intake or generic fallback.

### Supporting context keys

| Key | Purpose |
| --- | --- |
| `resolved_patient_id` | Backend-only patient UUID after successful identity resolution |
| `resolved_patient_name` | Display name for replies |
| `patient_resolution_id` | Resolution record reference (for possible-match flows) |
| `offered_appointments` | List of `{ appointment_id, summary }` objects the user may choose from |
| `selected_appointment_id` | Backend-only UUID for the appointment pending confirmation or execution |
| `selected_appointment_summary` | Human-readable summary (specialty, doctor, weekday, time) |
| `cancellation_status` | `"cancelled"` or `"declined"` when the flow completes |
| `cancelled_appointment_summary` | Summary of the appointment that was cancelled |

Internal IDs (`resolved_patient_id`, `selected_appointment_id`, entries in `offered_appointments`) exist in `chat_context` for backend validation and idempotency. They are **never** included in assistant replies.

## Flow behavior

End-to-end cancellation sequence:

1. User asks to cancel (for example `I want to cancel my appointment`).
2. Assistant asks for full name and date of birth.
3. Backend resolves patient via `PatientIdentityResolutionService`.
4. Backend lists cancelable upcoming appointments for that patient.
5. **Single appointment** — assistant asks whether this is the appointment to cancel (skips explicit selection step).
6. **Multiple appointments** — assistant lists numbered summaries and asks which to cancel.
7. User selects an offered appointment (ordinal, number, specialty, doctor, weekday, or time signal).
8. Assistant asks for explicit confirmation (`Please confirm: should I cancel your …?`).
9. User confirms (`yes, cancel it`) or rejects (`no, don't cancel it`).
10. Backend cancels through `AppointmentCancellationService` only after confirmation.
11. Assistant confirms cancellation and asks `Is there anything else I can help with?`
12. User may close the conversation or start a new request (schedule, cancel, reschedule) via post-cancellation routing.

Edge cases handled in orchestration:

- Incomplete identity → reprompt for full name and DOB.
- Patient not found → safe message; no appointment lookup.
- No upcoming cancelable appointments → informative message; flow stays in identity context.
- Possible or multiple patient matches → clarification before listing appointments.
- Ownership mismatch at execution → rejection; no cancellation.
- Already-cancelled appointment → idempotent handling via the service.

## Appointment selection

When multiple appointments exist, `offered_appointments` is populated and `appointment_management_awaiting = "appointment_selection"`.

Supported selection signals (resolved only against `offered_appointments`):

| Signal type | Examples |
| --- | --- |
| Ordinal | `the first one`, `first one`, `the second one`, `second one`, … |
| Option number | `1`, `2`, `number 1` |
| Specialty phrase | `the dermatology one`, bare specialty name when it matches an offered row |
| Doctor name | `Dr. Emily`, partial last name when unique among offered rows |
| Weekday | `Wednesday` when it matches an offered appointment |
| Time | `10 AM`, `10:00` — normalized via `normalize_appointment_time_expression` and matched to offered `HH:MM` labels |

Selection rules:

- **Zero match** — reprompt (`Please choose one of the appointments I listed.`).
- **Ambiguous match** — ask clarification (`I found more than one matching appointment. Which one would you like to cancel?`).
- **Unique match** — set `selected_appointment_id` / `selected_appointment_summary` and move to `cancellation_confirmation`.
- The backend **never** selects an appointment that was not in `offered_appointments`.

Messages containing cancel keywords during selection are treated as off-topic and reprompt for selection rather than re-entering identity intake.

## Confirmation safety

Cancellation is a two-step commit after selection:

1. **Selection** — records intent only; does not call `AppointmentCancellationService`.
2. **Confirmation** — explicit user consent required before any domain execution.

Confirmation uses `understand_confirmation(confirmation_type=ConfirmationType.CANCELLATION_CONFIRMATION, …)` in `app/services/chat_confirmation.py`.

| User response | Decision | Behavior |
| --- | --- | --- |
| `yes`, `yes, cancel it`, `cancel it`, `please cancel it`, … | `CONFIRMED` | Proceed to ownership check and service call |
| `no`, `don't cancel`, `do not cancel`, `keep it`, `never mind`, … | `REJECTED` | Decline safely; `cancellation_status = "declined"`; no service call |
| `maybe`, empty, or other unclear text | `UNCLEAR` | Reprompt; confirmation frame stays active |
| Change requests (`different time`, `wait`, …) | `WANTS_CHANGE` | Reprompt; confirmation frame stays active |

Ambiguous responses never execute cancellation.

## Ownership and idempotency

Before calling the cancellation service, the orchestrator:

1. Loads the appointment by `selected_appointment_id`.
2. Verifies `appointment.patient_id == resolved_patient_id`.
3. Verifies the appointment is cancelable (or already cancelled for idempotent replay).

On ownership mismatch, the user receives a safe message and the flow completes without side effects.

**Idempotency key format:**

```text
chat-cancel:{conversation_id}:{appointment_id}
```

Passed to `AppointmentCancellationRequest` with `explicit_confirmation=True`, `source=chat_cancellation`, and chat audit actor metadata.

Duplicate confirmation in the same conversation should not double-cancel. Already-cancelled appointments are handled idempotently by `AppointmentCancellationService` (`result.already_cancelled`).

## Post-cancellation completion

After successful cancellation, context includes:

- `appointment_management_awaiting = "completed"`
- `cancellation_status = "cancelled"`
- `cancelled_appointment_summary`

The success message ends with: `Is there anything else I can help with?`

Post-cancellation turns are classified by `classify_post_cancellation_turn` (delegating to `classify_post_completion_turn`). Routing branches on semantic decisions rather than scattering phrase checks through the receptionist.

### Semantic decision model

| Decision | Typical user input | Assistant behavior |
| --- | --- | --- |
| `END_CONVERSATION` | `no thanks`, `that's all`, `nothing else`, `no`, … | Polite closing (`You're all set. Have a great day!`) |
| `NEEDS_MORE_HELP` | `yes`, `yeah`, `sure`, … | Ask whether user wants to schedule, cancel, or reschedule |
| `NEW_SCHEDULING_REQUEST` | `I want to schedule an appointment`, `book an appointment`, … | Return `None` from post-cancellation handler → normal top-level routing |
| `CANCEL_REQUEST` | `cancel`, `cancellation`, … | Return `None` → re-enter cancellation task frame |
| `RESCHEDULE_REQUEST` | `reschedule`, `move appointment`, … | Return `None` → normal routing (reschedule task frame is future work) |
| `UNKNOWN` | Unrecognized follow-up | Clarify options (schedule, cancel, reschedule) |

Phrase matching lives in `post_completion_turn_classification.py` and is isolated from orchestration logic. Actionable new requests (`NEW_SCHEDULING_REQUEST`, `CANCEL_REQUEST`, `RESCHEDULE_REQUEST`) fall through to standard receptionist routing.

## Relationship to appointment intake

| Concern | Appointment intake | Cancellation (this slice) |
| --- | --- | --- |
| Task marker | `appointment_intake_awaiting` | `appointment_management_awaiting` |
| Mode | Implicit (scheduling) | `appointment_management_mode = "cancel"` |
| CTU role | Specialty, doctor, date, slot extraction | Optional identity extraction during `patient_identity` |
| Side effect | Hold + booking via `AppointmentBookingService` | Cancel via `AppointmentCancellationService` |
| Post-completion | Post-booking classifier | Post-cancellation classifier (shared phrase core) |

The two frames do not overlap: active cancellation suppresses appointment intake routing until completed or superseded by a new top-level request after the post-cancellation frame.

## Safety boundaries

- No UUIDs or internal IDs in user-visible replies.
- No cancellation without explicit confirmation after selection.
- No appointment selection outside `offered_appointments`.
- No direct appointment row mutation from the chat layer — only through `AppointmentCancellationService`.
- Retell voice cancellation path unchanged; both channels share the domain service.

## Out of scope / follow-ups

| Item | Status |
| --- | --- |
| Rescheduling task frame (`appointment_management_mode = "reschedule"`) | Future slice |
| Retell voice flow | Not modified in this slice |
| Timezone/seed audit for demo availability | Future slice |
| Public demo with real LLM providers | Requires auth, rate limits, and cost controls |
| Frontend newline rendering | API JSON `\n` escaping is expected; clients may need to render newlines in multi-line lists |

## Automated coverage

- `tests/test_chat_appointment_cancellation.py` — orchestrator: identity, listing, selection, confirmation, ownership, idempotency
- `tests/test_post_cancellation_turn.py` — post-cancellation semantic classification
- `tests/test_chat_receptionist_service.py` — receptionist wiring and task-frame routing
