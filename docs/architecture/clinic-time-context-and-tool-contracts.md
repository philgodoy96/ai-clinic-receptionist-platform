# Clinic Time Context and Tool Contracts

## Context

Provider agents can misunderstand relative dates and current time.

Voice agents may say "tomorrow" or "next Tuesday" based on model inference rather than the clinic's actual calendar. Chat LLM slot filling may suggest dates that are in the past, on closed days, or outside business hours. Retell tool payloads may include legacy UTC `start_from` / `start_to` values calculated by the provider.

The backend must resolve scheduling time in clinic-local context and enforce business rules before querying availability, creating holds, or booking appointments.

## Design Principle

Provider agents may describe time, but backend services resolve and enforce scheduling time.

Prompt guidance helps the agent ask and speak naturally. Tool contracts and `ClinicTimeService` are the source of truth for what dates and times are valid.

## Timezone Model

The platform uses two time layers:

| Layer | Usage |
|-------|--------|
| **UTC** | Storage, logs, API timestamps, database `timestamptz` values |
| **Clinic timezone** | Human scheduling, relative date resolution, business hours, patient-facing wording |

Demo clinic defaults:

- **Timezone:** `America/New_York` (`CLINIC_TIMEZONE`)
- **Locale:** `en-US` (`CLINIC_LOCALE`)

`ClinicTimeService` converts the injectable application clock to clinic-local `datetime` and `date` values. Availability slots remain stored in UTC; scheduling queries convert resolved clinic-local windows to UTC before calling `SchedulingService.check_availability`.

See also: [Configuration](../configuration.md).

## Structured Date Expressions

Scheduling tools accept structured expressions instead of free-form provider date math.

### `DateExpressionKind`

| Kind | Fields | Meaning |
|------|--------|---------|
| `today` | — | Current clinic calendar date |
| `tomorrow` | — | Clinic date + 1 day |
| `this_weekday` | `weekday` | Named weekday in the current ISO week |
| `next_weekday` | `weekday` | Named weekday in the following ISO week |
| `next_week` | — | Start of the next ISO week (Monday) |
| `in_n_days` | `days_offset` (≥ 0) | Clinic date + N days |
| `exact_date` | `exact_date` (`YYYY-MM-DD`) | Fixed calendar date in clinic context |
| `unknown` | — | Unresolved; backend returns `invalid_scheduling_expression` |

`weekday` values: `monday` … `sunday`.

Resolution outcomes from `ClinicTimeService.resolve_date`:

| Status | Meaning |
|--------|---------|
| `resolved` | Valid future business day |
| `past_date` | Date is before clinic today |
| `closed_day` | Date falls on a non-business day |
| `unknown` | Missing or invalid expression fields |

### `TimeWindowExpressionKind`

| Kind | Fields | Meaning |
|------|--------|---------|
| `morning` | — | Standard morning window (08:00–12:00 label; intersected with clinic hours) |
| `afternoon` | — | Standard afternoon window (12:00–17:00 label) |
| `evening` | — | Standard evening window (17:00–20:00 label) |
| `exact_time` | `exact_time` (`HH:MM`, 24-hour) | Specific clinic-local time |
| `unknown` | — | Unresolved |

Resolution outcomes from `ClinicTimeService.resolve_time_window`:

| Status | Meaning |
|--------|---------|
| `resolved` | Window or exact time is valid |
| `outside_business_hours` | Time is before open or at/after close |
| `unknown` | Missing or malformed time |

Business hours use a **half-open interval**: `09:00` is valid; `17:00` is not (clinic closes at 17:00).

### Tool argument priority (`check_availability`)

1. `date_expression` (+ optional `time_window_expression`) — preferred contract
2. `requested_date_text` — legacy natural-language or ISO date text (parsed server-side)
3. Voice `voice_context.requested_date` — only when structured/legacy date fields are absent
4. Legacy `start_from` / `start_to` — backward compatible; date portion is still validated against clinic rules

`SchedulingAvailabilityResolver` converts resolved expressions into UTC query windows. The backend does not trust Retell-calculated datetimes without validation.

## Business Hours

Default demo clinic configuration:

| Setting | Default |
|---------|---------|
| Business days | Monday–Friday |
| Open | `09:00` clinic local |
| Close | `17:00` clinic local |

Environment variables: `CLINIC_BUSINESS_DAYS`, `CLINIC_BUSINESS_HOURS_START`, `CLINIC_BUSINESS_HOURS_END`.

Enforcement applies to:

- `check_availability` date and time resolution
- `hold_appointment_slot` slot start validation
- `reschedule_appointment` target slot validation
- Chat availability guidance when `ClinicTimeService` is configured

Error codes returned to Retell on violation:

| Code | Cause |
|------|--------|
| `past_date` | Resolved date before clinic today |
| `clinic_closed` | Resolved date on a non-business day |
| `outside_business_hours` | Time window or exact time outside hours |
| `invalid_scheduling_expression` | Unknown or incomplete expression |
| `clinic_time_unavailable` | `ClinicTimeService` not configured on adapter |

