# Human Escalations API

This API is an internal/debug API for operational handoff records.

It is not a public patient-facing API.

Immediate escalations may also create an associated durable `human_escalation_notification` email job for staff handoff. That job is inspectable through the Email Job Debug API, not through the Human Escalations API response shape.

Escalation responses include assignment fields (`assigned_to`, `assigned_at`, `due_at`) and a computed `is_overdue` flag for open or acknowledged records past their due date.

## List Escalations

`GET /api/v1/human-escalations`

Supported filters:

- `status`
- `reason`
- `priority`
- `conversation_id`
- `patient_id`
- `appointment_id`
- `assigned_to`
- `unassigned`
- `overdue`
- `cursor`
- `limit`

The list endpoint uses cursor pagination.

`unassigned=true` returns escalations with no `assigned_to` value.

`overdue=true` returns open or acknowledged escalations where `due_at` is before the current time. Resolved or cancelled records are excluded even if their due date has passed.

## Get Escalation

`GET /api/v1/human-escalations/{escalation_id}`

## Acknowledge Escalation

`POST /api/v1/human-escalations/{escalation_id}/acknowledge`

Request body:

```json
{
  "acknowledged_by": "demo_staff"
}
```

## Resolve Escalation

`POST /api/v1/human-escalations/{escalation_id}/resolve`

Request body:

```json
{
  "resolved_by": "demo_staff",
  "resolution_notes": "Called patient and resolved scheduling issue."
}
```

## Assign Escalation

`POST /api/v1/human-escalations/{escalation_id}/assign`

Request body:

```json
{
  "assigned_to": "demo_staff"
}
```

Assigning an open escalation moves it to acknowledged and sets `assigned_at` and `due_at` from the escalation priority.

Reassigning to a different staff label updates assignment timestamps and recalculates the due date.

Assigning the same staff label twice is idempotent.

## Unassign Escalation

`POST /api/v1/human-escalations/{escalation_id}/unassign`

No request body.

Clears `assigned_to` and `assigned_at`. Preserves `due_at` for SLA tracking.

## Production Security

For this portfolio demo, authentication and RBAC are intentionally out of scope.

In production, these endpoints should require staff authentication, role-based authorization, and audit logging.
