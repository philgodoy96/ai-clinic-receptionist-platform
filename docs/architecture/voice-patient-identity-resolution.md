# Voice Patient Identity Resolution

## Context

Voice booking requires linking a caller to a durable `Patient` record before `book_appointment` can succeed.

Callers speak names, dates, and contact details imperfectly. ASR and the voice agent may normalize or mis-hear fields. The demo database contains seeded patients with sample `.test` emails, and public demo mode may auto-create minimal patients when policy allows.

Today, patient resolution runs inline inside `VoiceBookingConfirmationService` through `PatientIntakeService.resolve_for_voice_booking`. That path supports exact lookup (`lookup_only`) or demo-only auto-create (`demo_auto_create`). It does not yet expose structured ambiguity outcomes to the Retell agent.

This document defines the target identity-resolution design for the voice channel: a dedicated resolution step that returns explicit outcomes, optional caller confirmation, and an opaque `patient_resolution_id` that `book_appointment` can prefer over re-parsed identity fields.

See also:

- [Scheduling Application Services](scheduling-services.md) — patient identity rule (name + DOB is insufficient alone)
- [Retell Voice Booking Confirmation](retell-voice-booking-confirmation.md) — current booking gate
- [Public Demo Guardrails](public-demo-guardrails.md) — demo bounds
- [Voice Patient Identity Resolution (operations)](../operations/voice-patient-identity-resolution.md) — agent UX playbook
- [Retell Master Prompt v2](../operations/retell-master-prompt-v2.md) — caller-facing identity collection

## Design Principle

The voice agent collects candidates.

The backend resolves identity.

Booking consumes a confirmed resolution, not raw spoken fields alone.

Identity resolution is a read-only (or demo-bounded create) step. It must complete before `book_appointment`. The agent must not treat a successful hold as proof of patient identity.

## Why Identity Resolution Is Needed

Voice scheduling has three distinct problems that a single `book_appointment` call cannot solve well:

1. **Ambiguity** — Two patients may share a date of birth, a similar name, or both. A blind exact match fails or risks attaching the wrong record.
2. **Progressive collection** — The agent should ask one question at a time, confirm natural-language answers, and only book after a final summary. Resolution outcomes tell the agent whether to confirm, narrow, create, or retry.
3. **Separation of concerns** — `book_appointment` already validates holds, explicit confirmation, quotas, and idempotency. Patient matching deserves its own contract so booking does not re-implement fuzzy logic on every retry.

Without a structured resolution step, the agent either books against brittle exact-match rules or invents recovery behavior when lookup fails.

## Resolution Outcomes

The `resolve_patient_identity` tool (or equivalent internal service step) returns exactly one primary outcome:

| Outcome | Meaning | Agent next step |
|---------|---------|-----------------|
| `exact_match` | Exactly one patient matches all validated fields after normalization | Proceed toward final booking summary; no extra identity confirmation beyond normal DOB/email read-back |
| `possible_match` | One likely patient, but a field is weak or asymmetric (for example spoken name omits middle name) | Ask a natural confirmation question before treating identity as settled |
| `multiple_matches` | More than one patient matches the current field set | Narrow with email or phone; ask one clarifying question at a time |
| `not_found` | No patient matches; creation not allowed or not attempted | Retry identity collection or explain demo limits; do not call `book_appointment` |
| `created` | Demo policy created a new minimal patient (`demo_auto_create` + sample `.test` email only) | Proceed toward booking; resolution is already bound to the new record |

Each successful resolution (except `not_found`) should return:

- `patient_resolution_id` — opaque server-issued token
- Safe display fields for confirmation (for example first name, month/year of birth) — never full PHI in logs
- `outcome` — one of the values above

`not_found` must not return a `patient_resolution_id`.

## Why Date of Birth Alone Is Not Enough

Date of birth is a weak unique key in any real clinic population:

- Many patients share the same DOB (twins, common birth dates, data-entry collisions).
- DOB is often spoken ambiguously over voice ("April twelfth" vs "April twenty-first").
- DOB does not prove the caller owns the record; it only narrows candidates.

The scheduling domain already requires **full name + date of birth + at least one of email or phone** for lookup (`SchedulingService` / `PatientRepository.get_by_identity`). Voice resolution preserves and extends that rule:

- Email and phone are stronger contact anchors (unique in the demo schema).
- Name + DOB alone may surface `multiple_matches` or `possible_match`, never a silent `exact_match` for booking.

Resolution must not downgrade to DOB-only matching to "make the demo work."

## Why Possible Matches Require Confirmation

A `possible_match` means the backend found one best candidate, but confidence is not high enough to bind booking without caller affirmation.

Typical causes:

- Spoken name is a subset or superset of the chart name (see Michael Reed example below).
- Email matches but name differs slightly after normalization.
- Phone digits match but name or DOB was mis-heard.

