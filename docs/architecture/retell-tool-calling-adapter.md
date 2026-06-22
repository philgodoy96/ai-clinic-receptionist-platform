# Retell Tool-Calling Adapter

## Context

The public demo supports voice interactions through Retell.

Retell may request backend tools, but provider tool calls must not bypass backend validation or business invariants.

## Design Principle

Retell may request tools, but the backend owns validation, authorization, idempotency, and business invariants.

## Current Implementation

The implementation includes:

- Retell tool-calling request schemas
- explicit supported tool allowlist
- RetellToolCallingAdapter
- provider-safe response serialization
- integration with existing scheduling and hold services
- voice conversation context resolution via `VoiceConversationBridgeService`
- tests for route verification, adapter dispatch, idempotency, and safety

See also:

- [Retell Webhook Security](retell-webhook-security.md)
- [Retell Call Lifecycle](retell-call-lifecycle.md)
- [Voice Conversation Bridge](voice-conversation-bridge.md)
- [Public Demo Guardrails](public-demo-guardrails.md)
- [Appointment Slot Holds](appointment-holds.md)

## Supported Tools

Current supported tools:

- `check_availability`
- `hold_appointment_slot`
- `release_appointment_hold`

Unified tool execution is exposed at:

`POST /api/v1/retell/tools`

Legacy per-tool routes under `/api/v1/retell/tools/*` may remain for compatibility, but the unified adapter route is the supported execution boundary for voice scheduling tools.

## Safety Boundary

Retell tool calls cannot directly:

- create appointments
- cancel appointments
- reschedule appointments
- send emails
- trigger LLM calls
- mutate patient records
- bypass appointment hold rules
- bypass scheduling validation

The adapter delegates only to existing scheduling and hold services. Booking, cancellation, rescheduling, email dispatch, and LLM orchestration remain outside this boundary.

## Execution Flow

1. Retell sends a tool callback.
2. The backend verifies the Retell signature.
3. The backend parses and validates the tool request.
4. The adapter checks the explicit tool allowlist.
5. The adapter validates tool-specific arguments.
6. The adapter resolves or links the related voice `Conversation` through `VoiceConversationBridgeService`.
7. The adapter delegates to existing backend services using safe voice conversation context when needed.
8. The adapter merges safe scheduling context back into `conversation_metadata["voice_context"]` when appropriate.
9. The adapter returns a provider-safe response.

Public demo guardrails run after signature verification and before adapter execution on protected Retell tool routes.

## Idempotency

Side-effecting tool calls use `provider_call_id` and `tool_call_id` when available.

Duplicate provider retries should not duplicate holds or releases.

When `tool_call_id` is present, hold and release outcomes are recorded on the related `VoiceCall` event metadata so repeated requests return the prior provider-safe result instead of performing the side effect again.

## Relationship to Chat

Chat and voice use different channel adapters, but share the same business services.

The voice adapter must not create a parallel scheduling implementation.

Retell tools now resolve safe voice conversation context from the shared `Conversation` domain instead of maintaining a separate voice-only state store.

## Future Work

Future implementation phases may add:

- `book_appointment` via voice after explicit confirmation
- cancel appointment via voice
- reschedule appointment via voice
- transcript summary persistence
- voice-specific operational metrics
