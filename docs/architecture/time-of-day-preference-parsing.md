# Time-of-Day Preference Parsing Boundary

## Context

Users often express scheduling preferences using broad time-of-day language.

This implementation phase adds deterministic parsing for simple time windows.

## Design Principle

Time-of-day preference narrows availability.

It does not reserve a slot.

It does not create an appointment.

It does not replace explicit time selection.

## Supported Preferences

The demo supports:

- morning: 08:00-12:00
- afternoon: 12:00-17:00
- evening: 17:00-20:00

These are demo windows. Production systems should load them from clinic settings.

## Unsupported Preferences

The parser intentionally does not support ambiguous phrases such as:

- after lunch
- before noon
- early morning
- late afternoon
- around 2
- any time

Unsupported expressions should trigger clarification.

## Availability Filtering

When a requested time window exists, availability results are filtered before being offered to the user.

Only offered slots can later be held.

## Side Effect Boundary

Time preference parsing does not:

- create appointment holds
- create appointments
- send emails
- call an LLM
- bypass emergency handling
- bypass booking confirmation

## Relationship to Slot Filling

LLM structured output may suggest a broad time phrase.

The deterministic parser must normalize and validate it before it is applied to chat context.

See also: [Natural-Language Date Parsing Boundary](natural-language-date-parsing.md), [Structured-Output-Assisted Slot Filling](structured-output-slot-filling.md).
