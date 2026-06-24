# Retell Voice Smoke Scenarios

Realistic end-to-end smoke tests for the public scheduling voice demo. Run after [Retell Dashboard Setup](retell-dashboard-setup.md) and with demo data seeded (`python -m scripts.seed_demo_data`).

Companion docs:

- [Retell Master Prompt v2](retell-master-prompt-v2.md) — agent prompt
- [Retell Tool Descriptions](retell-tool-descriptions.md) — tool contracts and recovery
- [Retell Conversation UX Playbook](retell-conversation-ux-playbook.md) — dialogue examples

Use **fictional sample contact information** only. Default clinic hours: Monday–Friday, 9:00–17:00 clinic local (`America/New_York`).

---

## Preconditions

| Check | How |
|-------|-----|
| API healthy | `GET /health` → 200 |
| Retell enabled | `RETELL_ENABLED=true`, webhooks verified |
| Demo data | `python -m scripts.seed_demo_data` |
| Agent prompt | [Retell Master Prompt v2](retell-master-prompt-v2.md) published |
| Tools registered | All 9 tools per [tool descriptions](retell-tool-descriptions.md) |

Seeded patients:

| Name | DOB (spoken) | Email | Tool `patient_date_of_birth` |
|------|--------------|-------|------------------------------|
| John Miller | April 12, 1985 | john.miller@example.test | `1985-04-12` |
| Ava Thompson | September 3, 1992 | ava.thompson@example.test | `1992-09-03` |
| Michael Lee Reed | March 15, 1988 | michael.lee.reed@example.test | `1988-03-15` |

Automated coverage for identity-resolution flows lives in `tests/test_voice_identity_resolution_flow.py`.

---

## Patient identity resolution flows

These scenarios exercise `resolve_patient_identity`, `confirm_patient_identity`, and `book_appointment` with `patient_resolution_id`. Run after identity tools are registered and `VOICE_PATIENT_INTAKE_MODE=demo_auto_create` for new-patient paths.

### IR-1 — Existing exact patient

**Purpose:** Returning patient with full seeded name + DOB resolves cleanly and books via token.

#### Caller script

1. "I've been here before."
2. Name: **John Miller**; DOB: **April twelfth, nineteen eighty-five**.
3. Complete hold → summary → **"Yes, please schedule that."**

#### Expected tool sequence

1. `hold_appointment_slot`
2. `resolve_patient_identity` with name + DOB → `match_status: exact_match`, `patient_resolution_id` present
3. `book_appointment` with `patient_resolution_id`, `explicit_confirmation: true`

#### Pass criteria

- [ ] `resolve_patient_identity` → `exact_match`, no extra identity confirmation beyond normal summary.
- [ ] `book_appointment` → `status: succeeded`.
- [ ] No `patient_id` in tool results.

---

### IR-2 — Existing possible match (Michael Reed)

**Purpose:** Shortened spoken name triggers confirmation before booking.

#### Setup

Database has **Michael Lee Reed** (DOB March 15, 1988).

#### Caller script

1. Name: **Michael Reed**; same DOB; email **michael dot lee dot reed at example dot test**.
2. When agent asks: **"I have Michael Lee Reed, born in March 1988 — is that you?"** → **"Yes."**
3. Complete booking summary → yes.

#### Expected tool sequence

1. `resolve_patient_identity` → `possible_match`, `requires_confirmation: true`, `confirmation_question` mentions **Michael Lee Reed** (not raw email).
2. `confirm_patient_identity` with `confirmed: true` → `confirmed: true` in result.
3. `book_appointment` with same `patient_resolution_id` → `status: succeeded`.

#### Pass criteria

- [ ] Agent uses natural confirmation question from tool result (or equivalent wording).
- [ ] `book_appointment` **not** called before `confirm_patient_identity` succeeds.
- [ ] Unconfirmed token → `patient_identity_confirmation_required` if booking attempted early.

---

### IR-3 — Rejected possible match

**Purpose:** Caller denies candidate profile; agent recovers without booking wrong patient.

#### Caller script

1. Same as IR-2 through `possible_match`.
2. When asked "Is that you?" → **"No."**

#### Expected tool sequence

1. `resolve_patient_identity` → `possible_match`
2. `confirm_patient_identity` with `confirmed: false` → `status: rejected`, `error_code: patient_identity_confirmation_rejected`

