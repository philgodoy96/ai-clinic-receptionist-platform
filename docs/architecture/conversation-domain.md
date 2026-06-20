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

Conversation storage records interaction history.

Conversation metadata and message history do not own scheduling truth.

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

Scheduling-aware replies are persisted in `ConversationMessage` content and `message_metadata`, including intent values such as `list_specialties`, `list_doctors`, and `specialty_doctors`.

The future LLM layer should use conversation storage as context, but it should not own business rules.

Business rules remain in deterministic services.

## Future Work

Planned future implementation phases include:

- Chat availability guidance
- Chat appointment hold flow
- Chat booking confirmation flow
- Fake LLM provider
- Deterministic receptionist flow
- Conversation state machine
- Slot filling
- Retell webhook ingestion
- Human escalation workflow
- Cursor pagination for conversation messages