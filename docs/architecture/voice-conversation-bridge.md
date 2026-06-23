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
- optional deterministic `suggested_response_text` on successful scheduling tool results
- voice booking context resolution for `book_appointment`
- voice cancellation context resolution for `cancel_appointment`
- voice rescheduling context resolution for `reschedule_appointment`
- internal/debug context endpoint

See also:

- [Retell Call Lifecycle](retell-call-lifecycle.md)
- [Retell Tool-Calling Adapter](retell-tool-calling-adapter.md)
- [Retell Voice Booking Confirmation](retell-voice-booking-confirmation.md)
- [Retell Voice Appointment Cancellation](retell-voice-cancellation.md)
- [Retell Voice Appointment Rescheduling](retell-voice-rescheduling.md)
- [Appointment Rescheduling Foundation](appointment-rescheduling-foundation.md)
- [Retell Webhook Security](retell-webhook-security.md)
- [Conversation Domain](conversation-domain.md)
- [Receptionist Response Generator](receptionist-response-generator.md)

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

- executing booking business logic
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
- `appointment_id` and `appointment_status` after booking, cancellation, or rescheduling
- `rescheduled_from_appointment_id` after a successful reschedule
- `last_reschedule_summary` — latest voice reschedule outcome

`VoiceConversationBridgeService` and the Retell tool adapter read and merge only allowlisted keys. Blocked keys include transcripts, raw provider payloads, secrets, and raw phone numbers.

## Retell Tool Integration

Before executing supported scheduling tools, the Retell tool adapter ensures the related `VoiceCall` is linked to a voice `Conversation`.

`check_availability` may resolve partial arguments from stored voice context and write back safe criteria after a successful lookup.

`hold_appointment_slot` merges hold details into `voice_context`.

`release_appointment_hold` clears active hold fields from `voice_context` while preserving non-hold scheduling preferences when present.

`book_appointment` reads active hold fields from `voice_context`, validates hold ownership and expiration, and on success clears hold fields while storing a safe `appointment_id` reference. On recoverable booking failure, useful hold context is preserved so the caller can retry without re-holding.

`cancel_appointment` resolves the target appointment from tool arguments or linked `voice_context`, requires explicit cancellation confirmation, and on success updates `appointment_status` to `cancelled` while clearing active hold fields when present.

`reschedule_appointment` resolves the original appointment from tool arguments or linked `voice_context`, validates the target hold or new slot, requires explicit reschedule confirmation, and on success updates `voice_context` with the new successor appointment reference, `rescheduled_from_appointment_id`, and cleared hold fields. On recoverable reschedule failure, useful hold context is preserved so the caller can retry without re-holding.

Successful scheduling tool results may include `suggested_response_text`. That field is generated deterministically from backend facts and is intended as optional wording guidance for Retell. It does not execute tools or change business state.

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

- transcript summary persistence
- deployment runbook
- Retell dashboard setup
- real Retell smoke test
