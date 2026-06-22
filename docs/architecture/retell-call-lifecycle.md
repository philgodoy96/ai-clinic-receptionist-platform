# Retell Call Lifecycle

## Context

The public demo will support voice calls through Retell.

Before provider events can drive business actions, verified lifecycle events must be persisted as durable internal records.

## Design Principle

Verified provider events should become durable internal records before they drive business actions.

## Current Implementation

The implementation includes:

- VoiceCall persistence
- VoiceCallEvent persistence
- Retell lifecycle ingestion service
- idempotent event ingestion
- conservative call status transitions
- safe event metadata storage
- internal/debug APIs for inspection

See also:

- [Retell Webhook Security](retell-webhook-security.md)
- [Retell Tool-Calling Adapter](retell-tool-calling-adapter.md)
- [Configuration](../configuration.md)

## Entity Model

`VoiceCall` represents the provider call.

`VoiceCallEvent` represents an individual provider event for that call.

Events are idempotent through stable idempotency keys.

## Webhook Flow

1. Retell request arrives.
2. Signature is verified by the security layer.
3. Payload is parsed and minimally validated.
4. VoiceCall is created or updated.
5. VoiceCallEvent is persisted idempotently.
6. No business tools are executed in this phase.

Verified lifecycle events are ingested at:

`POST /api/v1/retell/webhooks/lifecycle`

Internal inspection APIs are available at:

- `GET /api/v1/internal/voice-calls`
- `GET /api/v1/internal/voice-calls/{voice_call_id}`
- `GET /api/v1/internal/voice-calls/{voice_call_id}/events`

## Safety Boundary

Lifecycle events cannot directly:

- create appointment holds
- create appointments
- send emails
- assign escalations
- execute Retell tools
- trigger LLM calls

Lifecycle ingestion is separate from Retell tool execution.

Tool callbacks are handled by the [Retell tool-calling adapter](retell-tool-calling-adapter.md) at `POST /api/v1/retell/tools`. Lifecycle webhooks do not execute scheduling tools.

## Payload Storage

The system stores only safe event metadata.

It does not store raw provider payloads, secrets, raw transcripts, or audio data in lifecycle event metadata.

Phone numbers are redacted before persistence on `VoiceCall` records.

## Status Transitions

The implementation uses conservative status transitions.

Terminal statuses such as `ended` or `failed` are not downgraded by older out-of-order events.

## Future Work

Future implementation phases may add:

- voice conversation bridge
- voice booking/cancel/reschedule flow
- transcript summary persistence
- recording/object storage
- voice call metrics
- deployment runbook for voice demo
