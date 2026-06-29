# Request Correlation and Structured Logging

## Context

The AI Clinic Receptionist Platform needs request correlation across HTTP requests, Retell tool calls, structured logs, and audit logs.

This helps debug production-style flows such as:

- Retell call checks availability
- Retell call creates a hold
- Retell call books an appointment
- Audit event is recorded
- A failure happens during booking

## Request Headers

The API supports:

    X-Request-ID
    X-Correlation-ID

If `X-Request-ID` is not provided, the backend generates one.

If `X-Correlation-ID` is not provided, the backend uses the request ID as the correlation ID.

Responses include both headers.

## Request Context

The backend stores request IDs in ContextVar values for the lifetime of the request.

This allows lower-level code, such as logging and audit services, to access request context without passing request objects through every service method.

## Structured Logging

Application logs are emitted as JSON.

Current log fields include:

- timestamp
- level
- logger
- message
- request_id
- correlation_id
- method
- path
- status_code
- duration_ms
- client_host
- event

## Audit Log Correlation

AuditLogService attaches the current request_id when an audit log payload does not explicitly provide one.

This links durable audit records back to API request logs.

Retell-specific correlation still uses:

- call_id
- conversation_id

## API Error Correlation

Standardized HTTP error payloads include `request_id` and `correlation_id` in the `error` object.

This links API error responses back to structured logs and audit records for the same request.

See [error-responses.md](../api/error-responses.md) for the full error envelope.

## Privacy Boundary

Structured logs should not contain:

- Diagnosis
- Clinical notes
- Raw transcripts by default
- Payment data
- Insurance identifiers
- Unnecessary patient details

Logs should focus on operational metadata.

## Extension points (not active in default demo runtime)

The following are outside the current demo observability baseline:

- OpenTelemetry traces and trace/span IDs
- Prometheus metrics export
- Log shipping to external platforms
- Request body logging
- PII redaction middleware
- Distributed tracing across workers

These are production-hardening extension points. Dependencies exist in the project but are not wired into the running application.