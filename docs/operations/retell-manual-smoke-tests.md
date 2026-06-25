# Retell Booking, Lookup, Cancellation, and Rescheduling Smoke Tests

Manual validation record for the Retell voice flow with booking, upcoming appointment lookup, cancellation, and rescheduling. Run after [Retell Dashboard Setup](retell-dashboard-setup.md) with demo data seeded (`python -m scripts.seed_demo_data`) and [Retell Master Prompt v5](retell-master-prompt-v5.md) (`retell-receptionist-v5`) pasted into the agent.

Companion docs:

- [Retell Master Prompt v5](retell-master-prompt-v5.md) — active agent prompt
- [Retell Master Prompt v4](retell-master-prompt-v4.md) — historical cancellation-only prompt (rescheduling deferred)
- [Retell Tool Configuration](retell-tool-configuration.md) — tool parameter schemas
- [Retell Tool Descriptions](retell-tool-descriptions.md) — tool contracts
- [Retell Voice Smoke Scenarios](retell-voice-smoke-scenarios.md) — extended scenario library

Use **fictional sample contact information** only.

---

## Scope

Voice flow supports:

- **New appointment booking** end-to-end
- **Upcoming appointment lookup** via `list_patient_appointments` after identity resolution
- **Appointment cancellation** via `cancel_appointment` after explicit confirmation
- **Appointment rescheduling** via `reschedule_appointment` after explicit confirmation, identity resolution, appointment selection, new availability check, and hold on the new slot

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
| Reschedule intent completes rescheduling after explicit confirmation | **Validated** |
| Cancel intent completes cancellation after explicit confirmation | **Validated** |
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

### 5 — Reschedule intent (full execution)

| Field | Detail |
|-------|--------|
| **Starting condition** | Seeded patient with at least one upcoming scheduled appointment and an available target slot |
| **Caller path** | "I need to reschedule my appointment" → name + DOB → agent lists appointments → caller selects appointment → agent checks new availability → caller selects new time → agent holds new time → agent summarizes move → explicit confirmation → caller says "Yes" |
| **Expected tool sequence** | `resolve_patient_identity` → `list_patient_appointments` → `check_availability` → `hold_appointment_slot` → no `reschedule_appointment` in confirmation-question turn → `reschedule_appointment` on **next** turn with `patient_resolution_id`, `appointment_id`, `new_slot_id`, `hold_id`, `explicit_confirmation: true`, `confirmation_text` → success |
| **Expected result** | Old appointment `rescheduled`; old slot `available`; new appointment `scheduled`; new slot `booked`; agent confirms only after tool success; old slot may be reused for a new booking |
| **Observed status** | Validated |

---

### 6 — Cancel intent (explicit confirmation)

| Field | Detail |
|-------|--------|
| **Starting condition** | Seeded patient with at least one upcoming scheduled appointment |
| **Caller path** | "I need to cancel my appointment" → provide name + DOB → agent lists appointments → caller selects one → agent repeats summary and asks explicit cancellation confirmation → caller says "Yes" |
| **Expected tool sequence** | `resolve_patient_identity` → `exact_match` + `patient_resolution_id` → `list_patient_appointments` → no `cancel_appointment` in the confirmation-question turn → `cancel_appointment` on the **next** turn with `patient_resolution_id`, `appointment_id`, `explicit_confirmation: true`, and `confirmation_text` from the caller's latest message → success |
| **Expected result** | Agent confirms cancellation only after `cancel_appointment` succeeds; appointment status changes from `scheduled` to `cancelled` in the database |
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

## Voice Rescheduling Smoke Tests

### Validated scenario — successful rescheduling

| Step | Detail |
|------|--------|
| Caller intent | Requested appointment rescheduling |
| Identity | Agent collected name and date of birth; `resolve_patient_identity` returned a valid `patient_resolution_id` |
| Lookup | `list_patient_appointments` returned upcoming appointments |
| Selection | Caller selected the old appointment to move |
| New time | Agent called `check_availability` for the requested new time |
| Hold | Agent called `hold_appointment_slot` for the new slot (`new_slot_id`, `hold_id`) |
| Confirmation | Agent asked explicit reschedule confirmation; caller confirmed on the next turn |
| Execution | `reschedule_appointment` succeeded with `patient_resolution_id`, `appointment_id`, `new_slot_id`, `hold_id`, `explicit_confirmation: true`, and `confirmation_text` |
| Database | Old appointment `rescheduled`; old slot `available`; new appointment `scheduled`; new slot `booked` |
| Reuse | Old slot could be booked again for a new appointment after rescheduling |
| Spoken result | Agent confirmed rescheduling only after tool success |