#### Pass criteria

- [ ] Agent does **not** call `book_appointment` after rejection.
- [ ] Agent asks for email/phone **or** offers new-patient path with caller-provided email.
- [ ] Suggested recovery follows `suggested_response_text` from tool result.

---

### IR-4 — Multiple matches

**Purpose:** Ambiguous name + DOB requires email or phone before booking.

#### Setup

Two **Sarah Chen** patients share DOB July 22, 1990 (different emails in seed data).

#### Caller script

1. Name **Sarah Chen**; DOB **July twenty-second, nineteen ninety** (no email yet).
2. When asked for email → **sarah dot chen at example dot test**.

#### Expected tool sequence

1. `resolve_patient_identity` (name + DOB only) → `multiple_matches`, `next_step: ask_email_or_phone`, **no** `patient_resolution_id`.
2. `resolve_patient_identity` (with email) → `exact_match`, `patient_resolution_id` present.

#### Pass criteria

- [ ] Agent asks for **one** discriminant (email or phone) — not both at once.
- [ ] Tool results never expose stored email/phone before caller provides it.

---

### IR-5 — New demo patient

**Purpose:** First-time caller with demo auto-create enabled.

#### Caller script

1. "I'm a new patient."
2. Name: **Felipe Logan**; DOB: **November second, nineteen ninety-five**.
3. Email: **felipe dot logan at example dot test** — confirm yes.
4. Hold → summary → yes.

#### Expected tool sequence

1. `resolve_patient_identity` with `caller_claims_existing_patient: false`, `allow_demo_patient_creation: true` → `created`
2. `book_appointment` with `patient_resolution_id` → `status: succeeded`

#### Pass criteria

- [ ] Demo patient row created without invented phone.
- [ ] Booking succeeds with resolution token.
- [ ] Non-real email invented by agent → booking must not proceed.

---

### IR-6 — Invented email guard (manual + automated)

**Purpose:** Agent must never book before caller speaks and confirms email.

#### Prompt rule

Master prompt prohibits inventing email or calling `book_appointment` immediately after asking for email.

#### Manual validation

1. Start booking; reach email collection question.
2. **Do not** speak an email — pause.
3. Verify agent does **not** call `book_appointment` with a fabricated address.

#### Automated guard (CI)

`tests/test_voice_identity_resolution_flow.py::test_invented_email_guard_rejects_booking_before_caller_confirms_email` verifies inline `book_appointment` without prior `resolve_patient_identity` fails with `patient_not_found` when no matching patient exists.

#### Pass criteria

- [ ] No `book_appointment` until caller provides and confirms email.
- [ ] Never use placeholder or agent-invented `.test` addresses.

---

## Scenario 1 — Existing seeded patient booking

**Purpose:** Verify returning-patient flow, hold → identity resolution → summary → book.

### Caller script

1. "I'd like to book a dermatology appointment."
2. "Yes, I've been here before."
3. Name: **John Miller**; DOB: **April twelfth, nineteen eighty-five**.
4. "Tomorrow morning with Dr. Carter."
5. Accept first offered morning time.
6. Email: **john dot miller at example dot test** — confirm yes.
7. When summarized, say **"Yes, please schedule that."**

### Expected tool sequence

1. `get_clinic_context`
2. `check_availability` with `date_expression: { "kind": "tomorrow" }`, `time_window_expression: { "kind": "morning" }`, `doctor_name: "Dr. Emily Carter"`
3. `hold_appointment_slot` with chosen `availability_slot_id`
4. `resolve_patient_identity` → `exact_match`
5. `book_appointment` with `patient_resolution_id`, `explicit_confirmation: true`

### Pass criteria

- [ ] Receptionist says "I can hold that time while I get your details" (not "hold slot").
- [ ] Full summary read back before `book_appointment`.
- [ ] `resolve_patient_identity` returns `exact_match` before booking.
- [ ] `book_appointment` returns `status: succeeded`.
- [ ] Receptionist confirms booking only after success.
- [ ] `end_call` only after caller indicates they are done.

### Fail signals

- Booking announced before tool success.
- `book_appointment` called without `explicit_confirmation: true`.
- Invented email not spoken by caller.
- `patient_not_found` spoken to caller.

---

## Scenario 2 — New demo patient booking

