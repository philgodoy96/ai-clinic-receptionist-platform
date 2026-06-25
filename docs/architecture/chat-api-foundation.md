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
- availability_no_matching_time_window
- invalid_date
- invalid_time_preference
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
- human_escalation_requested
- escalation_suggested
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

When useful scheduling context exists in `chat_context`, analysis requests include a **sanitized context snapshot** so the model can interpret the latest user message without receiving raw PII, IDs, or full transcript history. The snapshot does not authorize durable actions.

When model output fails structurally, the analysis service retries internally with a repair prompt before falling back to deterministic analysis. User-facing chat behavior remains deterministic-first.

Shadow analysis uses `FakeLLMProvider` by default. A real provider such as Groq or Bedrock may be selected through configuration, but the same reliability and safety boundaries apply.

Results are persisted on the assistant message as internal `llm_shadow_analysis` metadata, including classified intent, confidence, urgency, safety flags, reliability signals, and token/cost fields.

Shadow metadata intentionally excludes:

- raw prompts
- raw provider output
- extracted patient identity

This enables observability and comparison between deterministic behavior and LLM classification without changing successful API response semantics.

See also: [Chat LLM Interpretation Reliability](chat-llm-reliability.md).

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

## Natural-Language Date Boundary

The deterministic chat flow and LLM slot filling may resolve simple date phrases through `NaturalLanguageDateParser`.

The parser:

- normalizes supported expressions such as `today`, `tomorrow`, `this Monday`, `next Monday`, `in 3 days`, and ISO dates to `YYYY-MM-DD`
- rejects unsupported or ambiguous phrases with clarification instead of silently choosing a date
- uses an injectable clock so tests remain deterministic

Date parsing does not:

- create Redis holds
- create appointments
- send emails
- call an LLM
- bypass emergency handling
- bypass booking confirmation

When LLM slot filling suggests a date phrase, the deterministic parser normalizes and validates it before it is applied to `chat_context`. The deterministic availability flow uses the same parser when extracting dates from user messages.

Assistant message metadata may include `date_parsing` when a date expression was processed.

See also: [Natural-Language Date Parsing Boundary](natural-language-date-parsing.md).

## Time-of-Day Preference Boundary

The deterministic chat flow and LLM slot filling may resolve broad time-of-day phrases through `TimePreferenceParser`.

The parser:

- normalizes supported preferences such as `morning`, `afternoon`, and `evening` into `requested_time_window`
- rejects unsupported or ambiguous phrases with clarification instead of silently choosing a window
- filters availability results before offered slots are stored in `chat_context`

Time preference parsing does not:

- create Redis holds
- create appointments
- send emails
- call an LLM
- bypass emergency handling
- bypass booking confirmation
- replace explicit time selection such as `I'll take 09:00`

When LLM slot filling suggests a broad time phrase, the deterministic parser normalizes and validates it before it is applied to `chat_context`. The deterministic availability flow uses the same parser when extracting preferences from user messages.

Assistant message metadata may include `time_preference` when a time-of-day expression was processed.

See also: [Time-of-Day Preference Parsing Boundary](time-of-day-preference-parsing.md).

## Conversation Health Boundary

After the deterministic reply and `chat_context` updates are resolved, `ChatReceptionistService` may evaluate conversation health through `ConversationHealthService` before the assistant message is persisted.

Health evaluation is deterministic. It uses:

- the current user message
- the current or updated `conversation_metadata.chat_context`
- a bounded window of recent conversation messages

Results are persisted on the assistant message as internal `conversation_health` metadata, including signal counts, escalation flags, and escalation reason.

Conversation health does not:

- notify a real human
- use another LLM to impersonate a human
- create holds or appointments
- override emergency deterministic responses
- append handoff suggestions to successful booking, availability, or hold outcomes

Suggested escalation alone does not create `HumanEscalation` records.

When the user explicitly asks for a human, the deterministic reply becomes a handoff-style message and the conversation status may be updated to `escalated` when supported. Immediate signals may also create a durable `HumanEscalation` record; see Human Escalation Boundary. No durable human queue exists in this phase.

When repeated fallback, slot-filling rejection, low-confidence shadow analysis, booking conflict, or no-progress signals are detected, the assistant may append a soft handoff suggestion only for fallback or otherwise stuck responses.

Emergency language still wins over other health-driven reply changes.

## Human Escalation Boundary

When conversation health detects an immediate escalation signal, `ChatReceptionistService` creates or reuses a durable `HumanEscalation` record through `HumanEscalationService`.

Immediate escalation applies when:

- the user explicitly requests a human, or
- a medical emergency signal is detected

The chat layer:

- returns a deterministic handoff-style reply (or emergency reply for medical emergencies)
- records `human_escalation` metadata on the assistant message
- builds `handoff_context` from safe operational fields in `chat_context`

The chat layer does not:

- notify a real receptionist or staff member
- assign the escalation to a human agent
- use an LLM to impersonate a human
- release Redis holds automatically during escalation
- create a `HumanEscalation` record for suggested escalation alone

Durable escalation lifecycle management (acknowledge, resolve, cancel) is available through the internal human-escalations API. No human queue or dashboard exists in this phase.

See also: [Conversation Health and Escalation Signals](conversation-health.md).

## Future Work

Planned future implementation phases include:

- Human handoff notification job
- Escalation assignment/resolution workflow
- Real provider adapter
- Clinic timezone settings
- Voice provider transfer integration
- Cost tracking aggregation
- Conversation state machine
- Hold expiration handling in chat
- Retell webhook ingestion
- Chat message/client idempotency
- Durable action idempotency for duplicate chat POSTs
- Dedicated LLM run persistence table
- LLM scheduling evaluation harness
- Written chat cancellation and rescheduling