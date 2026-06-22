# Public Demo Guardrails

## Context

The platform is intended to support a public unauthenticated demo for a fictional clinic.

The demo must be easy to access, but it must also prevent abuse.

## Design Principle

Public demo must remain open, but bounded.

No login does not mean no abuse protection.

## Current Implementation

The current implementation supports:

- public demo mode configuration
- Redis-backed rate limiting
- per-IP chat message limits
- per-IP Retell tool limits
- daily appointment creation quotas
- daily confirmation email quotas (`DEMO_CONFIRMATION_EMAILS_PER_DAY_PER_IP`, `DEMO_GLOBAL_CONFIRMATION_EMAILS_PER_DAY`)
- global daily demo quotas
- standardized 429 responses
- Retell webhook signature verification as the provider-auth boundary on `/api/v1/retell/tools/*`

When confirmation email quotas are exceeded, booking can still succeed but the confirmation email job is skipped. This keeps the demo open while bounding outbound email volume.

Signature verification runs before guardrails on Retell tool routes. Invalid signatures are rejected without consuming Redis rate-limit counters or executing domain logic.

See also: [Email Dispatch Reliability](email-dispatch-reliability.md), [Retell Webhook Security](retell-webhook-security.md), [Retell Tool-Calling Adapter](retell-tool-calling-adapter.md), [Retell Voice Booking Confirmation](retell-voice-booking-confirmation.md), [Retell Voice Appointment Cancellation](retell-voice-cancellation.md).

## Local Development

Guardrails are disabled by default for local development.

Local mode should continue to run with:

- FakeLLMProvider
- fake email provider
- no real API keys
- Docker Compose infrastructure

## Public Demo Mode

For a hosted public demo:

```env
PUBLIC_DEMO_MODE=true
PUBLIC_DEMO_GUARDRAILS_ENABLED=true
```

Recommended real demo providers in future implementation phases:

```env
LLM_PROVIDER=groq
EMAIL_PROVIDER=resend
RETELL_ENABLED=true
RETELL_WEBHOOK_VERIFICATION_ENABLED=true
RETELL_WEBHOOK_SECRET=...
RETELL_ALLOW_INSECURE_WEBHOOKS=false
```

Fake providers remain the default for local development and CI. Retell remains disabled by default until explicitly enabled.

## Protected Surfaces

The following surfaces are protected:

- chat messages
- Retell tool endpoints (`POST /api/v1/retell/tools` and related `/api/v1/retell/tools/*` routes)
- appointment creation quotas
- confirmation email quotas (per-IP and global daily limits on new confirmation email jobs)

Retell tools are subject to the same public demo limits as chat. Per-IP Retell tool rate limits apply after signature verification succeeds. Unsupported or invalid tool requests are rejected without bypassing scheduling or hold invariants.

Voice booking via `book_appointment` must respect the same appointment creation quotas and confirmation email quotas as chat booking. A successful voice booking counts toward daily appointment limits. Confirmation email jobs created by voice booking count toward per-IP and global confirmation email quotas; when email quotas are exceeded, booking may still succeed but the confirmation email job is skipped.

Voice cancellation via `cancel_appointment` remains protected by the same Retell signature verification and per-IP Retell tool rate limits. Cancellation does not bypass appointment lookup, cancelable status validation, explicit confirmation, idempotency, or audit logging.

## Failure Behavior

When a quota is exceeded, the API returns 429.

When guardrails are enabled but Redis is unavailable, protected endpoints fail closed.

## Future Work

Future implementation phases may add:

- demo data reset/cleanup
- global LLM call budget
- global voice call budget
- frontend-side friction controls
- CAPTCHA or edge protection
- admin-only debug APIs
- deployment runbook
