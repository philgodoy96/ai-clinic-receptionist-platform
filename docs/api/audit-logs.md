# Audit Log API

## Context

The Audit Log API exposes durable operational event records.

It is designed for debugging, traceability, and future admin/operator views.

This API currently does not include authentication or RBAC because the project is still in local/demo mode.

Production hardening should protect this endpoint.

## Endpoint

    GET /api/v1/audit-logs

## Pagination

The endpoint uses cursor pagination.

Default limit:

    50

Maximum limit:

    100

Example:

    GET /api/v1/audit-logs?limit=50

Response:

    {
      "items": [],
      "next_cursor": null
    }

When `next_cursor` is returned, pass it to fetch the next page:

    GET /api/v1/audit-logs?limit=50&cursor=<next_cursor>

## Ordering

Audit logs are ordered by:

    created_at DESC, id DESC

The ID acts as a deterministic tie-breaker when multiple events share the same timestamp.

## Filters

Supported optional filters:

- event_type
- outcome
- actor_type
- source
- patient_id
- appointment_id
- call_id
- conversation_id

Example:

    GET /api/v1/audit-logs?event_type=appointment_booking_confirmed&source=retell_tool

## Response Fields

Each item includes:

- id
- event_type
- outcome
- actor_type
- actor_id
- source
- request_id
- call_id
- conversation_id
- patient_id
- appointment_id
- availability_slot_id
- event_metadata
- created_at

## Privacy Boundary

Audit logs should not contain:

- Diagnosis
- Clinical notes
- Raw transcripts by default
- Payment data
- Insurance identifiers
- Unnecessary patient details

The `event_metadata` field is intended for operational metadata such as stable failure reasons, hold IDs, or doctor IDs.

## Current Limitations

This endpoint does not yet include:

- Authentication
- Role-based authorization
- Export support
- Date range filters
- Full-text search
- Admin UI