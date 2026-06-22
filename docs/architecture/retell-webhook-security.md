# Retell Webhook Security

## Context

The public demo will eventually support voice calls through Retell.

Retell callbacks are public HTTP requests from an external provider, so they must be verified before processing.

## Design Principle

Never trust provider callbacks without verification.

Verify first, parse second, process third.

## Current Implementation

The implementation includes:

- Retell security configuration
- centralized signature verification
- protected Retell callback/tool routes
- standardized rejection for missing or invalid signatures
- request body size limit
- tests using fake/mocked verifier

See also:

- [Configuration](../configuration.md)
- [Public Demo Guardrails](public-demo-guardrails.md)
- [Retell Call Lifecycle](retell-call-lifecycle.md)

## Verification Flow

1. Receive request.
2. Read raw body.
3. Verify signature header using configured Retell webhook secret/key.
4. Parse JSON only after verification succeeds.
5. Validate payload.
6. Process supported callback/tool type.

Protected routes live under `/api/v1/retell/tools/*` and `/api/v1/retell/webhooks/lifecycle`. Verification runs before demo guardrails and before any domain adapter executes.

Verified lifecycle events flow into [Retell call lifecycle ingestion](retell-call-lifecycle.md) after signature verification succeeds.

## Local Development

Retell is disabled by default.

```env
RETELL_ENABLED=false
```

Local simulator routes, if present, remain separate from real Retell provider callback routes. Non-Retell endpoints such as `/health` and chat APIs do not require Retell signatures.

For local testing of Retell tool routes without real Retell credentials, enable Retell with insecure webhooks only in `local`, `test`, or `development` environments:

```env
RETELL_ENABLED=true
RETELL_ALLOW_INSECURE_WEBHOOKS=true
RETELL_WEBHOOK_SECRET=test-webhook-secret
```

`RETELL_ALLOW_INSECURE_WEBHOOKS=true` is rejected outside local/test/development `APP_ENV` values.

## Public Demo Configuration

Hosted demo configuration:

```env
RETELL_ENABLED=true
RETELL_WEBHOOK_VERIFICATION_ENABLED=true
RETELL_WEBHOOK_SECRET=...
RETELL_ALLOW_INSECURE_WEBHOOKS=false
```

Pair with public demo guardrails when exposing Retell tool endpoints to the internet.

## Safety Boundary

Unverified Retell requests cannot:

- create holds
- create appointments
- send emails
- assign escalations
- execute tools
- trigger LLM calls
- mutate durable conversation state

Rejected requests return standardized API errors such as `retell_signature_missing`, `retell_signature_invalid`, `retell_payload_too_large`, and `invalid_retell_payload` without executing domain logic.

## Interaction With Public Demo Guardrails

Retell endpoints remain subject to public demo abuse protection where applicable.

Signature verification prevents unauthorized callers from executing provider callback logic.

Rate limits and quotas protect the demo from excessive valid traffic.

Verification runs before guardrails on Retell tool routes, so invalid signatures do not consume Redis rate-limit counters or trigger downstream work.

## Future Work

Future implementation phases may add:

- Retell tool-calling adapter
- voice booking/cancel/reschedule flow
- optional IP allowlist
- alerting for repeated invalid signatures
