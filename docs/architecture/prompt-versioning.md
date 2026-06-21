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
- runtime `prompt_version` in LLM metadata
- evaluation dataset `prompt_version`
- evaluation metrics grouped by prompt version

## Storage and Registry Boundary

Runtime stores only `prompt_version`.

The prompt registry maps each `prompt_version` to prompt metadata such as name, schema, and the versioned prompt module path.

Versioned prompt modules keep the actual prompt text in Git.

Full prompts are not persisted in conversation metadata.

## Runtime Metadata

Assistant message metadata includes `prompt_version` under `llm_shadow_analysis`.

The system does not store the full prompt text in conversation metadata.

## Evaluation Traceability

Each evaluation case includes a prompt version.

This allows recorded outputs and metrics to be tied to the prompt that produced them.

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
- provider-run evaluation mode
- prompt regression reports
- prompt A/B testing
- prompt rollback strategy
- prompt version telemetry dashboards

See also: [Real LLM Provider Adapter Boundary](real-llm-provider-adapter.md), [LLM Evaluation Dataset](llm-evaluation-dataset.md), [Structured-Output-Assisted Slot Filling](structured-output-slot-filling.md).
