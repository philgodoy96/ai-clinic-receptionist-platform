# LLM Evaluation Dataset

## Context

The receptionist uses LLM output as structured analysis candidates.

Provider output is untrusted and must be validated before it can influence chat context.

This implementation phase adds an offline evaluation dataset for structured receptionist analysis quality.

## Design Principle

Evaluation checks model-analysis quality.

It does not test business side effects.

It must run offline and deterministically in CI.

## Current Implementation

The evaluation foundation includes:

- synthetic JSONL dataset
- required `prompt_version` on each evaluation case
- expected structured analysis outputs
- recorded output comparison
- offline evaluation runner
- simple accuracy metrics
- metrics grouped by prompt version
- recorded mode (default, CI-safe)
- optional provider-run mode (manual, explicit)

## Evaluation Modes

### Recorded Mode

Recorded mode compares each case's `recorded_output` against `expected`.

It is the default mode and should be used in CI.

```powershell
python -m scripts.evaluate_receptionist_analysis --mode recorded --fail-on-errors
```

### Provider Mode

Provider mode runs each case through the configured LLM provider and compares the live analysis result against `expected`.

It requires `--allow-provider-calls` and may incur provider cost when a real provider is configured.

```powershell
python -m scripts.evaluate_receptionist_analysis --mode provider --allow-provider-calls
```

See [Provider-Run Evaluation Mode](provider-run-evaluation-mode.md) for safety boundaries, report output, and testing notes.

## Dataset Boundary

The dataset uses synthetic examples only.

It must not contain real patient data, PHI, credentials, raw provider secrets, or production conversations.

## Evaluation Scope

The evaluator checks:

- intent
- urgency
- requires_human
- safety flags
- extracted fields

It does not create holds, create appointments, send emails, notify staff, or call a real LLM provider in recorded mode.

Provider-run evaluation calls the configured LLM provider only when `--mode provider --allow-provider-calls` is used. It still does not create holds, appointments, emails, or staff notifications.

Historical prompt versions are accepted in the dataset so recorded outputs from earlier prompts can still be evaluated. Metrics are grouped by the `prompt_version` on each case.

## Running Evaluation

Recorded mode (default):

```powershell
python -m scripts.evaluate_receptionist_analysis --fail-on-errors
```

Equivalent explicit form:

```powershell
python -m scripts.evaluate_receptionist_analysis --mode recorded --fail-on-errors
```

## Why Recorded Outputs First

Recorded-output evaluation is deterministic, free, and safe for CI.

Provider-run evaluation is available as an optional manual mode for local quality checks against the configured provider.

## Future Work

Future implementation phases may add:

- per-intent thresholds
- confusion matrix
- golden outputs per model
- prompt regression reports
- tenant-level quality dashboards

See also: [Provider-Run Evaluation Mode](provider-run-evaluation-mode.md), [Real LLM Provider Adapter Boundary](real-llm-provider-adapter.md), [Structured-Output-Assisted Slot Filling](structured-output-slot-filling.md), [Prompt Versioning and LLM Traceability](prompt-versioning.md).
