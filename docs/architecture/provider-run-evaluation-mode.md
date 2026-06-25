# Provider-Run Evaluation Mode

## Context

The platform includes an offline evaluation dataset for structured receptionist analysis.

Recorded-output evaluation is deterministic and CI-safe.

This implementation phase adds an optional manual provider-run mode.

## Design Principle

Provider-run evaluation is manual and explicit.

CI remains offline, deterministic, and free.

## Modes

### Recorded Mode

Recorded mode compares dataset `recorded_output` fields against expected outputs.

It is the default and should be used in CI.

```powershell
python -m scripts.evaluate_receptionist_analysis --mode recorded --fail-on-errors
```

### Provider Mode

Provider mode runs the dataset against the configured LLM provider through the same reliability orchestration layer used in chat shadow analysis:

- bounded primary attempts
- optional fallback provider when enabled
- deterministic fallback after exhaustion
- `failure_reason`, `failure_category`, and attempt metadata on failed cases

With Groq configured as the primary provider, provider mode calls Groq only when both `--mode provider` and `--allow-provider-calls` are set and `GROQ_API_KEY` / `GROQ_MODEL` are present in the environment.

For `llama-3.3-70b-versatile`, prefer `GROQ_RESPONSE_FORMAT=json_object` unless another model supports strict schema output. Provider eval reports record the live `prompt_version` (currently `receptionist-analysis-v2`).

Example Groq configuration for a manual provider-run check:

```env
LLM_PRIMARY_PROVIDER=groq
GROQ_API_KEY=gsk_...
GROQ_MODEL=llama-3.3-70b-versatile
GROQ_RESPONSE_FORMAT=json_object
LLM_MAX_PRIMARY_ATTEMPTS=2
LLM_FALLBACK_ENABLED=false
```

```powershell
python -m scripts.evaluate_receptionist_analysis --mode provider --allow-provider-calls --fail-on-errors
```

It requires explicit confirmation:

```powershell
python -m scripts.evaluate_receptionist_analysis --mode provider --allow-provider-calls
```

Provider mode may cost money when a real provider is configured.

## Report Output

A local JSON report can be written:

```powershell
python -m scripts.evaluate_receptionist_analysis --mode provider --allow-provider-calls --output reports/evals/provider-run.json
```

Reports must not include credentials or secrets.

## Safety Boundary

Provider-run evaluation does not:

- create holds
- create appointments
- send emails
- notify staff
- call business tools
- require database
- require Redis
- require RabbitMQ

## Testing Boundary

Automated tests do not call real Groq or Bedrock.

Tests use fake or stub providers.

Recorded mode remains offline and does not instantiate any real provider adapter.

## Future Work

Future implementation phases may add:

- prompt comparison reports
- provider/model comparison
- per-intent thresholds
- confusion matrix
- cost estimates
- regression report artifacts

See also: [Groq LLM Provider](groq-llm-provider.md), [LLM Evaluation Dataset](llm-evaluation-dataset.md), [Real LLM Provider Adapter Boundary](real-llm-provider-adapter.md), [Prompt Versioning and LLM Traceability](prompt-versioning.md), [LLM Reliability Orchestration](llm-reliability-orchestration.md).
