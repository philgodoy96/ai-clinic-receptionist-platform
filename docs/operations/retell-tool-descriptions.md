# Retell Dashboard Tool Descriptions

Canonical descriptions for the nine Retell custom functions in the public scheduling demo. Paste the **Dashboard description** into each tool's description field in the Retell console. Use the full sections below for agent prompt authoring, debugging, and recovery behavior.

Related docs:

- [Retell Dashboard Setup](retell-dashboard-setup.md)
- [Retell Master Prompt v2](retell-master-prompt-v2.md)
- [Retell Conversation UX Playbook](retell-conversation-ux-playbook.md)
- [Retell Voice Smoke Scenarios](retell-voice-smoke-scenarios.md)
- [Clinic Time Context and Tool Contracts](../architecture/clinic-time-context-and-tool-contracts.md)

All tools POST to `https://<api-host>/api/v1/retell/tools` with Retell's **default payload envelope** (not **Payload: args only**).

Tool responses use `status`: `succeeded`, `failed`, or `rejected`. Treat only `status: succeeded` as a completed action for booking, cancellation, and rescheduling.

---

## 1. get_clinic_context

### Dashboard description

```
Read-only. Returns the clinic's current calendar date, weekday, timezone, business days, and opening hours. Call this before discussing "today", "tomorrow", relative weekdays, or business hours. No arguments.
```

### Exact name

`get_clinic_context`

### When to call

- At the start of scheduling when the caller uses relative dates ("tomorrow", "next Tuesday").
- Before stating today's date, current time context, or clinic opening hours.
- Before `check_availability` when you need authoritative clinic calendar context.

### When not to call

- After clinic context is already established for the same call and the date discussion has not changed.
- To search for appointment times (use `check_availability`).
- To hold or book an appointment.

### Expected arguments

```json
{}
```

No fields required. `args` may be an empty object.

### Side effects

None. Read-only.

### Example success result

```json
{
  "clinic_name": "Demo Clinic",
  "clinic_timezone": "America/New_York",
  "current_date": "2026-07-01",
  "current_weekday": "wednesday",
  "business_days": ["monday", "tuesday", "wednesday", "thursday", "friday"],
  "business_hours": { "start": "09:00", "end": "17:00" }
}
```

### Common errors

| `error_code` | Cause |
|--------------|--------|
| `clinic_time_unavailable` | Clinic time service not configured on the deployment |

### Receptionist recovery

If this tool fails, apologize briefly and ask the caller for a specific calendar date instead of relative language. Do not guess today's date from memory.

---

## 2. check_availability

### Dashboard description

```
Read-only. Searches the schedule for available appointment times. Prefer date_expression (today, tomorrow, next_weekday, exact_date) and optional time_window_expression (morning, afternoon, evening, exact_time). Returns available_slots with ids for hold_appointment_slot. No side effects.
```

### Exact name

`check_availability`

### When to call

- After `get_clinic_context` when the caller wants to find an opening.
- When a previously offered time is no longer available and you need alternatives.
- When the caller changes doctor, day, or time preference.

### When not to call

- Before resolving clinic calendar context for relative date language.
- To hold or book a time (use `hold_appointment_slot` then `book_appointment`).
- Repeatedly with identical arguments when the caller has not changed their request.

### Expected arguments

Preferred contract:

```json
{
  "doctor_name": "Dr. Emily Carter",
  "date_expression": { "kind": "tomorrow" },
  "time_window_expression": { "kind": "morning" },
  "limit": 5
}
```

| Field | Required | Notes |
|-------|----------|-------|
| `doctor_id` | No | UUID; prefer `doctor_name` in voice |
| `doctor_name` | No | e.g. `"Dr. Emily Carter"` |
| `specialty_name` | No | e.g. `"Dermatology"` |
| `date_expression` | Preferred | See [date expression kinds](../architecture/clinic-time-context-and-tool-contracts.md) |
| `time_window_expression` | No | `morning`, `afternoon`, `evening`, or `exact_time` with `exact_time: "HH:MM"` |
| `requested_date_text` | Legacy | Natural-language date; server-parsed |
| `start_from` / `start_to` | Legacy | UTC window; validated server-side |
| `limit` | No | 1–50; caps returned openings |

