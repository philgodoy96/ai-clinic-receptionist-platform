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
| Tools registered | All 7 tools per [tool descriptions](retell-tool-descriptions.md) |

Seeded patients:

| Name | DOB (spoken) | Email | Tool `patient_date_of_birth` |
|------|--------------|-------|------------------------------|
| John Miller | April 12, 1985 | john.miller@example.test | `1985-04-12` |
| Ava Thompson | September 3, 1992 | ava.thompson@example.test | `1992-09-03` |

---

## Scenario 1 — Existing seeded patient booking

**Purpose:** Verify returning-patient flow, hold → identity → summary → book.

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
4. `book_appointment` with `explicit_confirmation: true`, seeded identity

### Pass criteria

- [ ] Receptionist says "I can hold that time while I get your details" (not "hold slot").
- [ ] Full summary read back before `book_appointment`.
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

Same pattern as Scenario 1 with Ava's identity on `book_appointment`.

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
| 1 | Existing patient | hold → book | Hold that time; summary before book |
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
