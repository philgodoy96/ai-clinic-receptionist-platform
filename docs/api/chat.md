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

- LLM understanding
- Appointment cancellation from chat
- Appointment rescheduling from chat
- Natural-language date parsing
- Slot filling
- Conversation state machine
- Human escalation
- Retell webhook ingestion

Those capabilities are planned for later implementation phases.