## get_clinic_context

Read-only Retell tool: `get_clinic_context`.

**Purpose:** Give the voice agent authoritative clinic calendar and hours context before discussing "today", relative dates, or scheduling windows.

**Arguments:** none

**Example response:**

```json
{
  "clinic_name": "Demo Clinic",
  "clinic_timezone": "America/New_York",
  "current_date": "2026-07-01",
  "current_weekday": "wednesday",
  "business_days": ["monday", "tuesday", "wednesday", "thursday", "friday"],
  "business_hours": {
    "start": "09:00",
    "end": "17:00"
  }
}
```

This tool has no scheduling side effects. It does not query doctors, slots, or holds.

## Retell Prompt Guidance

Use **[Retell Master Prompt v2](../operations/retell-master-prompt-v2.md)** (`retell-receptionist-v2`) as the canonical agent system prompt. Pair it with [Retell Tool Descriptions](../operations/retell-tool-descriptions.md), [Voice Smoke Scenarios](../operations/retell-voice-smoke-scenarios.md), and [Retell Dashboard Setup](../operations/retell-dashboard-setup.md) for dashboard configuration.

Recommended agent instructions (also enforced by backend contracts):

1. **Do not calculate relative dates yourself.** Do not infer "today", "tomorrow", or weekday names from model training data.
2. **Call `get_clinic_context` first** when the caller asks about today, current date, business hours, or before negotiating relative scheduling language.
3. **Use structured `date_expression`** (and optional `time_window_expression`) in `check_availability` tool calls. Prefer `next_weekday`, `tomorrow`, or `exact_date` over raw UTC timestamps.
4. **Backend tools are the source of truth** for availability, holds, booking, cancellation, and rescheduling. Do not confirm an appointment time until a hold or booking tool succeeds.
5. **Do not offer unavailable times.** If `check_availability` returns no slots or a scheduling error, ask the caller for another day or time window within business hours.
6. **Use natural scheduling language** — "appointment time", "opening", "schedule", "that time". Do not say "slot" or read internal IDs aloud.
7. **Patient identity** — follow existing vs new patient flows in the master prompt; backend enforces lookup (`VOICE_PATIENT_INTAKE_MODE=lookup_only`) or demo auto-create for `.test` emails.

The **prompt controls conversation flow** (questions, tone, tool timing, `end_call`). The **backend controls invariants** (clinic calendar, business hours, hold/booking rules). See [Who controls what](../operations/retell-dashboard-setup.md#who-controls-what) in the dashboard runbook.

Legacy `start_from` / `start_to` remain supported for compatibility but are not the preferred contract.

## Safety Boundary

Prompt guidance helps; backend enforces.

| Layer | Role |
|-------|------|
| Retell / LLM prompt | Natural conversation, tool selection, structured arguments, caller-facing recovery wording |
| Tool schemas | Validate argument shape before execution |
| `ClinicTimeService` | Resolve dates and times in clinic timezone |
| `SchedulingAvailabilityResolver` | Build validated UTC windows for queries |
| Retell tool adapter | Reject invalid windows; validate slot times on hold/reschedule |
| Business services | Holds, booking, cancellation, rescheduling invariants |
| Voice patient intake | `VOICE_PATIENT_INTAKE_MODE` — lookup-only vs demo auto-create for `.test` emails |

Even perfect prompt compliance cannot bypass backend validation. Invalid signatures block tool execution before the adapter runs (see [Retell Webhook Security](retell-webhook-security.md)).

Public demo operators must not collect real PHI. Use fictional sample contact information and seeded demo patients only (see [Retell Dashboard Setup](../operations/retell-dashboard-setup.md)).

## Current Implementation

The implementation includes:

- `CLINIC_*` settings with validation in `app/core/config.py`
- `ClinicTimeService` with injectable `Clock` / `FixedClock` for tests
- `DateExpressionSchema` and `TimeWindowExpressionSchema` in tool payloads
- `SchedulingAvailabilityResolver` for `check_availability`
- `get_clinic_context` on the Retell tool allowlist
- Chat availability integration via `ChatReceptionistService._validate_scheduling_date`
- Comprehensive tests in `tests/test_clinic_time_and_retell_tool_contracts.py`

## Relationship to Other Boundaries

- [Retell Tool-Calling Adapter](retell-tool-calling-adapter.md) — tool allowlist, execution, and clinic time enforcement on scheduling tools
- [Voice Conversation Bridge](voice-conversation-bridge.md) — safe `voice_context`; backend resolves dates rather than trusting stored provider text alone
- [Natural Language Date Parsing](natural-language-date-parsing.md) — legacy chat and `requested_date_text` path
- [Receptionist Response Generator](receptionist-response-generator.md) — phrasing only; does not resolve scheduling time

## Future Work

- Written chat reschedule with the same structured expression contract
- Per-clinic configuration API for multi-tenant deployments