Booking the wrong patient is worse than asking one more question. The agent should confirm with natural language:

> "I have Michael Lee Reed, born in March 1988 — is that you?"

Only after an affirmative answer should the backend issue or refresh a `patient_resolution_id` with a confirmed flag. Unconfirmed `possible_match` tokens must not satisfy `book_appointment`.

## Why `patient_resolution_id` Should Be Opaque

`patient_resolution_id` is not the patient UUID and must not be guessable from it.

Reasons:

1. **Channel safety** — The voice agent and Retell logs should not carry durable patient primary keys. An opaque resolution token limits blast radius if a callback is logged or replayed.
2. **Call-scoped intent** — The token represents "this caller, on this voice call, at this point in the conversation, resolved to this candidate (optionally confirmed)." It can expire, be superseded, or require re-confirmation without mutating the patient row.
3. **Stable booking handoff** — After confirmation, `book_appointment` references the token instead of re-parsing fields the agent might mis-transcribe on retry.
4. **Audit without PHI** — Audit metadata can record resolution outcome and token id, not raw email or DOB strings.

Implementation sketch: server-side store keyed by random id (UUID v4 or similar), linked to `provider_call_id`, candidate `patient_id`, outcome, confirmation state, and TTL.

## Why `book_appointment` Should Prefer `patient_resolution_id`

When `patient_resolution_id` is present and valid:

1. Load the resolution record for the current voice call.
2. Verify outcome is `exact_match`, `created`, or confirmed `possible_match`.
3. Use the bound `patient_id` for `AppointmentBookingService`.

Inline `patient_name` / `patient_date_of_birth` / `patient_email` fields become fallback for backward compatibility, not the primary path.

Benefits:

- **Idempotent retries** — Provider retries of `book_appointment` reuse the same resolved patient instead of re-matching on slightly different strings.
- **Single source of truth** — Identity is resolved once, confirmed in conversation, then frozen for booking.
- **Agent discipline** — The prompt can require resolution success before collecting final booking confirmation, reducing race conditions between identity tools and booking.

If the token is missing, expired, belongs to another call, or is unconfirmed `possible_match`, `book_appointment` fails with a safe error (for example `booking_identity_missing` or a dedicated resolution error) and the agent re-runs resolution.

## Demo-Safe, Not Production-Grade Verification

This design is appropriate for a **public fictional-clinic demo**, not for clinical or legal identity proof.

What it does:

- Reduces accidental wrong-patient booking in the demo dataset.
- Bounds auto-create to sample `.test` emails in `demo_auto_create` mode.
- Keeps stronger identifiers (email unique, phone unique) in the matching path.
- Avoids storing raw transcripts for verification.

What it does **not** do:

- Verify government ID, insurance card, or OTP to email/SMS.
- Integrate with an MPI, HL7 ADT, or EHR master patient index.
- Prevent a determined caller from claiming a seeded demo identity if they know the sample email and DOB.
- Provide HIPAA-grade authentication.

Production would require out-of-band verification, consent, break-glass procedures, and integration with authoritative patient sources. This slice intentionally stops at conversational confirmation plus backend matching rules.

## Examples

### Michael Reed vs Michael Lee Reed

**Database:** `Michael Lee Reed`, DOB `1988-03-15`, email `michael.lee.reed@example.test`

**Caller says:** "Michael Reed," same DOB, same email.

**Resolution:** `possible_match` — spoken name omits the middle name and surname segment present on chart.

**Agent:** "I have Michael Lee Reed born in March 1988 — is that you?"

**After yes:** backend marks resolution confirmed and returns `patient_resolution_id`.

**Wrong path:** treating as `exact_match` on first name + DOB only would eventually collide with another `Michael Reed` sharing the DOB.

Note: seeded demo doctors include **Dr. Michael Reed** (cardiology). Patient resolution must never conflate staff directory names with patient records; doctor lookup tools and patient resolution are separate domains.

### Multiple Patients With the Same DOB

**Database:**

- `Sarah Chen`, DOB `1990-07-22`, email `sarah.chen@example.test`
- `Sarah Chen`, DOB `1990-07-22`, email `sarah.chen.work@example.test`

**Caller provides:** name + DOB only.

**Resolution:** `multiple_matches` — two rows share normalized name and DOB.

**Agent:** "Which email do you have on file with us?" (one question)

**Caller provides email.**

**Resolution:** `exact_match` + `patient_resolution_id`.

**Invariant:** DOB alone cannot disambiguate; email (or phone) is required.

### New Patient Creation (Demo)

**Mode:** `VOICE_PATIENT_INTAKE_MODE=demo_auto_create`

