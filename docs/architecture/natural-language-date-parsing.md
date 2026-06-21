# Natural-Language Date Parsing Boundary

## Context

The chat receptionist originally required dates in `YYYY-MM-DD` format.

This implementation phase adds deterministic parsing for simple natural-language date expressions.

## Design Principle

Natural-language dates are user input.

The parser normalizes them.

Scheduling validates availability.

Booking still requires hold, patient identity, and explicit confirmation.

## Supported Expressions

The parser supports:

- `today`
- `tomorrow`
- `this Monday`
- `next Monday`
- `in 3 days`
- ISO dates such as `2026-07-02`

## Unsupported Expressions

The parser intentionally does not support ambiguous expressions such as:

- `next week`
- `sometime soon`
- `later`
- `next month`
- `morning`
- `afternoon`

Unsupported expressions should trigger clarification rather than silently choosing a date.

## Clock Boundary

The parser uses an injectable clock so tests are deterministic.

Production systems should use the clinic's timezone when resolving relative dates.

## Side Effect Boundary

Date parsing does not:

- create appointment holds
- create appointments
- send emails
- call an LLM
- bypass emergency handling
- bypass booking confirmation

## Relationship to LLM Slot Filling

LLM structured output may suggest a date phrase.

The deterministic parser must normalize and validate it before it is applied to chat context.

## Future Work

Future implementation phases may add:

- clinic timezone settings
- date range parsing
- time-of-day preferences
- real provider-assisted date extraction
- clarification flows for ambiguous date ranges
