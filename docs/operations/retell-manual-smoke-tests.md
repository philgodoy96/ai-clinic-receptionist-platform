# Retell Booking and Appointment Lookup Smoke Tests

Manual validation record for the Retell voice flow with booking and upcoming appointment lookup. Run after [Retell Dashboard Setup](retell-dashboard-setup.md) with demo data seeded (`python -m scripts.seed_demo_data`) and [Retell Master Prompt v3](retell-master-prompt-v3.md) (`retell-receptionist-v3`) pasted into the agent.

Companion docs:

- [Retell Master Prompt v3](retell-master-prompt-v3.md) — active agent prompt
- [Retell Tool Descriptions](retell-tool-descriptions.md) — tool contracts
- [Retell Voice Smoke Scenarios](retell-voice-smoke-scenarios.md) — extended scenario library

Use **fictional sample contact information** only.

---

## Scope

Voice flow supports:

- **New appointment booking** end-to-end
- **Upcoming appointment lookup** via `list_patient_appointments` after identity resolution

**Cancellation and rescheduling execution are not enabled.** The agent may list appointments and ask which one the caller wants to change, but must not call `cancel_appointment` or `reschedule_appointment`.

---

## Validated scenarios

The following behaviors were manually validated (June 2026):

| Scenario | Result |
|----------|--------|
| New patient booking succeeds | **Validated** |
| Repeated patient data resolves existing profile instead of creating a duplicate | **Validated** |
| Existing patient lookup uses name + DOB before email | **Validated** |
| Final booking confirmation waits for caller response before `book_appointment` | **Validated** |
| `book_appointment` succeeds with `patient_resolution_id` | **Validated** |
| Reschedule intent lists appointments but does not reschedule | **Validated** |
| Cancel intent lists appointments but does not cancel | **Validated** |
| Multiple upcoming appointments presented; caller asked to choose | **Validated** |
| Appointment lookup uses `patient_resolution_id` only | **Validated** |

---

## Manual test checklist

For each scenario: note the starting condition, caller path, expected tool sequence, expected result, and observed status when re-running.

### 1 — New patient booking

| Field | Detail |
|-------|--------|
| **Starting condition** | No matching patient in demo database; `VOICE_PATIENT_INTAKE_MODE=demo_auto_create` |
| **Caller path** | "I haven't been here before" → specialty/time preference → accept offered slot → name → DOB (confirm) → email (repeat back, confirm yes) → final summary → explicit yes |
| **Expected tool sequence** | `get_clinic_context` → `check_availability` → `hold_appointment_slot` → `resolve_patient_identity` → `created` + `patient_resolution_id` → `book_appointment` → success |
| **Expected result** | Appointment booked; confirmation spoken only after `book_appointment` succeeds; no invented email |
| **Observed status** | Validated |

---

### 2 — Existing patient (name + DOB lookup)

| Field | Detail |
|-------|--------|
| **Starting condition** | Seeded patient (e.g. John Miller, DOB 1985-04-12) |
| **Caller path** | "I've been here before" → name + DOB when asked → accept slot if not yet held → confirm email when prompted after match → final summary → explicit yes |
| **Expected tool sequence** | `get_clinic_context` → `check_availability` → `hold_appointment_slot` → `resolve_patient_identity` (name + DOB, `patient_email` null) → `exact_match` + `patient_resolution_id` → `book_appointment` → success |
| **Expected result** | Email not requested before first identity lookup; booking uses resolution token |
| **Observed status** | Validated |

---

### 3 — Repeated new-patient data (no duplicate)

| Field | Detail |
|-------|--------|
| **Starting condition** | Patient already created in a prior demo call with same name, DOB, and email |
| **Caller path** | Same as new patient (claims new) or repeats identical details on a second call |
| **Expected tool sequence** | `get_clinic_context` → `check_availability` → `hold_appointment_slot` → `resolve_patient_identity` → `exact_match` or `possible_match` (not duplicate `created`) → `book_appointment` → success after confirmation |
| **Expected result** | Backend resolves existing profile; no second demo patient row for the same identity |
| **Observed status** | Validated |

---

### 4 — Final confirmation turn discipline

