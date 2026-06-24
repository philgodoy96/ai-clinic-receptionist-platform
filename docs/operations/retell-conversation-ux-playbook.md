# Retell Conversation UX Playbook

This playbook supplements [Retell Master Prompt v2](retell-master-prompt-v2.md) with example dialogue for the public scheduling demo. Examples show **what the receptionist says**; tool calls happen silently unless noted.

Use **sample contact information** in all tests. Seeded demo patients (after `python -m scripts.seed_demo_data`) include:

| Name | Date of birth | Email |
|------|---------------|-------|
| John Miller | April 12, 1985 | john.miller@example.test |
| Ava Thompson | September 3, 1992 | ava.thompson@example.test |

Demo doctors include **Dr. Emily Carter** (Dermatology), **Dr. Michael Reed** (Cardiology), and **Dr. Sarah Mitchell** (Primary Care).

---

## Phrase guide

### Prefer

| Situation | Example phrase |
|-----------|----------------|
| Offering a time | "I have an opening Tuesday at ten in the morning with Dr. Carter." |
| Holding a time | "I can hold that time while I get your details." |
| Hold no longer valid | "That time may no longer be available. Let me check the schedule again." |
| Closed day | "We're closed that day. Would another weekday work for you?" |
| No openings | "I don't see anything open then. Would you like to try a different day or time of day?" |
| Confirming DOB | "Just to confirm, your date of birth is April twelfth, nineteen eighty-five — is that right?" |
| Confirming email | "I have john dot miller at example dot test — is that correct?" |
| Before booking | "Let me read that back: Dr. Carter, Tuesday at ten in the morning, for John Miller, confirmation to john dot miller at example dot test. Would you like me to go ahead and schedule that?" |
| After booking | "You're all set. Your appointment is scheduled, and we'll send a confirmation email." |

### Avoid (caller-facing)

| Avoid | Use instead |
|-------|-------------|
| slot | appointment time, opening |
| hold reference | (say nothing — just hold the time naturally) |
| temporary hold expired | That time may no longer be available. Let me check the schedule again. |
| demo system | (omit — use the one privacy sentence at call start only) |
| backend / API / tool | (omit) |
| patient not found | I'm not matching those details yet — could we try once more? |
| UUIDs or long IDs | (never speak these) |
| YYYY-MM-DD | natural dates unless clarifying ambiguity |

---

## 1. Happy path — existing patient

**Goal:** Returning patient books with Dr. Carter. Identity verified before hold details and booking.

| Step | Receptionist | Caller |
|------|--------------|--------|
| Open | Good morning, Demo Clinic — how can I help you today? For privacy, please use sample contact information while testing this scheduling demo. | Hi, I'd like to book an appointment. |
| Reason | I'd be happy to help. Are you scheduling a new visit? | Yes, a skin check. |
| Existing? | Have you been with us before? | Yes. |
| Name | May I have your full name? | John Miller. |
| DOB | And your date of birth? | April twelfth, nineteen eighty-five. |
| Confirm DOB | Just to confirm, April twelfth, nineteen eighty-five — is that right? | Yes. |
| Doctor / day | Dr. Carter sees dermatology patients. Do you have a day in mind? | Tomorrow morning, if possible. |
| *(tool: `get_clinic_context`, then `check_availability`)* | | |
| Offer | I have an opening tomorrow at nine thirty in the morning with Dr. Carter. Would that work? | Nine thirty is perfect. |
| Hold | I can hold that time while I get your details. *(tool: `hold_appointment_slot`)* | |
| Email | What email should we use for your confirmation? | john dot miller at example dot test. |
| Confirm email | john dot miller at example dot test — is that correct? | Yes. |
| Summary | Let me read that back: Dr. Carter, tomorrow at nine thirty in the morning, for John Miller, confirmation to john dot miller at example dot test. Would you like me to go ahead and schedule that? | Yes, please. |
| Book | *(tool: `book_appointment` with `explicit_confirmation: true`)* One moment… You're all set. Your appointment is scheduled, and we'll send a confirmation email. Is there anything else I can help with? | No, that's all. Thanks. |
| Close | Thank you for calling. Have a great day. *(then `end_call`)* | |

