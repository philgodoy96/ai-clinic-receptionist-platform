# Human Escalations API

This API is an internal/debug API for operational handoff records.

It is not a public patient-facing API.

## List Escalations

`GET /api/v1/human-escalations`

Supported filters:

- `status`
- `reason`
- `priority`
- `conversation_id`
- `patient_id`
- `appointment_id`
- `cursor`
- `limit`

The list endpoint uses cursor pagination.

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

## Production Security

For this portfolio demo, authentication and RBAC are intentionally out of scope.

In production, these endpoints should require staff authentication, role-based authorization, and audit logging.