**Purpose:** First-visit narrative with full identity collection.

### Caller script

1. "I'd like to schedule my first visit."
2. "No, I haven't been here before."
3. Name: **Ava Thompson**; DOB: **September third, nineteen ninety-two**.
4. "Primary care next week."
5. Accept offered time with Dr. Mitchell.
6. Email: **ava dot thompson at example dot test** — confirm yes.
7. **"Yes, go ahead and schedule it."**

### Expected tool sequence

1. `hold_appointment_slot`
2. `resolve_patient_identity` with `allow_demo_patient_creation: true` → `created` (or `exact_match` on retry)
3. `book_appointment` with `patient_resolution_id`, `explicit_confirmation: true`

### Pass criteria

- [ ] One question at a time during identity collection.
- [ ] DOB and email confirmed naturally before booking.
- [ ] `book_appointment` → `status: succeeded`.
- [ ] Privacy sentence spoken once at call start only.

---

## Scenario 3 — Saturday / Sunday closed

**Purpose:** `clinic_closed` recovery without technical language.

### Caller script

1. "I need an appointment this Saturday."
2. When offered weekdays, "Monday morning works."

### Expected tool sequence

1. `get_clinic_context`
2. `check_availability` with `date_expression: { "kind": "this_weekday", "weekday": "saturday" }` → `clinic_closed` **or** empty/closed handling
3. `check_availability` for Monday morning → openings returned

### Pass criteria

- [ ] Receptionist says clinic is closed Saturday (not "clinic_closed").
- [ ] Offers weekday alternative.
- [ ] Does not invent Saturday times.
- [ ] Successfully continues scheduling on Monday if caller agrees.

---

## Scenario 4 — Unavailable exact time

**Purpose:** Requested time not open; agent offers alternatives.

### Caller script

1. Book flow through doctor and day selection.
2. "Tomorrow at four thirty in the afternoon." *(or another time likely outside openings / after last slot)*

### Expected tool sequence

1. `get_clinic_context`
2. `check_availability` with `time_window_expression: { "kind": "exact_time", "exact_time": "16:30" }` → empty slots or `outside_business_hours`

### Pass criteria

- [ ] Receptionist does not claim the unavailable time is open.
- [ ] Offers another day or time window.
- [ ] Never says "slot" to the caller.

---

## Scenario 5 — Hold expired

**Purpose:** Recovery when hold lapses before booking.

### Simulation options

**A — Natural delay:** During identity collection, pause longer than the hold TTL (default several minutes; use test env with shorter TTL if available).

**B — Manual:** After hold, wait for Redis TTL expiry before `book_appointment`.

### Caller script

1. Complete flow through hold.
2. Stall during email collection ("Give me a moment…").
3. When agent recovers, accept a new offered time and complete booking.

### Expected tool sequence

1. `hold_appointment_slot` → success
2. `book_appointment` → `appointment_hold_expired`
3. `check_availability` → new openings
4. `hold_appointment_slot` → new hold
5. `book_appointment` → `status: succeeded`

### Pass criteria

- [ ] Receptionist says: "That time may no longer be available. Let me check the schedule again."
- [ ] Does **not** say "temporary hold expired" or "hold reference."
- [ ] Re-holds and completes booking after caller re-confirms.

---

## Scenario 6 — Unclear date of birth

**Purpose:** Clarify DOB one field at a time without `YYYY-MM-DD` lecturing.

### Caller script

1. When asked for DOB: "April twelfth."
2. When asked for year: "Nineteen eighty-five."
3. Confirm when read back.

### Pass criteria

- [ ] Agent asks for missing year (one question).
- [ ] Confirms full DOB in natural language.
- [ ] `book_appointment` uses `patient_date_of_birth: "1985-04-12"` in tool args without requiring caller to speak ISO format.

---

## Scenario 7 — Unclear email

**Purpose:** Spell-back and correction.

### Caller script

1. When asked for email: "john at example dot test."
2. When clarified: "john dot miller at example dot test."
3. Confirm yes.

### Pass criteria

- [ ] Agent asks for clarification on incomplete email.
- [ ] Reads back full email before summary.
- [ ] `book_appointment` uses confirmed email only — not invented.

---

## Scenario 8 — No premature `end_call`

**Purpose:** Call stays open during active scheduling.

### Caller script

