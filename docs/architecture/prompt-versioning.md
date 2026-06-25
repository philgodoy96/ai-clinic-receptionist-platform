# Prompt Versioning and LLM Traceability

## Context

Prompt changes can change LLM behavior.

The platform needs to know which prompt version produced a structured receptionist analysis.

## Design Principle

Prompt versions make LLM behavior traceable.

They do not make LLM output trusted.

The backend reliability boundary remains the source of truth.

## Current Implementation

The current implementation includes:

- receptionist prompt version registry
- current prompt version: `receptionist-analysis-v1`
- prompt metadata for receptionist analysis
- runtime `prompt_version` in LLM metadata across primary retries, fallback provider attempts, and deterministic fallback results
- evaluation dataset `prompt_version`
- evaluation metrics grouped by prompt version
- provider-run evaluation reports include `prompt_version` per case and in summary metrics

## Storage and Registry Boundary

Runtime stores only `prompt_version`.

The prompt registry maps each `prompt_version` to prompt metadata such as name, schema, and the versioned prompt module path.

Versioned prompt modules keep the actual prompt text in Git.

Full prompts are not persisted in conversation metadata.

## Runtime Metadata

Assistant message metadata includes `prompt_version` under `llm_shadow_analysis`.

The same `prompt_version` is recorded whether the analysis succeeds on the first primary attempt, after a primary retry, after a fallback provider attempt, or after deterministic fallback.

The system does not store the full prompt text in conversation metadata.

## Evaluation Traceability

Each evaluation case includes a prompt version.

This allows recorded outputs and metrics to be tied to the prompt that produced them.

Provider-run evaluation reports include `prompt_version` from the live analysis result for each case, plus prompt-version grouped metrics in the JSON report when `--output` is used.

## Safety Boundary

Prompt versioning does not allow the LLM to:

- create appointment holds
- create appointments
- send emails
- assign escalations
- impersonate a human
- bypass emergency handling
- bypass booking confirmation

## Future Work

Future implementation phases may add:

- prompt changelog
- prompt regression reports
- prompt A/B testing
- prompt rollback strategy
- prompt version telemetry dashboards

See also: [Provider-Run Evaluation Mode](provider-run-evaluation-mode.md), [Real LLM Provider Adapter Boundary](real-llm-provider-adapter.md), [LLM Evaluation Dataset](llm-evaluation-dataset.md), [Structured-Output-Assisted Slot Filling](structured-output-slot-filling.md), [LLM Reliability Orchestration](llm-reliability-orchestration.md), [Chat LLM Interpretation Reliability](chat-llm-reliability.md).