`date_expression` kinds: `today`, `tomorrow`, `this_weekday`, `next_weekday`, `next_week`, `in_n_days`, `exact_date`.

### Side effects

None. Read-only. Updates voice conversation context with scheduling preferences for later tools.

### Example success result

```json
{
  "available_slots": [
    {
      "availability_slot_id": "...",
      "doctor_name": "Dr. Emily Carter",
      "start_time": "2026-07-02T13:30:00Z",
      "end_time": "2026-07-02T14:00:00Z"
    }
  ]
}
```

### Common errors

| `error_code` | Cause |
|--------------|--------|
| `clinic_closed` | Resolved date falls on a non-business day (e.g. Saturday) |
| `past_date` | Resolved date is before clinic today |
| `outside_business_hours` | Time window outside opening hours |
| `invalid_scheduling_expression` | Missing or malformed date/time expression |
| `clinic_time_unavailable` | Clinic time service not configured |
| `doctor_not_found` | No matching doctor |
| `retell_tool_arguments_invalid` | Argument validation failed |

An empty `available_slots` list is not an error — it means no openings match the query.

### Receptionist recovery

| Error / result | Say |
|----------------|-----|
| `clinic_closed` | "We're closed that day. Would another weekday work?" |
| `outside_business_hours` | "We're open nine to five — would an earlier or later time work?" |
| Empty slots | "I don't see anything open then. Would you like a different day or time of day?" |
| `doctor_not_found` | "I don't have that doctor on the schedule. Would you like dermatology, cardiology, or primary care?" |

Never speak error codes or internal field names to the caller.

---

## 3. hold_appointment_slot

### Dashboard description

```
Side effect: temporarily reserves one appointment time for this call while you collect patient details. Requires availability_slot_id from check_availability. Say "I can hold that time" — never say "hold slot". Call before book_appointment.
```

### Exact name

`hold_appointment_slot`

### When to call

- Immediately after the caller accepts a specific appointment time from `check_availability`.
- Again after a hold expires and the caller chooses a replacement time.

### When not to call

- Before the caller selects a specific time.
- Before `check_availability` has returned a valid `availability_slot_id`.
- When you already have an active hold for the same time and call (unless recovering from expiry).

### Expected arguments

```json
{
  "availability_slot_id": "7fcf0ca1-7f14-4f45-a8a4-cc77d1a67f4d"
}
```

| Field | Required | Notes |
|-------|----------|-------|
| `availability_slot_id` | Yes | UUID from `check_availability` result |
| `owner_id` | No | Defaults to provider call id |
| `ttl_seconds` | No | 60–900; default from server hold TTL |

### Side effects

- Creates a Redis-backed hold tied to this voice call.
- Updates voice conversation context with active hold reference.
- Prevents other callers from booking that appointment time until release, expiry, or successful booking.

### Caller-facing language

Say: **"I can hold that time while I get your details."**

Do **not** say: hold slot, hold reference, temporary hold, or speak UUIDs.

### Common errors

| `error_code` | Cause |
|--------------|--------|
| `availability_slot_not_found` | Slot id invalid or removed |
| `availability_slot_unavailable` | Slot no longer available |
| `slot_already_held` | Another call holds this time |
| `clinic_closed` / `outside_business_hours` / `past_date` | Slot fails clinic time validation |
| `missing_hold_owner` | Could not resolve hold owner for this call |
| `invalid_appointment_hold` | Invalid hold window or owner |
| `appointment_hold_ownership_error` | Hold belongs to another owner |

### Receptionist recovery

| Error | Say |
|-------|-----|
| Any hold failure | "That time may no longer be available. Let me check the schedule again." |
| Then | Call `check_availability` and offer alternatives |

---

## 4. release_appointment_hold

### Dashboard description

