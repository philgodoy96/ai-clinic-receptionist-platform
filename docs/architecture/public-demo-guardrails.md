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
- daily confirmation email quotas
- global daily demo quotas
- standardized 429 responses

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
```

Fake providers remain the default for local development and CI.

## Protected Surfaces

The following surfaces are protected:

- chat messages
- Retell tool endpoints
- appointment creation quotas
- confirmation email quotas

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
