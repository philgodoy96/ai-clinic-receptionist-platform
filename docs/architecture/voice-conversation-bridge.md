# Voice Conversation Bridge

## Context

The system supports both written chat and voice interactions.

Voice calls arrive through Retell, but voice should not become a separate receptionist implementation.

## Design Principle

Voice is a channel into the same receptionist conversation system, not a separate receptionist system.

## Current Implementation

The implementation includes:

- durable linkage between `VoiceCall` and `Conversation`
- voice `Conversation` channel support
- `VoiceConversationBridgeService`
- safe voice conversation context
- Retell tool adapter integration with conversation context
- internal/debug context endpoint

See also:

- [Retell Call Lifecycle](retell-call-lifecycle.md)
- [Retell Tool-Calling Adapter](retell-tool-calling-adapter.md)
- [Retell Webhook Security](retell-webhook-security.md)
- [Conversation Domain](conversation-domain.md)

## Architecture

Retell provider callbacks and tool calls pass through the Retell security boundary.

Verified voice events create or update `VoiceCall` records.

The bridge links `VoiceCall` records to `Conversation` records.

After that, voice and chat share the same business services.

```
Retell lifecycle webhook
  -> signature verification
  -> VoiceCall / VoiceCallEvent persistence

Retell tool callback
  -> signature verification
  -> RetellToolCallingAdapter
  -> VoiceConversationBridgeService (resolve / link Conversation)
  -> scheduling and hold services

Chat API
  -> ConversationService
  -> scheduling and hold services
```

## Responsibilities

The bridge is responsible for:

- resolving `provider_call_id` to `VoiceCall`
- linking `VoiceCall` to `Conversation`
- returning safe conversation context
- helping Retell tools reuse existing conversation state

The bridge is not responsible for:

- booking appointments
- sending emails
- calling LLMs
- storing transcripts
- configuring Retell dashboard
- executing arbitrary provider tools

## Safe Context

Voice conversational state is stored under `conversation_metadata["voice_context"]`.

Safe context may include scheduling-related fields such as:

- `specialty_name`
- `doctor_name`
- `doctor_id`
- `requested_date`
- `requested_time_window`
- `selected_availability_slot_id`
- active hold fields (`hold_id`, `availability_slot_id`, `start_time`, `end_time`)

`VoiceConversationBridgeService` and the Retell tool adapter read and merge only allowlisted keys. Blocked keys include transcripts, raw provider payloads, secrets, and raw phone numbers.

## Retell Tool Integration

Before executing supported scheduling tools, the Retell tool adapter ensures the related `VoiceCall` is linked to a voice `Conversation`.

`check_availability` may resolve partial arguments from stored voice context and write back safe criteria after a successful lookup.

`hold_appointment_slot` merges hold details into `voice_context`.

`release_appointment_hold` clears active hold fields from `voice_context` while preserving non-hold scheduling preferences when present.

## Internal Debug Endpoint

For local verification and demo debugging only:

`GET /api/v1/internal/voice-calls/{voice_call_id}/conversation-context`

The response includes safe linkage and scheduling context fields only. It does not expose raw transcripts, raw provider payloads, secrets, full patient identity, or raw phone numbers.

There is no dashboard and no auth/RBAC on this endpoint yet. Treat it as internal/debug only.

## Safety Boundary

The bridge does not store raw provider payloads, raw transcripts, audio data, secrets, or raw phone numbers.

## Relationship to Chat

Chat and voice use different channel adapters.

Both should use the same `Conversation` domain and business services.

Chat conversational memory lives in `conversation_metadata["chat_context"]`.

Voice conversational memory lives in `conversation_metadata["voice_context"]`.

The two context namespaces are separate so chat and voice adapters do not overwrite each other's state.

## Future Work

Future implementation phases may add:

- voice booking after explicit confirmation
- cancel appointment via voice
- reschedule appointment via voice
- transcript summary persistence
- deployment runbook
- Retell dashboard setup
- real Retell smoke test