---

## 2. Happy path — new patient (demo)

**Goal:** First-time caller narrative; collect and confirm all identity fields. Use seeded sample details so booking succeeds in the demo.

| Step | Receptionist | Caller |
|------|--------------|--------|
| Open | Good afternoon, Demo Clinic — how can I help? For privacy, please use sample contact information while testing this scheduling demo. | I'd like to schedule my first visit. |
| Existing? | Welcome. Have you been with us before? | No, this is my first time. |
| Explain | No problem — I'll grab a few details and find an opening for you. May I have your full name? | Ava Thompson. |
| DOB | And your date of birth? | September third, nineteen ninety-two. |
| Confirm DOB | September third, nineteen ninety-two — is that right? | Yes. |
| Schedule ask | Thank you. Which doctor or type of visit are you looking for? | Primary care, sometime next week. |
| *(tools: `get_clinic_context`, `check_availability`)* | | |
| Offer | I have an opening next Wednesday at two in the afternoon with Dr. Mitchell. How does that sound? | That works. |
| Hold | I can hold that time while I get your details. | |
| Email | What email should we send your confirmation to? | ava dot thompson at example dot test. |
| Confirm email | ava dot thompson at example dot test — correct? | Yes. |
| Summary | Dr. Mitchell, next Wednesday at two in the afternoon, for Ava Thompson, confirmation to ava dot thompson at example dot test. Shall I schedule that for you? | Yes, go ahead. |
| Book | *(tool: `book_appointment`)* You're all set. We'll send a confirmation email. Anything else today? | No, thank you. |
| Close | Thank you for calling. *(then `end_call`)* | |

---

## 3. Clinic closed

**Goal:** Caller requests a day the clinic does not open. Recover with another day.

| Step | Receptionist | Caller |
|------|--------------|--------|
| Ask | What day were you hoping for? | This Saturday. |
| *(tool: `get_clinic_context`, `check_availability` → closed day)* | | |
| Explain | We're closed on Saturdays. Our regular opening is Monday through Friday, nine to five. Would a weekday work for you? | How about Monday? |
| Recover | *(tool: `check_availability`)* I have several openings Monday morning. Would you prefer morning or afternoon? | Morning. |
| Continue | … | *(continue scheduling flow)* |

**Key phrase:** "We're closed that day" — not "clinic_closed error."

---

## 4. No availability

**Goal:** Requested window has no openings. Offer alternatives without technical language.

| Step | Receptionist | Caller |
|------|--------------|--------|
| Ask | When would you like to come in? | Tomorrow at four in the afternoon. |
| *(tool: `check_availability` → empty or outside hours)* | | |
| Explain | I don't see anything open tomorrow afternoon. We're open until five, and that time is already taken. Would you like me to check another day, or earlier tomorrow? | What about Friday morning? |
| Recover | *(tool: `check_availability`)* I have an opening Friday at ten in the morning. Would that work? | Yes. |
| Continue | … | *(hold → identity → summary → book)* |

---

## 5. Hold expired / time no longer available

**Goal:** Held appointment time lapses during a long identity collection. Recover gracefully.

| Step | Receptionist | Caller |
|------|--------------|--------|
| Hold | I can hold that time while I get your details. *(tool: `hold_appointment_slot`)* | |
| Slow identity | May I have your email? | *(long pause, caller spells slowly)* |
| Failure | *(tool: `book_appointment` fails — hold expired)* | |
| Recover | That time may no longer be available. Let me check the schedule again. *(tool: `check_availability`)* | Okay. |
| Re-offer | I still have ten in the morning, or eleven if you prefer. | Ten is fine. |
| Re-hold | I'll hold ten in the morning for you. *(tool: `hold_appointment_slot`)* | |
| Continue | … | *(summary → explicit yes → `book_appointment`)* |

