# Chat Scheduling Evaluation Harness

## Purpose

The chat scheduling evaluation harness runs **multi-turn offline scenarios** against the deterministic-first chat receptionist flow.

It validates that chat scheduling behavior remains safe and correct after changes to:

- deterministic chat orchestration
- LLM shadow analysis and slot filling
- LLM retry/repair and deterministic fallback
- booking-state transitions across multiple user messages

The harness is repository-local tooling. It does not change production chat behavior.

## Why Single-Turn Receptionist Analysis Eval Is Not Enough

The existing receptionist analysis evaluation (`evals/receptionist_analysis.jsonl`) checks **one message → one structured analysis object**:

- intent
- urgency
- `requires_human`
- safety flags
- extracted fields

That is the right boundary for model-analysis quality, but chat scheduling quality also depends on:

- conversation continuity across turns
- when booking is or is not confirmed
- whether an appointment was actually created
- safe public wording over multiple replies
- clarification behavior for vague dates
- graceful behavior when LLM analysis fails

A single-turn analysis eval cannot catch regressions such as:

- confirming a booking too early
- leaking internal IDs into user-visible replies
- inventing patient email addresses
- breaking chat after provider failure

See also: [LLM Evaluation Dataset](llm-evaluation-dataset.md).

## Receptionist Analysis Eval vs Chat Scheduling Eval

| | **Receptionist analysis evaluation** | **Chat scheduling evaluation** |
|---|---|---|
| **Dataset** | `evals/receptionist_analysis.jsonl` | `evals/chat_scheduling.jsonl` |
| **Unit under test** | Structured LLM analysis output | Full `ChatReceptionistService` multi-turn flow |
| **Turn shape** | Single user message | One or more steps per scenario |
| **Primary checks** | Intent, urgency, safety, extracted fields | Booking state, intent, semantic reply constraints |
| **Default mode** | Recorded-output comparison | In-memory deterministic service fixtures |
| **Provider mode** | Optional (`--mode provider`) | Not implemented yet |
| **CLI** | `python -m scripts.evaluate_receptionist_analysis` | `python -m scripts.evaluate_chat_scheduling` |

Both harnesses are offline by default, synthetic, and CI-safe.

## Dataset

Path:

```text
evals/chat_scheduling.jsonl
```

Each JSONL row is one scenario with:

- `id` — stable scenario identifier
- `description` — human-readable summary
- `fixture` — which in-memory service profile to use
- `tags` — optional labels for grouping
- `steps` — ordered list of user messages and semantic expectations

Example scenario shape:

```json
{
  "id": "booking_start_cardiology",
  "description": "Caller starts a cardiology booking request.",
  "fixture": "availability_guidance",
  "tags": ["booking", "cardiology", "deterministic"],
  "steps": [
    {
      "user_message": "I'd like to book an appointment with a cardiologist.",
      "expected_booking_confirmed": false,
      "expected_appointment_created": false,
      "expect_no_internal_identifiers": true,
      "expect_no_invented_email": true
    }
  ]
}
```

The dataset uses synthetic examples only. It must not contain real patient data, PHI, credentials, or production conversations.

## CLI Usage

Run the committed dataset:

```powershell
python -m scripts.evaluate_chat_scheduling
```

Fail the process when any scenario fails:

```powershell
python -m scripts.evaluate_chat_scheduling --fail-on-errors
```

Write a JSON report:

```powershell
python -m scripts.evaluate_chat_scheduling --output reports/evals/chat-scheduling.json
```

Run one scenario by id:

```powershell
python -m scripts.evaluate_chat_scheduling --scenario-id booking_start_cardiology
```

Optional dataset override:

```powershell
python -m scripts.evaluate_chat_scheduling --dataset path/to/custom.jsonl
```

### Exit behavior

- Default: exit `0` even when scenarios fail, but print a failed-scenario summary (same pattern as receptionist analysis eval).
- With `--fail-on-errors`: exit non-zero when one or more scenarios fail.