### Manual checklist — rescheduling validation

| # | Scenario | Expected behavior |
|---|----------|-------------------|
| R1 | Successful reschedule | Full flow above; historical-preserving state transition |
| R2 | Missing final confirmation | Agent asks reschedule confirmation but caller does not confirm → no `reschedule_appointment` |
| R3 | Expired or missing hold | `reschedule_appointment` fails with hold-expired style error; original appointment remains `scheduled`; new slot remains `available` |
| R4 | Appointment not owned by patient | Safe failure; agent does not claim reschedule succeeded |
| R5 | Old appointment already cancelled or rescheduled | `appointment_not_reschedulable`; no duplicate successor appointment |
| R6 | New slot no longer available | Safe failure; original appointment and slot unchanged |
| R7 | Duplicate Retell tool call | Same `tool_call_id` retry does not create duplicate appointments (`duplicate: true`) |

---

## Voice Cancellation Smoke Tests

### Validated scenario — successful cancellation

| Step | Detail |
|------|--------|
| Caller intent | Asked to cancel an appointment |
| Identity | Agent collected name and date of birth; `resolve_patient_identity` returned a valid `patient_resolution_id` |
| Lookup | `list_patient_appointments` returned upcoming appointments |
| Selection | Caller selected the target appointment |
| Confirmation | Agent asked explicit cancellation confirmation; caller confirmed |
| Execution | `cancel_appointment` succeeded with `patient_resolution_id`, `appointment_id`, `explicit_confirmation: true`, and `confirmation_text` |
| Database | Appointment status changed from `scheduled` to `cancelled` |
| Spoken result | Agent confirmed cancellation only after tool success |

### Manual checklist — future validation

| # | Scenario | Expected behavior |
|---|----------|-------------------|
| C1 | Cancellation with one appointment | Agent confirms the single appointment, asks explicit confirmation, then calls `cancel_appointment` after yes |
| C2 | Cancellation with multiple appointments | Agent lists options, caller chooses one, agent repeats summary, asks confirmation, then cancels after yes |
| C3 | Missing final confirmation | Agent asks cancellation confirmation but caller does not confirm → no `cancel_appointment` |
| C4 | Ownership mismatch | `cancel_appointment` returns `appointment_not_owned_by_patient`; agent does not claim cancellation succeeded |
| C5 | Already cancelled appointment | `cancel_appointment` returns `already_cancelled: true`; agent explains appointment was already cancelled |
| C6 | Post-cancellation lookup | After successful cancellation, `list_patient_appointments` no longer returns the cancelled appointment |
| C7 | Reschedule after cancellation | After cancellation, reschedule intent should list remaining appointments only |

---

## Appointment Hold TTL Smoke Tests

Holds are backend-owned and short-lived (default **300** seconds via `APPOINTMENT_HOLD_TTL_SECONDS`).

| # | Scenario | Expected behavior |
|---|----------|-------------------|
| H1 | Hold creation | `hold_appointment_slot` returns `expires_in_seconds` equal to backend-configured TTL |
| H2 | Retell cannot choose TTL | Legacy `ttl_seconds` in tool args, if present, is ignored; response TTL matches backend config |
| H3 | Hold blocks availability | Held slot excluded from `check_availability` while active |
| H4 | Expired hold | After expiry or call drop, slot returns to availability; booking/reschedule with expired hold fails safely |
| H5 | No hold renewal | Unrelated conversation turns (for example `check_availability`) do not extend hold TTL |

Appointment holds are intentionally short-lived coordination records, not durable reservations. No hold recovery is implemented in this slice.

---

## Scheduling Availability Hardening Smoke Tests

Run after [Retell Dashboard Setup](retell-dashboard-setup.md) with demo data seeded. These scenarios validate scheduling policy and Redis degradation behavior introduced in the availability hardening slice.

Configuration defaults (override in `.env` if needed):

```env
SCHEDULING_MIN_BOOKING_LEAD_MINUTES=60
SCHEDULING_BOOKING_HORIZON_DAYS=14
```

