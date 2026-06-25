# Retell Tool Configuration

Canonical JSON parameter schemas for Retell custom functions in the public scheduling demo. Pair with [Retell Master Prompt v5](retell-master-prompt-v5.md) (`retell-receptionist-v5`) and [Retell Tool Descriptions](retell-tool-descriptions.md) for dashboard descriptions and recovery guidance.

Related:

- [Retell Dashboard Setup](retell-dashboard-setup.md)
- [Retell Manual Smoke Tests](retell-manual-smoke-tests.md)
- [Appointment Slot Holds](../architecture/appointment-holds.md)

---

## Global conventions

- Tool names use snake_case and must match the backend allowlist exactly.
- Retell payload mode: **args only** must be **OFF** (use the default Retell envelope).
- Side-effecting tools return `status`: `succeeded`, `failed`, or `rejected`. Treat only `status: succeeded` as a completed action.
- When `tool_call_id` is present, duplicate retries return the stored outcome with `duplicate: true`.

---

## hold_appointment_slot

Temporarily reserves one appointment time for this call while patient details are collected.

### Parameters

```json
{
  "type": "object",
  "properties": {
    "availability_slot_id": {
      "type": "string",
      "description": "Availability slot UUID returned by check_availability for the time the caller selected."
    },
    "owner_id": {
      "type": "string",
      "description": "Optional. Defaults to the provider call id when omitted."
    }
  },
  "required": ["availability_slot_id"],
  "additionalProperties": false
}
```

### Hold TTL policy

- **Retell voice tools do not control hold TTL.** The backend owns hold duration policy.
- Redis stores the hold with `APPOINTMENT_HOLD_TTL_SECONDS` (default **300** seconds).
- Successful responses include `expires_in_seconds` equal to the backend-configured TTL.
- Legacy `ttl_seconds` in tool arguments, if sent by an older agent configuration, is **ignored** by the backend. It does not change Redis expiration or the response TTL.
- Holds are short-lived coordination records, not durable reservations. If a call drops or the caller abandons the flow, the hold expires automatically and the slot returns to availability.

See [Appointment Slot Holds](../architecture/appointment-holds.md).

---

## reschedule_appointment

Moves an existing appointment to a new time after explicit caller confirmation.

### Parameters

```json
{
  "type": "object",
  "properties": {
    "patient_resolution_id": {
      "type": "string",
      "description": "Patient resolution ID returned by resolve_patient_identity or confirm_patient_identity. Required before rescheduling an appointment."
    },
    "appointment_id": {
      "type": "string",
      "description": "Original appointment UUID returned by list_patient_appointments for the appointment the caller selected to reschedule."
    },
    "new_slot_id": {
      "type": "string",
      "description": "New availability slot UUID returned by check_availability and selected by the caller."
    },
    "hold_id": {
      "type": "string",
      "description": "Hold ID for the new slot returned by hold_appointment_slot."
    },
    "explicit_confirmation": {
      "type": "boolean",
      "description": "Must be true only after the caller explicitly confirms rescheduling."
    },
    "confirmation_text": {
      "type": "string",
      "description": "The caller's actual latest verbal confirmation. Example: Yes, please reschedule it."
    },
    "reschedule_reason": {
      "type": "string",
      "description": "Optional short reason. Example: Caller requested a different time."
    }
  },
  "required": [
    "patient_resolution_id",
    "appointment_id",
    "new_slot_id",
    "hold_id",
    "explicit_confirmation",
    "confirmation_text"
  ],
  "additionalProperties": false
}
```

### Rules

- `patient_resolution_id` comes from `resolve_patient_identity` or `confirm_patient_identity`.
- `appointment_id` comes from `list_patient_appointments`.
- `new_slot_id` and `hold_id` come from `check_availability` and `hold_appointment_slot` for the **new** time.
- `confirmation_text` must be the caller's actual latest confirmation, not invented by the agent.
- Never trust `appointment_id` alone — always pass `patient_resolution_id`.
- Never trust `new_slot_id` alone — always pass a valid `hold_id` for the new slot.
- Never call before explicit caller confirmation on a separate turn.
- Never claim rescheduling succeeded before `status: succeeded`.

### Durable state transition

On success, the backend performs a historical-preserving transition:

```text
old appointment -> rescheduled
old slot -> available
new appointment -> scheduled
new slot -> booked
```

The transition is atomic at the service boundary. If rescheduling fails, no partial durable state should remain.

See [Retell Voice Appointment Rescheduling](../architecture/retell-voice-rescheduling.md) and [Appointment Rescheduling Foundation](../architecture/appointment-rescheduling-foundation.md).

---

## Other tools

Parameter schemas and dashboard descriptions for the remaining tools (`get_clinic_context`, `check_availability`, `release_appointment_hold`, `resolve_patient_identity`, `confirm_patient_identity`, `list_patient_appointments`, `book_appointment`, `cancel_appointment`) are documented in [Retell Tool Descriptions](retell-tool-descriptions.md).

---

## Scheduling availability policy

`check_availability` is an **advisory read**. The backend applies:

| Setting | Default | Effect |
|---------|---------|--------|
| `SCHEDULING_MIN_BOOKING_LEAD_MINUTES` | `60` | Excludes slots before clinic now + lead time |
| `SCHEDULING_BOOKING_HORIZON_DAYS` | `14` | Excludes slots beyond the horizon; **single source of truth** for availability visibility and demo slot generation |

Same-day scheduling is allowed when returned by backend policy. Retell must **not** decide what is bookable or hardcode horizon length. The backend classifies horizon using **resolved absolute dates/windows**, not phrases like "next month".

### Horizon-aware response metadata

Successful `check_availability` results may include:

| Field | Purpose |
|-------|---------|
| `availability_status` | `available`, `no_matching_slots`, `outside_booking_horizon`, `needs_date_clarification` |
| `available_slots` | Matching openings (empty when not `available`) |
| `booking_window` | `earliest_bookable_date`, `latest_bookable_date`, `timezone` when relevant |
| `suggested_response_text` | Voice-safe recovery phrase — prefer over improvising when no slots or clarification is needed |

See [Retell Tool Descriptions — check_availability](retell-tool-descriptions.md#2-check_availability).

Consistency model:

```text
check_availability        = advisory read
hold_appointment_slot     = temporary coordination boundary
book_appointment          = durable state transition
reschedule_appointment    = durable state transition
database constraints      = final safety net
```
