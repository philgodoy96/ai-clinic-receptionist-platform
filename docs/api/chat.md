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
- fallback

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
- Scheduling tool calls
- Appointment booking from chat
- Slot filling
- Conversation state machine
- Human escalation
- Retell webhook ingestion

Those capabilities are planned for later implementation phases.