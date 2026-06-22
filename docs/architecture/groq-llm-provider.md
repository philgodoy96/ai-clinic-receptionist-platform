# Groq LLM Provider

## Context

The public demo can use Groq as the primary LLM provider for receptionist analysis.

FakeLLMProvider remains the default for local development and CI.

Bedrock remains available as an optional enterprise/fallback provider.

## Design Principle

Groq is a provider adapter, not the orchestrator.

The backend reliability boundary remains in control.

## Current Implementation

The implementation includes:

- Groq provider configuration
- GroqLLMProvider behind the LLMProvider Protocol
- provider factory support
- structured output / JSON response mode configuration
- provider metadata
- integration with LLM reliability orchestration
- explicit `User-Agent` on outbound HTTP requests
- tests with mocked provider calls

## HTTP Client

`GroqLLMProvider` uses Python `urllib` for outbound calls. Groq's API is fronted by
Cloudflare, which blocks the default `Python-urllib/3.x` user agent. Requests without
an explicit `User-Agent` can fail immediately with HTTP `403` and body `error code: 1010`
before Groq processes the prompt.

The adapter sends `User-Agent: ai-clinic-receptionist-platform/1.0` on every request.
Provider exceptions for HTTP `4xx`/`5xx` responses include the response body when
available to simplify troubleshooting.

### Troubleshooting shadow analysis failures

If chat shadow metadata shows `used_fallback=true`, `failure_reason=provider_exception`,
and `input_tokens=0` while a manual Groq API test succeeds from another HTTP client:

- confirm the running API process includes the explicit `User-Agent` fix
- compare the failing client fingerprint (for example PowerShell vs Python `urllib`)
- inspect the provider exception message for Cloudflare `1010` or Groq API error JSON

## Local Development

Local development uses:

```env
LLM_PRIMARY_PROVIDER=fake
LLM_FALLBACK_ENABLED=false
```

No Groq API key is required for local development or CI.

## Public Demo Configuration

Hosted public demo can use:

```env
LLM_PRIMARY_PROVIDER=groq
GROQ_API_KEY=...
GROQ_MODEL=...
GROQ_RESPONSE_FORMAT=json_schema
LLM_MAX_PRIMARY_ATTEMPTS=2
LLM_FALLBACK_ENABLED=false
```

## Optional Enterprise Fallback

A future enterprise-style configuration may use:

```env
LLM_PRIMARY_PROVIDER=groq
LLM_FALLBACK_ENABLED=true
LLM_FALLBACK_PROVIDER=bedrock
LLM_MAX_PRIMARY_ATTEMPTS=2
LLM_MAX_FALLBACK_ATTEMPTS=1
```

## Safety Boundary

Groq output cannot directly:

- create appointment holds
- create appointments
- send emails
- assign escalations
- impersonate a human
- bypass confirmation
- bypass emergency handling

All provider output is still parsed, repaired if needed, validated, and checked by backend guardrails.

## Structured Outputs

The provider supports configurable response format modes:

- `json_schema`
- `json_object`
- `none`

Backend validation remains mandatory regardless of provider response format.

## Future Work

Future implementation phases may add:

- model-specific eval reports
- provider-specific circuit breaker
- cost budgets
- prompt regression reports
- streaming if needed
- provider health endpoint

See also: [Real LLM Provider Adapter Boundary](real-llm-provider-adapter.md), [LLM Reliability Orchestration](llm-reliability-orchestration.md), [Provider-Run Evaluation Mode](provider-run-evaluation-mode.md), [Configuration](../configuration.md).
