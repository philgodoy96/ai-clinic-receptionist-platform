# Voice Patient Identity Resolution (Future Slice)

Status: **Planned** — not in the current voice patient intake branch.

## Problem

The current branch supports:

- `VOICE_PATIENT_INTAKE_MODE=lookup_only` — exact match on name + DOB + email
- `VOICE_PATIENT_INTAKE_MODE=demo_auto_create` — minimal demo patient creation for `.test` emails when no match exists

This is sufficient for a public demo with seeded patients and guided sample contact data. It does **not** solve ambiguous identity, fuzzy name matching, or progressive lookup before booking.

## Proposed scope

### New read tool: `resolve_patient_identity`

A dedicated Retell tool (or internal service step) that returns structured resolution outcomes without booking:

| Outcome | Meaning |
|---------|---------|
| `exact_match` | Single patient matches all provided fields |
| `possible_match` | Close match; agent should ask a confirmation question |
| `multiple_matches` | Ambiguous; agent should clarify one field at a time |
| `not_found` | No match; may proceed to new-patient intake if policy allows |
| `created` | Demo auto-create path created a minimal patient (demo mode only) |

### Agent behavior

- Ask whether the caller has been seen before before collecting full identity.
- On `possible_match`, use natural confirmation: "I have a John Miller born in April 1985 — is that you?"
- On `multiple_matches`, narrow with email or phone when the caller provided them.
- Never invent email or phone; never call `book_appointment` until resolution succeeds and final summary + yes.

### Backend artifacts

- `patient_resolution_id` or opaque resolution token tying a voice call to a resolved patient candidate
- Safer lookup by email/phone when name+DOB is ambiguous
- Audit metadata without storing raw PHI in logs

### Out of scope for this slice

- Fuzzy matching in `book_appointment` itself
- Production MPI / EHR integration
- Changing `book_appointment` schema in the first iteration

## Related docs

- [Retell Master Prompt v2](retell-master-prompt-v2.md)
- [Retell Tool Descriptions](retell-tool-descriptions.md)
- [Configuration — Voice patient intake](../configuration.md#voice-patient-intake)
