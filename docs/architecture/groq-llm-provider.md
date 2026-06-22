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
- tests with mocked provider calls

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
