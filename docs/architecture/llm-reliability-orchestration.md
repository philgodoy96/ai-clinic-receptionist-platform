# LLM Reliability Orchestration

## Context

The public demo will eventually use real LLM providers.

Real providers can fail, timeout, return invalid JSON, produce low-confidence outputs, or violate safety expectations.

## Design Principle

LLM providers may fail.

The backend must retry only when safe, fall back only when useful, and always end in a deterministic safe path.

## Current Implementation

The implementation includes:

- explicit LLM failure taxonomy
- local repair before provider retry
- bounded primary provider attempts
- optional fallback provider
- deterministic fallback after exhaustion
- rich LLM metadata

## Failure Categories

The system distinguishes:

- repairable outputs
- retryable provider/output failures
- non-retryable safety or policy failures
- deterministic fallback

## Retry Policy

Primary provider attempts are bounded by:

```env
LLM_MAX_PRIMARY_ATTEMPTS=2
```

The system does not retry for:

- medical emergency
- explicit human request
- safety violation
- low confidence
- policy violation

## Fallback Provider

Fallback provider support is optional and disabled by default:

```env
LLM_FALLBACK_ENABLED=false
```

When enabled, fallback provider is only used after fallback-eligible retryable failures.

Example future configuration:

```env
LLM_PRIMARY_PROVIDER=groq
LLM_FALLBACK_ENABLED=true
LLM_FALLBACK_PROVIDER=bedrock
LLM_MAX_PRIMARY_ATTEMPTS=2
LLM_MAX_FALLBACK_ATTEMPTS=1
```

## Metadata

LLM metadata includes:

- provider
- primary_provider
- fallback_provider
- used_fallback_provider
- attempt_count
- primary_attempt_count
- fallback_attempt_count
- used_repair
- used_fallback
- failure_category
- failure_reason
- prompt_version

The system does not store full prompt text or raw provider output in message metadata.

## Safety Boundary

LLM retry/fallback cannot:

- create appointment holds
- create appointments
- send emails
- assign escalations
- impersonate a human
- bypass confirmation
- bypass emergency handling

## Future Work

Future implementation phases may add:

- Groq primary provider
- Bedrock fallback in hosted mode
- provider-specific rate limit classification
- circuit breaker
- per-provider cost budgets
- prompt regression reports

See also: [Real LLM Provider Adapter Boundary](real-llm-provider-adapter.md), [LLM Provider Foundation](llm-provider-foundation.md), [Provider-Run Evaluation Mode](provider-run-evaluation-mode.md), [Prompt Versioning and LLM Traceability](prompt-versioning.md).
