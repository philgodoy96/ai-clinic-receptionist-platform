# Conversation Domain

## Context

The AI Clinic Receptionist Platform needs durable conversation storage for chat orchestration and future LLM behavior.

The Chat API already uses `Conversation` and `ConversationMessage` to record each user turn and deterministic assistant reply.

Chat messages may now include scheduling-aware assistant replies, such as specialty listings, doctor listings, or specialty-specific doctor guidance.

Conversation records are used to preserve interaction history across:

- Chat sessions
- Retell voice calls
- Future tool calls
- Future escalation to human
- Future LLM debugging

## Responsibility Boundary

Conversation storage is not the source of truth for scheduling.

Scheduling truth lives in:

- appointments
- availability slots
- patients
- doctors
- email jobs
- audit logs
- Redis appointment holds for temporary slot reservations

Conversation storage records interaction history.

Conversation metadata and message history do not own scheduling truth.

`chat_context` inside `conversation_metadata` is conversational memory only. It helps the chat layer remember what the user has already said across turns, but it is not authoritative for appointments, availability, patients, doctors, or specialties.

`voice_context` inside `conversation_metadata` is conversational memory for Retell voice tool flows. It may store safe scheduling criteria, selected slot identifiers, and active hold fields so voice tools can continue a multi-turn scheduling conversation. Like `chat_context`, it is not authoritative for appointments, availability, patients, doctors, or specialties. Redis hold state is the actual temporary hold truth. The `appointments` table in PostgreSQL is the durable source of truth for confirmed bookings.

`chat_context` may store values such as `offered_slots`, `hold_id`, `patient_identity`, and `appointment_id` so the chat layer can continue a multi-turn hold and booking flow. These values are conversational memory only. Redis hold state is the actual temporary hold truth. The `appointments` table in PostgreSQL is the durable source of truth for confirmed bookings. Conversation metadata does not replace Redis or scheduling storage.

They may reference scheduling context in assistant replies, but appointments, availability, patients, doctors, and specialties remain authoritative in scheduling storage.

## Core Entities

### Conversation

A conversation represents one interaction session.

Fields include:

- channel
- status
- patient_id
- appointment_id
- external_conversation_id
- call_id
- request_id
- correlation_id
- started_at
- ended_at
- conversation_metadata

`conversation_metadata` may include a `chat_context` object for conversational state such as selected doctor, selected specialty, requested date, `offered_slots`, `hold_id`, `patient_identity`, and `appointment_id`. This is conversational memory for multi-turn chat guidance, not business truth. Redis hold state is the actual temporary hold truth. Confirmed appointment truth lives in the Postgres `appointments` table and related scheduling services.

`conversation_metadata` may also include a `voice_context` object for safe Retell voice scheduling state such as requested date, requested time window, selected availability slot, and active hold fields. See [Voice Conversation Bridge](voice-conversation-bridge.md).

### ConversationMessage

A conversation message represents one message or tool-related entry.

Fields include:

- conversation_id
- role
- content
- tool_name
- tool_call_id
- message_metadata
- created_at

## Channels

Supported channels:

- chat
- voice
- retell_voice
- system

## Statuses

Supported statuses:

- active
- closed
- escalated
- abandoned

## Message Roles

Supported roles:

- user
- assistant
- system
- tool

## Metadata Naming

SQLAlchemy reserves `metadata` on declarative models.

This project uses:

- conversation_metadata
- message_metadata

## Privacy Boundary

Conversation records should not store:

- Diagnosis
- Clinical notes
- Payment data
- Insurance identifiers
- Unnecessary patient details

Future transcript storage should be reviewed carefully before storing raw full transcripts.

## LLM Boundary

This implementation does not include LLM orchestration.

The Chat API (`POST /api/v1/chat/messages`) persists interaction history through `Conversation` and `ConversationMessage` records. Each request creates or reuses a conversation, stores the user message with role `user`, generates a deterministic assistant reply, and stores that reply with role `assistant`.

Scheduling-aware replies are persisted in `ConversationMessage` content and `message_metadata`, including intent values such as `list_specialties`, `list_doctors`, `specialty_doctors`, availability guidance intents such as `availability_results` and `availability_missing_date`, hold flow intents such as `hold_created` and `hold_conflict`, and booking flow intents such as `booking_confirmed` and `booking_identity_missing`.

Assistant `message_metadata` may also include `chat_context` snapshots when availability guidance, hold flow, or booking confirmation updates conversational state. After a successful booking, `chat_context` may store `appointment_id`, but the authoritative appointment record remains in Postgres.

The future LLM layer should use conversation storage as context, but it should not own business rules.

Business rules remain in deterministic services.

## Future Work

Planned future implementation phases include:

- Fake LLM provider
- Structured output parser
- Natural-language date parsing
- Human escalation
- Hold expiration handling in chat
- Deterministic receptionist flow
- Conversation state machine
- Slot filling
- Retell webhook ingestion
- Cursor pagination for conversation messages