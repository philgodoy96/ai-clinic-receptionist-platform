# API Error Responses

## Context

The API uses a standardized error response envelope for HTTP errors.

This makes client handling and operational debugging more predictable.

## Error Shape

All HTTP API errors use:

    {
      "error": {
        "code": "string",
        "message": "string",
        "details": null,
        "request_id": "string",
        "correlation_id": "string"
      }
    }

## Fields

### code

Stable machine-readable error code.

Examples:

- validation_error
- email_job_not_found
- invalid_email_job_cursor
- invalid_email_job_retry_state
- invalid_audit_log_cursor
- appointment_slot_already_booked

### message

Human-readable explanation.

### details

Optional structured details.

Validation errors include sanitized validation details.

Raw request input should not be returned.

### request_id

Request identifier used to correlate the error response with application logs.

### correlation_id

Correlation identifier used to group related operations across systems.

## Validation Errors

Validation errors use:

    {
      "error": {
        "code": "validation_error",
        "message": "Request validation failed.",
        "details": {
          "errors": [
            {
              "loc": ["query", "limit"],
              "msg": "Input should be less than or equal to 100",
              "type": "less_than_equal"
            }
          ]
        },
        "request_id": "...",
        "correlation_id": "..."
      }
    }

Validation error details should not include raw input values.

## Internal Errors

Unexpected errors use:

    {
      "error": {
        "code": "internal_server_error",
        "message": "Internal server error.",
        "details": null,
        "request_id": "...",
        "correlation_id": "..."
      }
    }

Internal errors should not expose stack traces or implementation details.

## Retell Tool Boundary

Retell tool business errors intentionally keep their tool response shape:

    {
      "ok": false,
      "error_code": "...",
      "message": "..."
    }

This is because Retell tool responses are consumed by a voice orchestration provider and are part of the tool contract.

HTTP-level errors still use the standardized API error envelope.