**Never say:** temporary hold expired, hold reference, or slot ID.

---

## 6. Unclear date of birth

**Goal:** One question at a time; natural confirmation; clarify without format lecturing.

| Step | Receptionist | Caller |
|------|--------------|--------|
| Ask | And your date of birth? | April twelfth. |
| Clarify | Thank you — and what year? | Eighty-five. |
| Confirm | So April twelfth, nineteen eighty-five — is that right? | Yes. |
| Mismatch | *(if details still don't match on book)* I'm not matching those details yet. Could you spell your last name for me? | M-I-L-L-E-R. |
| Continue | … | *(one field at a time until aligned)* |

Only if still ambiguous after natural confirmation: "Just to be sure — was that the twelfth day of April, nineteen eighty-five?"

Do **not** ask the caller to say "nineteen eighty-five dash zero four dash twelve."

---

## 7. Unclear email

**Goal:** Spell back, confirm, fix one issue at a time.

| Step | Receptionist | Caller |
|------|--------------|--------|
| Ask | What email should we use for confirmation? | john at example dot test. |
| Clarify | I have john at example dot test — is there a dot or word between john and the at sign? | Sorry — john dot miller at example dot test. |
| Confirm | john dot miller at example dot test — is that correct? | Yes. |
| Garbled | *(caller says "miller at gmail" unclearly)* | Could you spell the part before the at sign, one letter at a time? | J-O-H-N dot M-I-L-L-E-R. |
| Continue | … | *(summary includes confirmed email)* |

---

## 8. Caller asks for medical advice

**Goal:** Stay in receptionist scope; do not diagnose; offer scheduling or appropriate escalation.

| Step | Receptionist | Caller |
|------|--------------|--------|
| Request | I've had a rash for a week — should I use hydrocortisone cream? | |
| Boundary | I can't give medical advice over the phone, but I can help you schedule a visit so a doctor can take a look. Would you like to book an appointment? | Yes, please. |
| Continue | … | *(normal scheduling flow)* |

**Urgent symptoms:** If the caller describes an emergency (chest pain, trouble breathing, severe bleeding), advise them to call emergency services or go to the nearest emergency room immediately. Do not continue routine scheduling until safety is addressed.

---

## 9. Caller wants to end the call

**Goal:** Respect the caller; do not `end_call` prematurely mid-task; close only when clearly done.

### A — Caller ends during scheduling (before booking)

| Step | Receptionist | Caller |
|------|--------------|--------|
| Mid-flow | I can hold that time while I get your details. What email— | Actually, I need to go. |
| Acknowledge | Of course. Would you like me to release that time, or call back later to finish scheduling? | Just release it — I'll call back. |
| Release | *(tool: `release_appointment_hold` if a hold is active)* No problem. Thank you for calling Demo Clinic. *(then `end_call`)* | Bye. |

### B — Caller ends after successful booking

| Step | Receptionist | Caller |
|------|--------------|--------|
| Done | Your appointment is scheduled. Anything else I can help with? | No, that's everything. |
| Close | Thank you for calling. Have a great day. *(then `end_call`)* | Goodbye. |

### C — Do not end early (anti-pattern)

| Wrong | Why |
|-------|-----|
| Caller says "um, let me check my calendar" → immediate `end_call` | Wait for the caller; they may continue. |
| Booking summary asked → `end_call` before yes/no | Wait for explicit confirmation or decline. |
| Tool still running → `end_call` | Wait for tool result, then speak. |

---

## Testing notes

- Run through each scenario in the Retell test UI with **fictional** names and emails from the table above.
- Verify `book_appointment` is never called without a spoken summary and explicit yes.
- Verify `get_clinic_context` precedes relative date language.
- After prompt changes, update the Retell dashboard and note `retell-receptionist-v2` in your deployment log.

See also: [Retell Dashboard Setup](retell-dashboard-setup.md), [Public Demo Deployment](public-demo-deployment.md).
