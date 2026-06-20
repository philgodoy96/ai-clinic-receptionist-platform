# Chat API

## Context

The Chat API provides a deterministic chat receptionist foundation.

It records user messages and assistant replies in durable conversation storage.

This implementation does not use an LLM yet.

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
      "reply": "I can help with appointment scheduling. Please tell me the specialty or doctor you would like to see."
    }

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

- book appointments
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

- create appointments
- confirm bookings
- call an LLM
- parse natural-language dates such as "tomorrow" or "next Monday"

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

- create appointments
- confirm bookings
- send confirmation emails
- call an LLM
- collect full patient identity validation

## Error Responses

HTTP errors use the standardized API error response envelope documented in:

    docs/api/error-responses.md

Examples:

- conversation_not_found
- invalid_conversation_message
- validation_error

## Current Limitations

This implementation does not yet include:

- LLM understanding
- Appointment booking from chat
- Appointment cancellation from chat
- Appointment rescheduling from chat
- Natural-language date parsing
- Slot filling
- Conversation state machine
- Human escalation
- Retell webhook ingestion

Those capabilities are planned for later implementation phases.