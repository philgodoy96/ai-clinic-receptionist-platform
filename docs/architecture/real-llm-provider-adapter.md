# Real LLM Provider Adapter Boundary

## Context

The platform uses an LLM to assist with structured receptionist analysis.

The system originally used a fake provider so tests and local demos were deterministic.

This implementation phase adds a real provider adapter boundary while preserving fake as the default.

## Design Principle

A real LLM provider is an implementation detail.

The backend reliability boundary remains the source of trust.

## Current Implementation

The implementation supports:

- fake provider by default
- configurable provider selection
- Groq provider adapter for hosted public demo
- Bedrock provider adapter for optional enterprise/fallback use
- structured output prompting
- provider timeout/retry configuration
- validation and fallback through the reliability orchestration layer
- bounded primary retries, optional fallback provider, and deterministic fallback after exhaustion
- offline evaluation dataset with recorded-output comparison for structured analysis quality
- optional manual provider-run evaluation mode for local quality checks
- prompt version registry with runtime `prompt_version` metadata on LLM analysis results

## Safety Boundary

The LLM cannot:

- create appointment holds
- create appointments
- send emails
- assign escalations
- impersonate a human
- bypass emergency handling
- bypass booking confirmation

The LLM can only suggest structured candidates that backend services validate.

LLM analysis metadata includes `prompt_version` so shadow analysis can be traced to a registered prompt version. The full prompt text is not stored in conversation metadata.

## Configuration

Default:

```env
LLM_PROVIDER=fake
LLM_MAX_PRIMARY_ATTEMPTS=2
LLM_FALLBACK_ENABLED=false
```

Optional Groq configuration for hosted public demo:

```env
LLM_PRIMARY_PROVIDER=groq
GROQ_API_KEY=gsk_...
GROQ_MODEL=llama-3.3-70b-versatile
GROQ_BASE_URL=https://api.groq.com/openai/v1
GROQ_REQUEST_TIMEOUT_SECONDS=10
GROQ_MAX_OUTPUT_TOKENS=800
GROQ_TEMPERATURE=0
GROQ_RESPONSE_FORMAT=json_schema
LLM_MAX_PRIMARY_ATTEMPTS=2
LLM_FALLBACK_ENABLED=false
```

Optional Bedrock configuration:

```env
LLM_PROVIDER=bedrock
LLM_PRIMARY_PROVIDER=bedrock
BEDROCK_MODEL_ID=your-model-id
AWS_REGION=us-east-1
BEDROCK_REQUEST_TIMEOUT_SECONDS=10
BEDROCK_MAX_RETRIES=0
BEDROCK_TEMPERATURE=0
BEDROCK_MAX_TOKENS=800
```

Optional fallback provider (disabled by default):

```env
LLM_FALLBACK_ENABLED=true
LLM_FALLBACK_PROVIDER=bedrock
LLM_MAX_FALLBACK_ATTEMPTS=1
```

When fallback is enabled, the fallback provider is only used after fallback-eligible retryable primary failures. See [LLM Reliability Orchestration](llm-reliability-orchestration.md).

Groq API keys are not stored in the repository. Provide `GROQ_API_KEY` through environment variables or your deployment secret manager at runtime.

AWS credentials are not stored in the repository.

Runtime credentials should come from standard AWS environment, profile, or role mechanisms.

## Testing Boundary

Automated tests do not call real Groq or Bedrock.

Provider calls are mocked/stubbed.

FakeLLMProvider remains the default for tests and local demos.

Offline evaluation of structured analysis quality runs against a synthetic JSONL dataset and recorded outputs. It does not call real providers in recorded mode.

The configured provider adapter can be evaluated manually with provider-run mode:

```powershell
python -m scripts.evaluate_receptionist_analysis --mode provider --allow-provider-calls
```

See [LLM Evaluation Dataset](llm-evaluation-dataset.md), [Provider-Run Evaluation Mode](provider-run-evaluation-mode.md), and [Prompt Versioning and LLM Traceability](prompt-versioning.md).

## Failure Handling

Provider failures are handled by the reliability orchestration layer:

- local JSON repair before provider retry
- bounded primary provider attempts
- optional fallback provider for fallback-eligible retryable failures
- deterministic fallback analysis after exhaustion

Invalid JSON, schema violations, safety violations, and low-confidence outputs are classified explicitly. The system does not retry for medical emergency, explicit human request, safety violation, low confidence, or policy violation.

See [LLM Reliability Orchestration](llm-reliability-orchestration.md).

## Future Work

Future implementation phases may add:

- circuit breaker
- tenant-level cost tracking
- streaming support for voice

See also: [Groq LLM Provider](groq-llm-provider.md), [LLM Evaluation Dataset](llm-evaluation-dataset.md), [Provider-Run Evaluation Mode](provider-run-evaluation-mode.md), [Prompt Versioning and LLM Traceability](prompt-versioning.md), [LLM Reliability Orchestration](llm-reliability-orchestration.md).
