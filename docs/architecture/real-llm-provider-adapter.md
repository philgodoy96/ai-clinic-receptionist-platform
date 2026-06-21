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
- Bedrock provider adapter
- structured output prompting
- provider timeout/retry configuration
- validation and fallback through the existing reliability layer

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

## Configuration

Default:

```env
LLM_PROVIDER=fake
```

Optional real provider:

```env
LLM_PROVIDER=bedrock
BEDROCK_MODEL_ID=your-model-id
AWS_REGION=us-east-1
BEDROCK_REQUEST_TIMEOUT_SECONDS=10
BEDROCK_MAX_RETRIES=0
BEDROCK_TEMPERATURE=0
BEDROCK_MAX_TOKENS=800
```

AWS credentials are not stored in the repository.

Runtime credentials should come from standard AWS environment, profile, or role mechanisms.

## Testing Boundary

Automated tests do not call real Bedrock.

Provider calls are mocked/stubbed.

FakeLLMProvider remains the default for tests and local demos.

## Failure Handling

Provider failures should fall back safely.

Invalid JSON, schema violations, safety violations, and low-confidence outputs are handled by the existing reliability layer.

## Future Work

Future implementation phases may add:

- provider fallback chain
- circuit breaker
- rate limit handling
- prompt versioning
- model evaluation dataset
- tenant-level cost tracking
- streaming support for voice

See also: [LLM Provider Foundation](llm-provider-foundation.md), [Structured-Output-Assisted Slot Filling](structured-output-slot-filling.md).