```
Side effect: releases a held appointment time when the caller abandons that time or chooses a different opening. Use hold_id from the active hold. Optional when the caller switches times before booking.
```

### Exact name

`release_appointment_hold`

### When to call

- Caller abandons a held time and does not want to book it.
- Caller chooses a different time and you need to free the previous hold before holding a new one.
- Caller ends the call before booking (optional courtesy release).

### When not to call

- After a successful `book_appointment` (hold is released automatically).
- When there is no active hold.
- As a substitute for booking.

### Expected arguments

```json
{
  "hold_id": "2d85f2c2-5d2e-4c2a-ae2f-09e32011ce37"
}
```

| Field | Required | Notes |
|-------|----------|-------|
| `hold_id` | Yes | UUID from `hold_appointment_slot` result or voice context |
| `owner_id` | No | Defaults to provider call id |

### Side effects

- Removes the Redis hold.
- Clears active hold from voice conversation context.

### Common errors

| `error_code` | Cause |
|--------------|--------|
| `missing_hold_owner` | Could not resolve hold owner |
| `appointment_hold_ownership_error` | Hold belongs to another call or already released |

### Receptionist recovery

If release fails because the hold already expired, continue naturally: "No problem — let me find another opening for you." Then call `check_availability`.

---

## 5. resolve_patient_identity

### Dashboard description

```
Read-only identity resolution (demo create only when allowed). Returns match_status, patient_resolution_id, and suggested_response_text. Use before book_appointment. Collect patient_name and patient_date_of_birth from the caller. Never invent email or phone. Do not expose raw patient_id.
```

### Exact name

`resolve_patient_identity`

### When to call

- After the caller provides name and date of birth (and email or phone when available).
- Before `book_appointment` when patient identity is not yet resolved.
- When narrowing ambiguous matches (add email or phone on retry).

### When not to call

- Before asking the caller for identity fields.
- With invented email or phone values.

### Expected arguments

```json
{
  "patient_name": "Michael Reed",
  "patient_date_of_birth": "1985-04-12",
  "patient_email": null,
  "patient_phone": null,
  "caller_claims_existing_patient": true,
  "allow_demo_patient_creation": false
}
```

| Field | Required | Notes |
|-------|----------|-------|
| `patient_name` | Yes | Full name as spoken by caller |
| `patient_date_of_birth` | Yes | ISO date `YYYY-MM-DD` in tool args only |
| `patient_email` | No | Use when caller provided and confirmed email |
| `patient_phone` | No | Omit unless caller provided a number |
| `caller_claims_existing_patient` | No | Default `true`; blocks demo create when no match |
| `allow_demo_patient_creation` | No | Default `false`; set `true` only in public demo for new patients with `.test` email |

### Success result fields

| Field | Meaning |
|-------|---------|
| `match_status` | `exact_match`, `possible_match`, `multiple_matches`, `not_found`, or `created` |
| `requires_confirmation` | `true` when caller must confirm identity |
| `patient_resolution_id` | Opaque token for confirm/booking (absent on `not_found` / `multiple_matches`) |
| `confirmation_question` | Safe natural-language question for `possible_match` |
| `next_step` | Agent guidance (`proceed_to_booking`, `confirm_identity`, `collect_email`, etc.) |
| `suggested_response_text` | Provider-safe phrase for the receptionist |

### Common errors

| `error_code` | Cause |
|--------------|--------|
| `retell_tool_arguments_invalid` | Missing or invalid arguments |
| `booking_identity_missing` | Blank patient name |
| `patient_identity_resolution_unavailable` | Resolution service not configured |

### Receptionist recovery

| `match_status` | Say |
|----------------|-----|
| `possible_match` | Ask `confirmation_question`, then call `confirm_patient_identity` |
| `multiple_matches` | Ask for email (or phone) on file — one question at a time |
| `not_found` | "I'm not matching those details yet — could we try your name and date of birth once more?" |

---

## 6. confirm_patient_identity

### Dashboard description

