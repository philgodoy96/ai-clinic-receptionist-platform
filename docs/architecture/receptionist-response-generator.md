# Receptionist Response Generator

## Context

The system supports deterministic receptionist flows.

Chat and voice scheduling decisions are made by backend services, guardrails, and business rules. User-facing wording has historically been produced by deterministic templates and fixed reply strings.

This implementation phase introduces controlled natural-language response generation. The backend still decides what to say. An optional LLM may only rephrase safe responses from backend-provided facts.

## Design Principle

The backend plans the response. The LLM may phrase the response.

`ResponsePlan` is the contract between business logic and response rendering. Business services produce intent, fallback text, facts, and safety controls. Generators render user-facing text from that plan.

The LLM does not decide scheduling outcomes, tool execution, or policy exceptions.

## Current Implementation

The implementation includes:

- `ResponsePlan` and `GeneratedResponse` domain models with validation and safe metadata sanitization
- `DeterministicReceptionistResponseGenerator` with template rendering for core receptionist response types
- optional `LLMReceptionistResponseGenerator` behind a `ReceptionistResponseGenerator` protocol
- `ResponseOutputValidator` for length, empty text, and blocked secret-like patterns
- deterministic fallback on provider timeout, malformed structured output, unsafe output, or unknown errors
- chat integration via `ChatReceptionistService` and `render_chat_reply()`
- optional voice `suggested_response_text` on successful Retell tool results
- prompt version `receptionist-response-v1` for LLM phrasing requests

### Response planning

`ResponsePlan` carries:

- `response_type` — informational, scheduling, confirmation, escalation, fallback, or critical
- `channel` — chat or Retell voice
- `fallback_text` — required safe baseline wording from business logic
- `facts` — backend-provided values such as specialty, date, slot count, hold ID, or appointment ID
- `deterministic_behavior` — forces deterministic rendering for controlled flows
- optional `safety_level`

`GeneratedResponse` records:

- rendered `text`
- `mode` — `deterministic` or `llm`
- `used_fallback`
- safe `facts` and `metadata` without raw provider payloads

### Chat integration

`ChatReceptionistService` builds a `ResponsePlan` from the business reply snapshot and renders it through the configured generator.

Controlled chat intents remain deterministic even when `RECEPTIONIST_RESPONSE_MODE=llm`, including:

- emergency guidance
- human escalation
- booking confirmation and booking failure outcomes
- hold creation

For deterministic mode and controlled intents, the generator preserves the business `fallback_text` so existing chat semantics remain stable.

Assistant message metadata stores safe `response_generation` fields such as `mode`, `used_fallback`, `prompt_version`, and `provider`. It does not store raw prompts or raw provider output.

### Voice integration

The Retell tool adapter may attach `suggested_response_text` to successful booking, cancellation, reschedule, and hold tool results.

Voice suggested text is generated through the deterministic response generator only. Retell may speak or display that text, but the backend does not delegate voice turn-taking to this LLM boundary.

## Safety Boundary

The LLM cannot:

- create appointments
- cancel appointments
- reschedule appointments
- invent availability
- invent confirmation email status
- override emergency handling
- bypass escalation rules
- execute tools

Response generation is read-only with respect to business side effects. Holds, bookings, cancellations, reschedules, emails, and escalations remain in their existing services.

## Deterministic Responses

Critical responses remain deterministic or strongly controlled.

The system skips LLM phrasing when:

- `deterministic_behavior` is true on the plan
- `response_type` is `critical`
- `safety_level` is `critical`
- the template is `emergency_guidance`

`RECEPTIONIST_RESPONSE_MODE` defaults to `deterministic`, so local development and CI do not require a real provider for user-facing wording.

## LLM Responses

LLM responses are allowed only for safe phrasing tasks and are based on backend-provided facts.

When `RECEPTIONIST_RESPONSE_MODE=llm`, the generator sends a structured request containing the response plan payload and expects JSON output shaped as:

```json
{"text": "..."}
```

The LLM may vary wording, but it must not introduce facts that are not present in the plan.

Provider selection uses `RECEPTIONIST_RESPONSE_LLM_PROVIDER` when set; otherwise it falls back to the primary LLM provider configuration.

## Failure Handling

Provider failures, malformed outputs, or unsafe outputs fall back to deterministic responses.

Failure paths include:

- provider timeout or transport errors
- structured output parse or validation errors
- `ResponseOutputValidationError` for blocked patterns such as API keys or secret-like content

Fallback responses use deterministic rendering or the plan `fallback_text`. Metadata records `used_fallback=true` and a safe `failure_reason` without storing raw provider payloads.

## Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `RECEPTIONIST_RESPONSE_MODE` | `deterministic` | Response rendering mode: `deterministic` or `llm` |
| `RECEPTIONIST_RESPONSE_LLM_PROVIDER` | empty | Optional dedicated provider for response phrasing. When unset, uses the primary LLM provider |
| `RECEPTIONIST_RESPONSE_MAX_TOKENS` | `400` | Max output tokens for LLM phrasing (`>= 1`) |
| `RECEPTIONIST_RESPONSE_TEMPERATURE` | `0` | LLM temperature for response phrasing (`0`–`2`) |
| `RECEPTIONIST_RESPONSE_VALIDATE_OUTPUT` | `true` | Enable post-generation output validation |

For local development and CI:

```env
RECEPTIONIST_RESPONSE_MODE=deterministic
RECEPTIONIST_RESPONSE_VALIDATE_OUTPUT=true
```

Optional hosted demo phrasing example:

```env
RECEPTIONIST_RESPONSE_MODE=llm
RECEPTIONIST_RESPONSE_LLM_PROVIDER=groq
RECEPTIONIST_RESPONSE_MAX_TOKENS=400
RECEPTIONIST_RESPONSE_TEMPERATURE=0
RECEPTIONIST_RESPONSE_VALIDATE_OUTPUT=true
```

This boundary is separate from LLM shadow analysis and slot-filling reliability orchestration. Analysis retries and fallback providers do not automatically change response phrasing behavior.

## Future Work

- public frontend demo
- deployment configuration
- Retell dashboard setup
- real smoke testing
- richer eval dataset for response quality

See also: [LLM Reliability Orchestration](llm-reliability-orchestration.md), [Prompt Versioning and LLM Traceability](prompt-versioning.md), [Voice Conversation Bridge](voice-conversation-bridge.md), [Retell Tool-Calling Adapter](retell-tool-calling-adapter.md), [Chat API Foundation](chat-api-foundation.md).
