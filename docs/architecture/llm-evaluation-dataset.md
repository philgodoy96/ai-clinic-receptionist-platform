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
- expected structured analysis outputs
- recorded output comparison
- offline evaluation runner
- simple accuracy metrics

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

It does not create holds, create appointments, send emails, notify staff, or call a real LLM provider.

## Running Evaluation

```powershell
python -m scripts.evaluate_receptionist_analysis --fail-on-errors
```

## Why Recorded Outputs First

Recorded-output evaluation is deterministic, free, and safe for CI.

Real provider evaluation can be added later as an optional local/manual mode.

## Future Work

Future implementation phases may add:

- provider-run evaluation mode
- prompt versioning
- per-intent thresholds
- confusion matrix
- golden outputs per model
- model regression reports
- tenant-level quality dashboards

See also: [Real LLM Provider Adapter Boundary](real-llm-provider-adapter.md), [Structured-Output-Assisted Slot Filling](structured-output-slot-filling.md).