1. Start booking flow through availability offer.
2. Say "Um, let me check my calendar…" and pause 5–10 seconds.
3. Continue and complete booking.

### Pass criteria

- [ ] No `end_call` during pause.
- [ ] No `end_call` between summary question and caller's yes.
- [ ] No `end_call` while a tool request is in flight.
- [ ] `end_call` only after "Anything else?" and caller declines.

### Fail signals

- Call drops while caller is silent but still engaged.
- Call ends before booking confirmation question is answered.

---

## Scenario 9 — Duplicate / retry behavior

**Purpose:** Retell retry of `book_appointment` does not double-book.

### Setup

Complete Scenario 1 through successful `book_appointment`. Observe backend logs or DB for a single appointment.

### Simulation

If testing via API: send the same `book_appointment` payload twice with identical `tool_call_id` and `provider_call_id`.

### Expected backend behavior

- First call: `status: succeeded`, `duplicate: false`
- Second call: `status: succeeded`, `duplicate: true`
- One appointment row; one confirmation email job (idempotent)

### Pass criteria (voice)

- [ ] Receptionist does not tell caller two appointments were created.
- [ ] On retry, agent may briefly acknowledge success once — no panic apology.

---

## Scenario 10 — Provider `tool_call_id` behavior

**Purpose:** Verify idempotency keying per Retell tool invocation.

### What to verify

| Tool type | `tool_call_id` present | Expected behavior |
|-----------|------------------------|-------------------|
| `book_appointment` | Yes, same on retry | Stored outcome replayed, `duplicate: true` |
| `resolve_patient_identity` | Yes, same on retry | Stored outcome replayed, `duplicate: true` |
| `confirm_patient_identity` | Yes, same on retry | Stored outcome replayed, `duplicate: true` |
| `cancel_appointment` | Yes, same on retry | No double cancellation |
| `reschedule_appointment` | Yes, same on retry | No double reschedule |
| `hold_appointment_slot` | Yes, same on retry | Recorded outcome replayed if side-effect path stored |
| `get_clinic_context` | Any | Safe to repeat; read-only |
| `check_availability` | Any | Safe to repeat; read-only |

### Inspection

- Check `VoiceCallEvent` / tool outcome records for matching `tool_call_id`.
- Idempotency key format: `retell:{provider_call_id}:tool:{tool_name}:{tool_call_id}`.

### Pass criteria

- [ ] Retell dashboard uses default envelope so `tool_call_id` is forwarded.
- [ ] Duplicate side-effect callbacks return `duplicate: true` without new side effects.

---

## Quick smoke matrix

| # | Scenario | Key tools | Must-pass phrase / behavior |
|---|----------|-----------|----------------------------|
| IR-1 | Exact patient identity | resolve → book | `exact_match` then succeeded book |
| IR-2 | Possible match | resolve → confirm → book | Natural "is that you?" confirm |
| IR-3 | Rejected match | resolve → confirm (no) | Retry identity, no book |
| IR-4 | Multiple matches | resolve → resolve+email | Ask for email once |
| IR-5 | New demo patient | resolve (created) → book | No invented phone |
| IR-6 | Invented email guard | — | No book before email confirmed |
| 1 | Existing patient | hold → resolve → book | Hold that time; summary before book |
| 2 | New patient | hold → book | One question at a time |
| 3 | Weekend closed | check_availability | "We're closed that day" |
| 4 | No exact time | check_availability | Offer alternative |
| 5 | Hold expired | book → check → hold → book | "Let me check the schedule again" |
| 6 | Unclear DOB | book | Natural DOB confirm |
| 7 | Unclear email | book | Spell-back email |
| 8 | No early end | — | `end_call` only when done |
| 9 | Duplicate book | book_appointment | `duplicate: true`, one appointment |
| 10 | tool_call_id | side-effect tools | Idempotent retries |

---

## After smoke tests

- [ ] Review Retell call transcript for banned words: slot, hold reference, demo system, patient not found, UUID.
- [ ] Confirm lifecycle webhook created `VoiceCall` for `call_id`.
- [ ] Log prompt version `retell-receptionist-v2` in deployment notes.
- [ ] File issues against prompt or tool descriptions if recovery language drifted.

See also: [Retell Dashboard Setup — Smoke Test Checklist](retell-dashboard-setup.md#smoke-test-checklist).