## Supported Fixture Profiles

Each scenario selects a fixture profile through the `fixture` field. Fixture builders live in `app/evals/chat_scheduling_fixtures.py` and use evaluation-only in-memory fakes (`_Eval*` repositories and services). They do not require PostgreSQL, Redis, RabbitMQ, or a running HTTP server.

| Fixture | Purpose |
|---|---|
| `availability_guidance` | Deterministic availability guidance flow with demo July scheduling data and fixed date parsing clock |
| `structured_slot_filling` | Chat flow with `FakeLLMProvider` for assistive analysis and structured slot filling |
| `llm_failure_fallback` | Chat flow where LLM analysis always fails, exercising deterministic fallback |

Unsupported fixture names produce a clear evaluation error. The CLI does not fake success.

## Evaluation Invariants

- **Offline by default** — no live provider calls during normal evaluation runs
- **No real provider calls** — uses `FakeLLMProvider` or an eval-only raising provider stub
- **No API keys required**
- **No HTTP server required**
- **Deterministic-first chat remains authoritative** — holds, booking, and public replies come from backend services
- **LLM is assistive/shadow only** — LLM output may pre-fill validated context but does not own durable scheduling actions

## Semantic Expectation Style

Scenarios avoid brittle exact-match assertions on full assistant wording.

Prefer semantic checks such as:

| Expectation | Meaning |
|---|---|
| `expected_intent` | Public chat intent after the step |
| `expected_booking_confirmed` | Whether booking was confirmed too early or correctly withheld |
| `expected_appointment_created` | Whether a durable appointment exists |
| `expected_reply_contains_any` | Reply includes at least one safe phrase (for example a time token) |
| `expected_reply_not_contains_any` | Reply must not include forbidden phrases (for example `booked`) |
| `expect_no_internal_identifiers` | No UUIDs or internal terms in the reply |
| `expect_no_invented_email` | Reply must not invent email addresses the user did not provide |
| `expect_clarification_wording` | Vague date requests should ask for clarification |

Implementation: `app/evals/chat_scheduling.py`.

## Report Shape

When `--output` is provided, the CLI writes JSON with:

- `dataset` — path to the JSONL file
- `total_scenarios`
- `passed_scenarios`
- `failed_scenarios`
- `scenarios` — per-scenario results

Each scenario result includes:

- `scenario_id`
- `description`
- `fixture`
- `passed`
- `step_count`
- `failure_reason`
- `failed_expectations` — list of `{ step_index, field, message, expected, actual }`

The report intentionally excludes:

- raw provider prompts
- API keys or secret-like fields
- full assistant reply text

## Current Limitations

- **Booking-focused only** — scenarios cover booking start, availability guidance, identity collection, a full booking confirmation happy path, and LLM failure fallback
- **No chat cancellation or rescheduling scenarios yet**
- **No provider mode yet** — cannot compare live Groq/Bedrock outputs against expectations
- **In-memory eval fixtures** — not the DB-backed production stack; validates chat orchestration behavior, not deployment integration
- **No per-prompt-version accuracy metrics** — unlike receptionist analysis eval

## Future Work

- Optional provider mode for Groq/Bedrock with explicit opt-in and cost awareness
- Richer response-quality scoring beyond semantic guards
- Chat cancellation and rescheduling scenarios
- CI integration if the team wants `--fail-on-errors` in pull-request checks
- Prompt-version traceability for chat eval runs

## Related Documentation

- [LLM Evaluation Dataset](llm-evaluation-dataset.md) — single-turn structured analysis evaluation
- [Provider-Run Evaluation Mode](provider-run-evaluation-mode.md) — optional live provider mode for analysis eval
- [Chat LLM Interpretation Reliability](chat-llm-reliability.md) — deterministic-first chat and LLM assist boundaries
- [Structured-Output-Assisted Slot Filling](structured-output-slot-filling.md)
- [Chat API Foundation](chat-api-foundation.md)