```
Confirms or rejects a possible_match patient_resolution_id from resolve_patient_identity. Call only after the caller answers the confirmation question. confirmed: true proceeds toward booking; confirmed: false rejects the candidate.
```

### Exact name

`confirm_patient_identity`

### When to call

- After `resolve_patient_identity` returns `possible_match` and the caller answers yes or no.

### Expected arguments

```json
{
  "patient_resolution_id": "opaque-token",
  "confirmed": true,
  "confirmation_text": "Yes, that's me."
}
```

| Field | Required | Notes |
|-------|----------|-------|
| `patient_resolution_id` | Yes | From `resolve_patient_identity` |
| `confirmed` | Yes | `true` if caller affirmed identity |
| `confirmation_text` | No | Short caller phrase |

### Common errors

| `error_code` | Cause |
|--------------|--------|
| `patient_resolution_not_found` | Unknown, expired, or wrong-call token |
| `patient_identity_confirmation_rejected` | Caller said no (`confirmed: false`) — status `rejected` |
| `retell_tool_arguments_invalid` | Missing `patient_resolution_id` |

---

## 7. book_appointment

### Dashboard description

```
Side effect: books the appointment after explicit caller confirmation. Requires active hold, patient_name, patient_date_of_birth, patient_email from the caller, explicit_confirmation: true. Never call immediately after asking a question. Never invent email or phone. Only say "booked" when status=succeeded.
```

### Exact name

`book_appointment`

### When to call

- After reading back a full summary (doctor, appointment time, name, email).
- After the caller gives an explicit yes to schedule.
- With `explicit_confirmation: true` and identity fields collected **from the caller** (never invented or placeholder values).

### When not to call

- Before `hold_appointment_slot` succeeds.
- Before the caller confirms the summary.
- **Immediately after asking a question** — wait for the caller's answer first (especially email, DOB, or final confirmation).
- With `explicit_confirmation: false` or missing.
- With guessed, invented, or placeholder email addresses (for example do not fabricate `felipe.logan@example.test`).
- With invented `patient_phone` — omit the field unless the caller provided a number.
- When the caller is still thinking, checking a calendar, or asking clarifying questions.

### Expected arguments

```json
{
  "hold_id": "2d85f2c2-5d2e-4c2a-ae2f-09e32011ce37",
  "patient_name": "John Miller",
  "patient_date_of_birth": "1985-04-12",
  "patient_email": "john.miller@example.test",
  "patient_phone": "+1-555-0201",
  "explicit_confirmation": true,
  "confirmation_text": "Yes, please schedule that."
}
```

| Field | Required | Notes |
|-------|----------|-------|
| `hold_id` | Yes* | From active hold; *or `slot_id` if hold in context |
| `slot_id` | Alt | UUID when hold id omitted but context has slot |
| `patient_name` | Yes | Full name as confirmed with caller |
| `patient_date_of_birth` | Yes | ISO date `YYYY-MM-DD` in tool args only — do not require caller to speak this format |
| `patient_email` | Yes | Must match what caller **spoke and confirmed**; never invent |
| `patient_phone` | No | Omit unless caller provided a number; never invent |
| `explicit_confirmation` | Yes | Must be `true` only after hold + identity collected/confirmed + final summary + clear yes |
| `confirmation_text` | No | Short caller confirmation phrase |
| `notes` | No | Visit reason if collected |

### Side effects

- Creates appointment in PostgreSQL via `AppointmentBookingService`.
- Releases the hold after successful commit.
- May enqueue confirmation email job (subject to demo email quotas).
- Records voice booking attempt for idempotency.
- Updates voice conversation context with appointment reference.

### Booking rules (critical)

1. **Never call before final confirmation** — hold succeeded, name/DOB/email collected and confirmed, full summary spoken, explicit yes received.
2. **Never call right after asking a question** — wait for the caller's answer before `book_appointment`.
3. **Do not invent email or phone** — use only addresses and numbers the caller provided and you confirmed.
4. **Do not say booked unless `status: succeeded`** — failed or rejected means the appointment was not created.
5. **`duplicate: true`** on retry with the same `tool_call_id` still means success — do not create alarm; confirm once to the caller.