### A — Redis unavailable during availability lookup

| Step | Detail |
|------|--------|
| Setup | Stop Redis (`docker compose stop redis` or equivalent) |
| Action | Call `GET /api/v1/scheduling/doctors/{doctor_id}/availability` with a valid window, or run Retell `check_availability` |
| Expected | DB-backed available slots may still be returned when slots exist within policy window |
| Expected | No internal Redis or degradation details in API or Retell tool response |
| Expected | Request completes without server error |

### B — Redis unavailable during hold

| Step | Detail |
|------|--------|
| Setup | Redis stopped |
| Action | Attempt `POST /api/v1/scheduling/appointment-holds` or Retell `hold_appointment_slot` for a valid available slot |
| Expected | Hold fails with `appointment_hold_store_unavailable` (HTTP `503` on scheduling API; structured error on Retell) |
| Expected | Assistant does not claim the time was held |
| Expected | No appointment is created |

**Validated (June 2026):** With Redis offline, hold returned `appointment_hold_store_unavailable` and the assistant did not claim the time was held.

### C — Redis available during hold

| Step | Detail |
|------|--------|
| Setup | Start Redis |
| Action | Hold a valid available slot |
| Expected | Hold succeeds with `hold_id` and `expires_in_seconds` |
| Expected | Assistant may proceed toward identity and booking steps |

**Validated (June 2026):** With Redis online, hold creation succeeded again.

### D — Held slot exclusion

| Step | Detail |
|------|--------|
| Setup | Redis running; note a slot returned by availability |
| Action | Create a hold for that slot, then call availability again for the same window |
| Expected | Held slot is not offered while the hold is active |
| Expected | Other available slots in the window may still appear |

### E — Cancellation release interaction

| Step | Detail |
|------|--------|
| Setup | Booked appointment linked to an availability slot within lead/horizon policy |
| Action | Cancel the appointment with explicit confirmation |
| Expected | Appointment status becomes `cancelled` |
| Expected | Linked slot returns to `available` in PostgreSQL |
| Expected | Slot may appear in availability again if inside lead-time and horizon policy and not Redis-held |

---

## Known limitations

- **Appointment lookup** is supported via `list_patient_appointments` after patient identity resolution.
- **Cancellation execution** is supported via `cancel_appointment` after explicit confirmation.
- **Rescheduling execution** is supported via `reschedule_appointment` after explicit confirmation, hold on the new slot, and validated references from prior tool results.
- **Hold recovery after call drop** is not implemented; holds expire automatically by backend TTL policy.
- **Scheduling availability hardening** is implemented: minimum lead time, booking horizon, durable status filtering, and Redis-held slot exclusion when Redis is available.
- **Dynamic schedule rules** and rolling availability generation remain future work.
- **Rescheduling email notifications** may not be implemented in all deployments.
- **Redis is required** for hold creation, booking, and rescheduling; availability reads may degrade gracefully when Redis hold filtering is unavailable.

## Follow-up roadmap

1. **Patient-aware hold recovery** for authenticated patient sessions (separate from current voice flow).
2. **Dynamic schedules and rolling availability** — doctor-specific schedule rules, admin schedule management.
3. **Rescheduling and cancellation notification templates** where not yet deployed.

---

## Re-run checklist

After prompt or backend changes:

- [ ] Paste latest [v5 paste-ready prompt](retell-master-prompt-v5.md#paste-ready-retell-master-prompt) into Retell dashboard.
- [ ] Register `list_patient_appointments` and `reschedule_appointment` in Retell (ten tools total).
- [ ] Re-run scenarios 1–7, rescheduling checklist R1–R7, and cancellation checklist C1–C7 above; update **Observed status** if behavior changed.
- [ ] Review transcript for banned caller-facing terms (slot, UUID, patient not found, hold reference).
- [ ] Confirm `book_appointment`, `cancel_appointment`, and `reschedule_appointment` outcomes show `status: succeeded` only before spoken confirmation.
- [ ] Run [Scheduling Availability Hardening Smoke Tests](#scheduling-availability-hardening-smoke-tests) after Redis or scheduling policy changes.
- [ ] Log prompt version `retell-receptionist-v5` in deployment notes.

See also: [Retell Voice Smoke Scenarios](retell-voice-smoke-scenarios.md) for extended IR and recovery cases.
