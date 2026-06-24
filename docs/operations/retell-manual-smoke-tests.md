# Retell Booking Smoke Tests

Manual validation record for the booking-stabilized Retell voice flow. Run after [Retell Dashboard Setup](retell-dashboard-setup.md) with demo data seeded (`python -m scripts.seed_demo_data`) and [Retell Master Prompt v3](retell-master-prompt-v3.md) (`retell-receptionist-v3`) pasted into the agent.

Companion docs:

- [Retell Master Prompt v3](retell-master-prompt-v3.md) — active agent prompt
- [Retell Tool Descriptions](retell-tool-descriptions.md) — tool contracts
- [Retell Voice Smoke Scenarios](retell-voice-smoke-scenarios.md) — extended scenario library

Use **fictional sample contact information** only.

---

## Scope

Booking-only voice flow. Cancellation and rescheduling are intentionally out of scope until appointment lookup tooling ships.

---

## Validated scenarios

The following behaviors were manually validated after the durable patient identity resolution backend hotfix (June 2026):

| Scenario | Result |
|----------|--------|
| New patient booking succeeds | **Validated** |
| Repeated patient data resolves existing profile instead of creating a duplicate | **Validated** |
| Existing patient lookup uses name + DOB before email | **Validated** |
| Final booking confirmation waits for caller response before `book_appointment` | **Validated** |
| `book_appointment` succeeds with `patient_resolution_id` | **Validated** |
| Cancel/reschedule requests do not enter the booking flow | **Validated** |

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

### 5 — Cancellation or rescheduling request

| Field | Detail |
|-------|--------|
| **Starting condition** | Active Retell agent with v3 prompt; cancel/reschedule tools registered but not used by prompt |
| **Caller path** | "I need to cancel my appointment" or "Can I reschedule for next week?" |
| **Expected tool sequence** | No `cancel_appointment`; no `reschedule_appointment`; no `resolve_patient_identity` for a new booking unless caller accepts scheduling a new appointment |
| **Expected result** | Agent explains booking-only scope, suggests front desk or patient portal for changes, offers: "Would you like help scheduling a new appointment instead?" |
| **Observed status** | Validated |

---

## Known limitations

- **Voice cancellation and rescheduling** require appointment lookup tooling (`list_patient_appointments` or equivalent) and patient–appointment ownership validation. The active prompt intentionally does not advertise these capabilities.
- **Availability hardening** (booking horizon, minimum lead time, filtering Redis-held slots, dynamic schedule rules) is planned separately and is not part of this validation record.
- These docs reflect the **tested booking flow**, not the full future receptionist experience (cancel, reschedule, waitlist, etc.).

---

## Follow-up roadmap

1. Add voice-native `list_patient_appointments` tool.
2. Add patient–appointment ownership validation for cancellation and rescheduling.
3. Release availability slot on cancellation.
4. Enable cancellation prompt only after backend support is complete.
5. Add scheduling hardening:
   - booking horizon
   - minimum lead time
   - filtering Redis-held slots from availability
   - rolling availability generation or dynamic schedule rules
6. Enable rescheduling after appointment lookup and availability hardening.

---

## Re-run checklist

After prompt or backend changes:

- [ ] Paste latest [v3 paste-ready prompt](retell-master-prompt-v3.md#paste-ready-retell-master-prompt) into Retell dashboard.
- [ ] Re-run scenarios 1–5 above and update **Observed status** if behavior changed.
- [ ] Review transcript for banned caller-facing terms (slot, UUID, patient not found, hold reference).
- [ ] Confirm `book_appointment` outcomes show `status: succeeded` only before spoken confirmation.
- [ ] Log prompt version `retell-receptionist-v3` in deployment notes.

See also: [Retell Voice Smoke Scenarios](retell-voice-smoke-scenarios.md) for extended IR and recovery cases.