### Common errors

| `error_code` | Cause |
|--------------|--------|
| `booking_confirmation_required` | `explicit_confirmation` not true |
| `booking_identity_missing` | Incomplete patient identity |
| `booking_hold_missing` | No active hold |
| `appointment_hold_expired` | Hold timed out |
| `appointment_hold_owner_mismatch` | Hold belongs to another call |
| `patient_not_found` | Name, DOB, and email do not match a patient record (or demo intake is `lookup_only` / email is not a `.test` domain) |
| `demo_guardrail_limit_exceeded` | Daily demo booking quota reached |
| `missing_voice_conversation_context` | Voice call / conversation not linked |
| `voice_booking_unavailable` | Booking service not configured |

### Receptionist recovery

| Error | Say |
|-------|-----|
| `patient_not_found` | "I'm not matching those details yet — could we try your name and date of birth once more?" Then re-collect one field at a time. **Never say "patient not found."** |
| `appointment_hold_expired` | "That time may no longer be available. Let me check the schedule again." Re-run `check_availability` → `hold_appointment_slot` → summary → confirm → book. |
| `booking_confirmation_required` | Ask again: "Would you like me to go ahead and schedule that appointment?" |
| `demo_guardrail_limit_exceeded` | "I'm unable to complete another booking right now. Please try again later." |
| Any failure before success | Do **not** say the appointment is booked. Apologize briefly and continue or offer alternatives. |

---

## 8. cancel_appointment

### Dashboard description

```
Side effect: cancels an existing appointment after explicit caller confirmation. Requires explicit_confirmation: true and appointment reference from context or appointment_id. Only say cancelled when status=succeeded.
```

### Exact name

`cancel_appointment`

### When to call

- Caller asks to cancel a known upcoming appointment.
- After confirming which appointment and receiving explicit yes to cancel.

### When not to call

- During a new booking flow before an appointment exists.
- Without `explicit_confirmation: true`.
- When the appointment reference is ambiguous (multiple appointments, unclear identity).

### Expected arguments

```json
{
  "appointment_id": "4c71ec24-892b-4ab1-b4f4-cf5e42e88e91",
  "explicit_confirmation": true,
  "confirmation_text": "Yes, cancel it.",
  "cancellation_reason": "Schedule conflict",
  "patient_name": "John Miller",
  "patient_date_of_birth": "1985-04-12",
  "patient_email": "john.miller@example.test"
}
```

| Field | Required | Notes |
|-------|----------|-------|
| `appointment_id` | Context* | UUID; may resolve from voice context if unambiguous |
| `explicit_confirmation` | Yes | Must be `true` |
| `confirmation_text` | No | Caller confirmation phrase |
| `cancellation_reason` | No | Brief reason |
| `patient_name` / `patient_date_of_birth` / `patient_email` | No | Identity cross-check when required by context |

### Side effects

- Cancels appointment via `AppointmentCancellationService`.
- Updates voice conversation context.

### Common errors

| `error_code` | Cause |
|--------------|--------|
| `cancellation_confirmation_required` | Missing explicit confirmation |
| `appointment_reference_required` | No unambiguous appointment to cancel |
| `appointment_context_mismatch` | Identity does not match appointment context |
| `appointment_not_found` | Appointment does not exist |
| `appointment_not_cancelable` | Appointment status prevents cancellation |
| `voice_cancellation_unavailable` | Cancellation service not configured |

### Receptionist recovery

| Error | Say |
|-------|-----|
| `appointment_reference_required` | "Which appointment would you like to cancel? I can look up your upcoming visits once I have your name and date of birth." |
| `cancellation_confirmation_required` | "Just to confirm — would you like me to cancel that appointment?" |
| `appointment_not_cancelable` | "That appointment can't be cancelled on the phone. I can note your request or help you reschedule." |

---

## 9. reschedule_appointment

### Dashboard description

