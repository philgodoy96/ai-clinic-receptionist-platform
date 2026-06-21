# Chat API

## Context

The Chat API provides a deterministic chat receptionist foundation.

It records user messages and assistant replies in durable conversation storage.

Deterministic rules drive the public response. Optional LLM shadow analysis runs internally for observability only.

## Endpoint

Send a message:

    POST /api/v1/chat/messages

## Request

    {
      "message": "I need an appointment",
      "conversation_id": null,
      "patient_id": null,
      "conversation_metadata": {}
    }

If `conversation_id` is omitted, the backend creates a new chat conversation.

If `conversation_id` is provided, the backend appends the message to the existing conversation.

## Response

    {
      "conversation_id": "...",
      "user_message_id": "...",
      "assistant_message_id": "...",
      "intent": "appointment_request",
      "reply": "I can help with appointment scheduling. Please tell me the specialty or doctor you would like to see.",
      "appointment_id": null,
      "booking_confirmed": false
    }

When booking is confirmed, the response may include:

    {
      "conversation_id": "...",
      "user_message_id": "...",
      "assistant_message_id": "...",
      "intent": "booking_confirmed",
      "reply": "Your appointment with Dr. Emily Carter on 2026-07-02 at 09:00 has been booked. A confirmation email will be sent if an email address is available.",
      "appointment_id": "...",
      "booking_confirmed": true
    }

## LLM Shadow Analysis (Internal)

The backend may run optional LLM analysis in shadow mode while generating a reply.

The public API response does not expose LLM analysis. Response fields such as `intent`, `reply`, `appointment_id`, and `booking_confirmed` remain driven by the deterministic flow.

LLM analysis is stored only as internal assistant message metadata (`llm_shadow_analysis`). Clients calling `POST /api/v1/chat/messages` do not receive LLM analysis fields in the response body.

## LLM-Assisted Slot Filling (Internal)

When eligible LLM analysis is available, the backend may validate extracted scheduling and patient-identity fields and merge accepted values into internal `chat_context`.

The public API response remains deterministic. Response fields such as `intent`, `reply`, `appointment_id`, and `booking_confirmed` are not driven by raw LLM output.

Slot-filling results are stored only as internal assistant message metadata (`slot_filling`), including applied fields, rejected fields, and rejection reasons. This metadata is for observability and debugging; clients do not receive it in the response body.

Slot filling does not create holds, create bookings, or bypass identity or confirmation requirements.

## Human Handoff and Conversation Health

The public chat API may softly suggest human handoff when the conversation appears stuck, such as after repeated fallback responses or similar low-progress signals.

If the user explicitly asks to speak with a human, the assistant returns a handoff-style reply and the conversation may be marked `escalated` internally. Explicit human requests and medical emergencies can also create an internal `HumanEscalation` record for staff handoff, plus a durable internal `human_escalation_notification` email job. This phase does not send real staff email or use an LLM to impersonate a human.

Health and escalation signals are stored as internal assistant message metadata (`conversation_health`, `human_escalation` when a record is created, and `human_handoff_notification` when a notification job is created or reused). Clients calling `POST /api/v1/chat/messages` do not receive these fields in the response body.

Emergency responses remain deterministic and take priority over other health-driven reply changes.

## Supported Intents

The deterministic responder currently supports:

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
- human_escalation_requested
- escalation_suggested
- fallback

## Scheduling-Aware Responses

The deterministic chat responder can now answer read-only scheduling questions using backend scheduling data.

Supported examples:

    "What specialties do you have?"
    "Which doctors do you have?"
    "I need a dermatologist"
    "I need a cardiologist"

The chat API may return intents such as:

- list_specialties
- list_doctors
- specialty_doctors
- appointment_request

This implementation is read-only for specialty and doctor listing.

It does not:

- cancel appointments
- reschedule appointments
- call an LLM

## Availability Guidance

The chat API can provide deterministic, read-only availability guidance.

Supported examples:

    "Dr. Emily Carter availability"
    "Dr. Emily Carter on 2026-07-02"
    "Dermatology on 2026-07-02"
    "What times are available?"

Dates must currently use:

    YYYY-MM-DD

The API may return these intents:

- availability_request
- availability_missing_date
- availability_missing_doctor
- availability_results
- availability_no_slots
- invalid_date

This phase is read-only for availability lookup.

It does not:

- parse natural-language dates such as "tomorrow" or "next Monday"
- call an LLM

## Appointment Holds

The Chat API can create a temporary appointment hold after the user chooses a specific offered time.

Example flow:

1. User checks availability:

    {
      "message": "Dr. Emily Carter on 2026-07-02"
    }

2. Assistant returns available times.

3. User chooses a time:

    {
      "conversation_id": "...",
      "message": "I'll take 09:00"
    }

4. Assistant temporarily holds the slot.

The hold is not a booking.

The hold may expire.

The API may return these intents:

- hold_request
- hold_created
- hold_missing_availability
- hold_slot_not_found
- hold_conflict

This phase does not:

- send confirmation emails before booking is confirmed
- call an LLM

## Booking Confirmation

After a temporary hold is created, the Chat API can confirm the booking when the user provides patient identity and explicit confirmation.

Required patient identity fields:

- full_name
- date_of_birth
- phone
- email

The current deterministic parser supports simple structured messages such as:

    My name is Jane Doe, DOB 1990-01-15, phone +15551234567, email jane@example.com. Confirm.

The API may return these intents:

- booking_identity_missing
- booking_confirmation_required
- booking_confirmed
- booking_hold_missing
- booking_hold_expired
- booking_conflict

Booking confirmation:

- validates an existing hold
- creates a durable appointment
- releases the temporary hold after commit
- creates a confirmation email job
- publishes email dispatch after commit when enabled

This implementation still does not use an LLM.

Example flow after a hold is created:

1. User provides patient identity:

    {
      "conversation_id": "...",
      "message": "Jane Doe, 1990-05-15, +1 555-123-4567, jane.doe@example.com"
    }

2. Assistant asks for explicit confirmation (`booking_confirmation_required`).

3. User confirms:

    {
      "conversation_id": "...",
      "message": "Please confirm."
    }

4. Assistant confirms the booking (`booking_confirmed`).

Booking-related assistant outcomes return HTTP 200 because they are conversation results, not API protocol errors.

## Error Responses

HTTP errors use the standardized API error response envelope documented in:

    docs/api/error-responses.md

Examples:

- conversation_not_found
- invalid_conversation_message
- validation_error

## Current Limitations

This implementation does not yet include:

- LLM-driven reply or intent selection in the public API response
- Appointment cancellation from chat
- Appointment rescheduling from chat
- Natural-language date parsing
- Conversation state machine
- Escalation assignment workflow
- Retell webhook ingestion

Those capabilities are planned for later implementation phases.