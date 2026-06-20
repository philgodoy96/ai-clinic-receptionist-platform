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

The chat API must not create appointments unless it explicitly calls scheduling services in a future implementation phase.

## Scheduling-Aware Read Boundary

The chat layer may read scheduling data through SchedulingService.

It must not mutate scheduling state in this implementation phase.

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
- fallback

## Availability Read Boundary

Availability guidance reads scheduling data through SchedulingService.

It does not mutate scheduling state.

Showing a slot to the user is not the same as reserving it.

A later implementation phase will add hold creation and booking confirmation.

## Safety Boundary

The chat responder does not provide diagnosis or clinical advice.

Emergency language is handled with safe guidance to contact emergency services or go to the nearest emergency room.

## Future Work

Planned future implementation phases include:

- Chat appointment hold flow
- Chat booking confirmation flow
- Conversation state machine
- Slot filling
- Fake LLM provider
- Structured LLM output parsing
- Natural-language date parsing
- Human escalation
- Retell webhook ingestion