```
Side effect: moves an existing appointment to a new time after explicit confirmation. Requires original appointment reference plus hold_id or new_slot_id from check_availability and hold_appointment_slot. Only say rescheduled when status=succeeded.
```

### Exact name

`reschedule_appointment`

### When to call

- Caller asks to move an existing appointment.
- After new time is held and caller explicitly confirms the reschedule.

### When not to call

- For new bookings (use `book_appointment`).
- Without a new target hold or slot.
- Without `explicit_confirmation: true`.

### Expected arguments

```json
{
  "original_appointment_id": "4c71ec24-892b-4ab1-b4f4-cf5e42e88e91",
  "hold_id": "2d85f2c2-5d2e-4c2a-ae2f-09e32011ce37",
  "explicit_confirmation": true,
  "confirmation_text": "Yes, move it to the new time.",
  "reschedule_reason": "Conflict with work",
  "patient_name": "John Miller",
  "patient_date_of_birth": "1985-04-12",
  "patient_email": "john.miller@example.test"
}
```

| Field | Required | Notes |
|-------|----------|-------|
| `original_appointment_id` | Context* | May resolve from voice context |
| `hold_id` | Yes* | New time hold; *or `new_slot_id` |
| `new_slot_id` | Alt | Target slot UUID |
| `explicit_confirmation` | Yes | Must be `true` |
| `confirmation_text` | No | Caller confirmation phrase |
| `reschedule_reason` | No | Brief reason |
| Patient identity fields | No | Cross-check when required |

### Side effects

- Reschedules via `AppointmentReschedulingService`.
- Releases old slot; books new slot.
- Updates voice conversation context.

### Common errors

| `error_code` | Cause |
|--------------|--------|
| `reschedule_confirmation_required` | Missing explicit confirmation |
| `appointment_reference_required` | Original appointment unclear |
| `appointment_context_mismatch` | Identity mismatch |
| `appointment_not_found` | Original appointment not found |
| `appointment_not_reschedulable` | Status prevents reschedule |
| `active_hold_required` | Target hold expired |
| `new_slot_required` / `slot_unavailable` / `slot_already_booked` | Target time invalid |
| `voice_rescheduling_unavailable` | Rescheduling service not configured |

### Receptionist recovery

| Error | Say |
|-------|-----|
| `active_hold_required` / hold errors | "That time may no longer be available. Let me check the schedule again." |
| `appointment_reference_required` | "Which appointment would you like to move? I'll need your name and date of birth first." |
| `reschedule_confirmation_required` | "Shall I move your appointment to [new time]?" |

---

## Global tool response contract

```json
{
  "status": "succeeded",
  "tool_name": "book_appointment",
  "tool_call_id": "tc_abc123",
  "result": { },
  "duplicate": false
}
```

| `status` | Meaning |
|----------|---------|
| `succeeded` | Tool completed; `result` has details |
| `failed` | Business rule or domain failure; `error_code` set |
| `rejected` | Request invalid before execution (bad args, unsupported tool) |

## Idempotency and `tool_call_id`

Retell may retry side-effecting tools. When Retell sends the same `tool_call_id` for the same call and tool:

- The backend returns the stored outcome with `duplicate: true`.
- `book_appointment`, `cancel_appointment`, `reschedule_appointment`, `resolve_patient_identity`, and `confirm_patient_identity` do not double-apply side effects on retry.
- The receptionist should treat `duplicate: true` with `status: succeeded` as success — confirm once, do not apologize for a duplicate.

Side-effecting tools without `tool_call_id` do not get cross-retry deduplication at the adapter layer.

---

## Dashboard registration checklist

- [ ] All seven tools registered at `POST /api/v1/retell/tools`
- [ ] Tool names match exactly (snake_case)
- [ ] Dashboard descriptions pasted from this document
- [ ] **Payload: args only** is **OFF** (Retell default envelope)
- [ ] Agent prompt uses [Retell Master Prompt v2](retell-master-prompt-v2.md)
- [ ] Smoke tests follow [Retell Voice Smoke Scenarios](retell-voice-smoke-scenarios.md)
