# Chat API Foundation

## Context

The AI Clinic Receptionist Platform needs a chat interface before introducing LLM orchestration.

This implementation provides a deterministic chat foundation.

## Design

The chat API:

1. Receives a user message.
2. Creates or reuses a conversation.
3. Stores the user message.
4. Generates a deterministic assistant response.
5. Stores the assistant response.
6. Returns the response to the client.

## Why No LLM Yet

The project intentionally starts with deterministic behavior.

This keeps the system:

- testable
- cheap to run
- predictable
- safe from hallucinated scheduling actions

LLM orchestration will be introduced after the conversation and chat API boundaries are stable.

## Responsibility Boundary

Conversation storage records interaction history.

Scheduling truth remains in scheduling services and tables.

The chat API must not create appointments unless it explicitly calls scheduling services through `AppointmentBookingService`.

## Booking Boundary

The chat layer does not manually create appointments.

It delegates durable appointment creation to `AppointmentBookingService`.

Transaction order:

1. Validate hold and identity.
2. Create appointment through booking service.
3. Create confirmation email job.
4. Commit database transaction.
5. Release Redis hold.
6. Publish email dispatch best-effort.

If RabbitMQ dispatch fails after commit, booking remains confirmed because PostgreSQL is the durable source of truth.

## Scheduling-Aware Read Boundary

The chat layer may read scheduling data through SchedulingService.

It must not mutate durable Postgres scheduling state in this implementation phase.

Temporary slot holds are created separately in Redis after the user chooses a specific offered slot.

Scheduling truth remains in scheduling tables.

Chat conversation history records what the user asked and what the assistant answered.

## Current Intents

The deterministic responder supports:

- greeting
- appointment_request
- cancel_request
- reschedule_request
- emergency
- list_specialties
- list_doctors
- specialty_doctors
- availability_request
- availability_missing_date
- availability_missing_doctor
- availability_results
- availability_no_slots
- invalid_date
- hold_request
- hold_created
- hold_missing_availability
- hold_slot_not_found
- hold_conflict
- booking_identity_missing
- booking_confirmation_required
- booking_confirmed
- booking_hold_missing
- booking_hold_expired
- booking_conflict
- fallback

## Availability Read Boundary

Availability guidance reads scheduling data through SchedulingService.

It does not mutate durable scheduling state.

Showing a slot to the user is not the same as reserving it.

## Temporary Hold Boundary

The chat layer can create a Redis appointment hold only after the user chooses a specific offered slot.

A hold is temporary and does not represent a confirmed appointment.

Durable appointment creation is delegated to `AppointmentBookingService` after the user provides patient identity and explicit confirmation.

Showing a slot:
- read-only

Holding a slot:
- temporary Redis mutation

Booking a slot:
- durable Postgres mutation

## Safety Boundary

The chat responder does not provide diagnosis or clinical advice.

Emergency language is handled with safe guidance to contact emergency services or go to the nearest emergency room.

## LLM Shadow Analysis

`ChatReceptionistService` may invoke `LLMReceptionistAnalysisService` in shadow mode after the user message is stored and before the deterministic reply is generated.

The deterministic flow remains the source of behavior:

- Assistant intent and reply text come from the deterministic responder and scheduling/booking services.
- Holds, bookings, identity collection, and confirmation gates are unchanged.
- LLM analysis does not create holds, create bookings, or bypass identity or confirmation requirements.

Shadow analysis uses `FakeLLMProvider` only in this phase. No real provider calls are made.

Results are persisted on the assistant message as internal `llm_shadow_analysis` metadata, including classified intent, confidence, urgency, safety flags, reliability signals, and token/cost fields.

Shadow metadata intentionally excludes:

- raw prompts
- raw provider output
- extracted patient identity

This enables observability and comparison between deterministic behavior and LLM classification without changing successful API response semantics.

## LLM-Assisted Slot Filling Boundary

When LLM analysis is eligible, `ChatReceptionistService` may call `LLMChatSlotFillingService` after analysis and before the deterministic reply is generated.

The slot-filling layer:

- validates extracted specialty, doctor, date, time, and patient identity fields
- merges only accepted values into `conversation_metadata.chat_context`
- records applied and rejected fields as internal assistant message metadata (`slot_filling`)

The slot-filling layer does not:

- create Redis holds
- create appointments
- resolve patients from the database
- bypass patient identity completeness
- bypass explicit confirmation
- change public API response fields such as `intent`, `reply`, `appointment_id`, or `booking_confirmed`

Deterministic intent selection, reply text, hold creation, and booking confirmation remain unchanged. Validated slot filling may pre-fill conversational context so the deterministic flow can continue with fewer missing fields.

Slot filling is skipped when analysis used fallback, failed reliability checks, confidence is below threshold, intent is emergency, or safety flags are present.

See also: [Structured-Output-Assisted Slot Filling](structured-output-slot-filling.md).

## Future Work

Planned future implementation phases include:

- Conversation health and escalation signals
- Human escalation foundation
- Real provider adapter
- LLM reliability and fallbacks
- Cost tracking aggregation
- Natural-language date parsing
- Conversation state machine
- Hold expiration handling in chat
- Retell webhook ingestion