**Caller provides:** `Felipe Logan`, DOB `1995-11-02`, email `felipe.logan@example.test`, no phone.

**Resolution:** no row found → policy allows create → `created` with new `patient_resolution_id`.

**Service behavior (current `PatientIntakeService`):** normalizes name/email, does not invent phone, idempotent on retry by email.

**Rejected path:** `felipe.logan@example.com` → `not_found` (non-`.test` domain blocked in demo auto-create).

### Existing Patient Lookup by Email or Phone

**Database:** `John Miller`, DOB `1985-04-12`, phone `+1-555-0201`, email `john.miller@example.test`

**Caller provides:** full name, DOB, email `JOHN.MILLER@EXAMPLE.TEST` (case differs).

**Resolution:** `exact_match` after normalization.

**Alternate path:** caller gives phone instead of email; `get_by_identity` matches on normalized phone digits when email is also supplied or as the second factor after name + DOB.

**Lookup-only mode:** if no row matches, `not_found` — no create.

## Current Implementation

The following exists today:

- `PatientIdentityResolutionService` with structured outcomes
- `resolve_patient_identity` and `confirm_patient_identity` Retell tools
- Opaque `patient_resolution_id` store in Redis with TTL
- `book_appointment` prefers `patient_resolution_id` when provided, with inline identity fallback
- `PatientIntakeService` with `lookup_only` and `demo_auto_create` modes for fallback path
- `VOICE_PATIENT_INTAKE_MODE` configuration

## Target Flow

```
Caller provides identity fields (one question at a time)
  -> resolve_patient_identity
      -> exact_match | possible_match | multiple_matches | not_found | created
  -> (if possible_match) caller confirms
  -> hold_appointment_slot (if not already held)
  -> final spoken summary + explicit yes
  -> book_appointment(patient_resolution_id=..., explicit_confirmation=true)
      -> validate resolution + hold + quotas
      -> AppointmentBookingService
```

Resolution may be retried freely; it must not create appointments or send email.

## Invariants

- Resolution never books appointments, sends email, or releases holds.
- `exact_match` requires a single patient after normalization and configured matching rules.
- `possible_match` never satisfies booking without caller confirmation recorded server-side.
- `multiple_matches` never returns a single patient id without an additional discriminant (email or phone).
- `created` only occurs when `demo_auto_create` policy allows and email domain is an approved sample domain (`.test`).
- `not_found` in `lookup_only` mode is final for that field set until the caller changes input.
- Email and phone are never invented by the agent or backend.
- Resolution records are scoped to `provider_call_id` (or equivalent voice owner); cross-call reuse is rejected.
- Opaque resolution ids must not embed or leak patient UUIDs.
- Audit and provider logs store outcome codes and resolution ids, not raw email, phone, or full DOB strings.

## Failure Modes

| Failure | Cause | Safe behavior |
|---------|-------|---------------|
| `insufficient_patient_identity` | Missing name or email (and policy requires them) | Agent collects missing field; no resolution id issued |
| `not_found` | No match; create disallowed | Agent re-collects or explains demo sample data; no booking |
| `multiple_matches` | Ambiguous candidate set | Agent asks for email or phone once; re-resolves |
| Unconfirmed `possible_match` | Booking attempted without confirmation | Reject booking; agent confirms identity first |
| Expired `patient_resolution_id` | TTL elapsed or hold outlived resolution | Re-resolve; may need to re-confirm |
| Resolution owner mismatch | Token from another call | Reject booking; re-resolve on current call |
| Demo domain rejected | Auto-create with non-`.test` email | `not_found`; agent guides toward sample email |
| Integrity collision on create | Concurrent demo creates same email/phone | Idempotent recover existing row if identity matches; else `not_found` |
| `booking_identity_missing` | `book_appointment` without valid resolution or complete inline fallback | No appointment; agent completes resolution |
| Wrong-patient social engineering | Caller knows another demo patient's sample email + DOB | Demo accepts match — acceptable demo risk, unacceptable in production |

## Testing Strategy

- Unit tests for outcome classification (exact, possible, multiple, not found, created) with fake patient repositories.
- Tests for normalization (case, whitespace, phone digits) aligned with `PatientIntakeService`.
- Integration tests: resolution → confirmed `possible_match` → `book_appointment` with token.
- Regression: `lookup_only` never creates; `demo_auto_create` idempotency and `.test` domain guard.
- Voice booking tests: reject booking without resolution when token is required.

## Related Configuration

```env
VOICE_PATIENT_INTAKE_MODE=lookup_only          # default; no auto-create
VOICE_PATIENT_INTAKE_MODE=demo_auto_create     # public demo; .test emails only
```

Public demo should pair `demo_auto_create` with [Public Demo Guardrails](public-demo-guardrails.md) so resolution and booking remain rate-limited and quota-bounded.