| Field | Detail |
|-------|--------|
| **Starting condition** | Hold active; identity resolved; email confirmed |
| **Caller path** | Agent speaks full appointment summary and asks "Should I book that?" → pause → caller says "Yes" |
| **Expected tool sequence** | No `book_appointment` in the same assistant turn as the final confirmation question; `book_appointment` only on the **next** turn with `explicit_confirmation: true` and `confirmation_text` copied from the caller's latest message |
| **Expected result** | Two-turn pattern: question turn, then booking turn after clear yes |
| **Observed status** | Validated |

---

### 5 — Reschedule intent (lookup only)

| Field | Detail |
|-------|--------|
| **Starting condition** | Seeded patient with two upcoming scheduled appointments |
| **Caller path** | "I need to reschedule my appointment" → provide name + DOB → agent lists appointments → caller chooses one (e.g. 2:00 PM) |
| **Expected tool sequence** | `resolve_patient_identity` → `exact_match` + `patient_resolution_id` → `list_patient_appointments` → no `reschedule_appointment` |
| **Expected result** | Agent lists two upcoming appointments; caller chooses one; agent acknowledges selection; agent explains this voice flow can look up appointments but cannot reschedule yet |
| **Observed status** | Validated |

---

### 6 — Cancel intent (lookup only)

| Field | Detail |
|-------|--------|
| **Starting condition** | Seeded patient with two upcoming scheduled appointments |
| **Caller path** | "I need to cancel my appointment" → provide name + DOB → agent lists appointments → caller chooses one (e.g. 3:00 PM) |
| **Expected tool sequence** | `resolve_patient_identity` → `exact_match` + `patient_resolution_id` → `list_patient_appointments` → no `cancel_appointment` |
| **Expected result** | Agent lists upcoming appointments; caller chooses one; agent acknowledges selection; agent explains this voice flow can look up appointments but cannot cancel yet |
| **Observed status** | Validated |

---

### 7 — Multiple appointments (choose one)

| Field | Detail |
|-------|--------|
| **Starting condition** | Seeded patient with multiple upcoming scheduled appointments |
| **Caller path** | Cancel or reschedule request → identity verified → agent reads concise summaries → caller picks one |
| **Expected tool sequence** | `resolve_patient_identity` → `list_patient_appointments` with `patient_resolution_id` → `next_step: choose_appointment` |
| **Expected result** | Appointments ordered soonest first; agent asks which appointment to change; agent does not auto-select |
| **Observed status** | Validated |

---

## Known limitations

- **Appointment lookup** is supported via `list_patient_appointments` after patient identity resolution.
- **Cancellation execution** is follow-up work — do not call `cancel_appointment` or claim an appointment was cancelled.
- **Rescheduling execution** is follow-up work — do not call `reschedule_appointment` or claim an appointment was rescheduled.
- **Selected appointment persistence** for cancel/reschedule may be addressed in a future slice.
- **Slot release on cancellation** is not implemented yet.
- **Scheduling hardening** (booking horizon, minimum lead time, Redis-held slot filtering, dynamic schedule rules) remains follow-up work.
- These docs reflect the **tested booking and lookup flow**, not the full future receptionist experience.

---

## Follow-up roadmap

1. **Voice cancellation flow** — selected appointment, explicit confirmation, patient–appointment ownership validation, slot release on cancellation.
2. **Scheduling hardening** — booking horizon, minimum lead time, filtering Redis-held slots from availability, rolling or dynamic availability.
3. **Voice rescheduling flow** — selected appointment, new availability lookup, hold new time, final reschedule confirmation, safe reschedule execution.

---

## Re-run checklist

After prompt or backend changes:

- [ ] Paste latest [v3 paste-ready prompt](retell-master-prompt-v3.md#paste-ready-retell-master-prompt) into Retell dashboard.
- [ ] Register `list_patient_appointments` in Retell (ten tools total).
- [ ] Re-run scenarios 1–7 above and update **Observed status** if behavior changed.
- [ ] Review transcript for banned caller-facing terms (slot, UUID, patient not found, hold reference).
- [ ] Confirm `book_appointment` outcomes show `status: succeeded` only before spoken confirmation.
- [ ] Confirm cancel/reschedule intents never call `cancel_appointment` or `reschedule_appointment`.
- [ ] Log prompt version `retell-receptionist-v3` in deployment notes.

See also: [Retell Voice Smoke Scenarios](retell-voice-smoke-scenarios.md) for extended IR and recovery cases.
