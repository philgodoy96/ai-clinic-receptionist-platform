# Configuration

Application settings are loaded from environment variables and an optional local `.env` file through Pydantic settings.

See `.env.example` for a safe local template and `.env.demo.example` for a hosted public demo deployment template.

## Application

| Variable | Default | Description |
|----------|---------|-------------|
| `APP_NAME` | `ai-clinic-receptionist-platform` | Application name |
| `APP_ENV` | `local` | Environment label |
| `APP_DEBUG` | `true` | FastAPI debug mode |
| `API_V1_PREFIX` | `/api/v1` | API route prefix |

## Clinic Time and Business Hours

Clinic timezone and business hours are used for scheduling-aware receptionist behavior: relative date resolution, business-day validation, hours-of-operation enforcement on scheduling tools, and the read-only `get_clinic_context` Retell tool.

| Variable | Default | Description |
|----------|---------|-------------|
| `CLINIC_TIMEZONE` | `America/New_York` | IANA timezone for clinic-local scheduling context |
| `CLINIC_BUSINESS_DAYS` | `monday,tuesday,wednesday,thursday,friday` | Comma-separated weekday list (`monday`–`sunday`, case-insensitive, no duplicates) |
| `CLINIC_BUSINESS_HOURS_START` | `09:00` | Clinic opening time in 24-hour `HH:MM` format |
| `CLINIC_BUSINESS_HOURS_END` | `17:00` | Clinic closing time in 24-hour `HH:MM` format. Must be after start |
| `CLINIC_NAME` | `Demo Clinic` | Optional display name for the fictional demo clinic |
| `CLINIC_LOCALE` | `en-US` | Optional locale for clinic-facing formatting |

For local development and CI, keep:

```env
CLINIC_TIMEZONE=America/New_York
CLINIC_BUSINESS_DAYS=monday,tuesday,wednesday,thursday,friday
CLINIC_BUSINESS_HOURS_START=09:00
CLINIC_BUSINESS_HOURS_END=17:00
```

Voice agents should call `get_clinic_context` before discussing relative dates. Scheduling tools prefer structured `date_expression` arguments; the backend resolves and enforces clinic time regardless of provider prompt behavior.

See also: [Clinic Time Context and Tool Contracts](architecture/clinic-time-context-and-tool-contracts.md).

## Database and Infrastructure

| Variable | Default | Description |
|----------|---------|-------------|
| `DATABASE_URL` | local PostgreSQL URL | SQLAlchemy database URL |
| `REDIS_URL` | `redis://localhost:6379/0` | Redis connection URL |
| `RABBITMQ_URL` | local RabbitMQ URL | RabbitMQ connection URL |
| `APPOINTMENT_HOLD_TTL_SECONDS` | `300` | Redis hold TTL in seconds |

## Email Jobs

Postgres `EmailJob` records are the durable source of truth for delivery state. RabbitMQ carries wake-up messages only (`email_job_id`). Retry scheduling uses `next_attempt_at`; RabbitMQ TTL retry queues are not used for provider failures.

Email delivery is **at-least-once**. Exactly-once delivery across Postgres and external providers is not guaranteed. When `EMAIL_PROVIDER=resend`, the worker passes `EmailJob.idempotency_key` (or an `email_job:{id}` fallback) to Resend as an `Idempotency-Key` header.

| Variable | Default | Description |
|----------|---------|-------------|
| `EMAIL_PROVIDER` | `fake` | Email provider selection: `fake` or `resend` |
| `EMAIL_FROM_ADDRESS` | `clinic-demo@example.test` | Sender address required when `EMAIL_PROVIDER=resend` |
| `EMAIL_REPLY_TO` | empty | Optional reply-to address for Resend |
| `EMAIL_PROVIDER_REQUEST_TIMEOUT_SECONDS` | `10` | HTTP timeout for the Resend client |
| `EMAIL_JOB_DISPATCH_ENABLED` | `false` | Enable RabbitMQ dispatch after durable email jobs are created |
| `EMAIL_JOB_QUEUE_NAME` | `email_jobs` | RabbitMQ queue name for email jobs |
| `EMAIL_JOB_MAX_ATTEMPTS` | `3` | Maximum delivery attempts before a job becomes `failed` |
| `EMAIL_JOB_BACKOFF_BASE_SECONDS` | `30` | Base delay for exponential retry backoff |
| `EMAIL_JOB_BACKOFF_MAX_SECONDS` | `900` | Maximum retry backoff delay in seconds |
| `EMAIL_JOB_LOCK_TTL_SECONDS` | `300` | Worker lock duration while a job is `processing` |
| `RESEND_API_KEY` | empty | Resend API key when `EMAIL_PROVIDER=resend` |
| `HUMAN_ESCALATION_NOTIFICATION_EMAIL` | demo staff email | Default staff notification recipient |

For local development and CI, keep:

```env
EMAIL_PROVIDER=fake
EMAIL_JOB_DISPATCH_ENABLED=false
```

Optional Resend configuration for a hosted public demo:

```env
EMAIL_PROVIDER=resend
RESEND_API_KEY=re_...
EMAIL_FROM_ADDRESS=Clinic <noreply@example.com>
```

See also: [Email Dispatch Reliability](architecture/email-dispatch-reliability.md).

## LLM Provider

| Variable | Default | Description |
|----------|---------|-------------|
| `LLM_PROVIDER` | `fake` | LLM provider selection: `fake`, `groq`, or `bedrock`. Kept for backward compatibility; see `LLM_PRIMARY_PROVIDER`. |
| `LLM_PRIMARY_PROVIDER` | empty | Optional explicit primary provider. When unset, `LLM_PROVIDER` is used. |
| `LLM_ENABLED` | `true` | Enable LLM shadow analysis and slot-filling assistance |
| `LLM_MAX_PRIMARY_ATTEMPTS` | `2` | Bounded primary provider attempts per analysis (`1`–`3`) |
| `LLM_FALLBACK_ENABLED` | `false` | Enable optional fallback LLM provider after primary exhaustion |
| `LLM_FALLBACK_PROVIDER` | empty | Required when `LLM_FALLBACK_ENABLED=true` (`fake`, `groq`, or `bedrock`) |
| `LLM_MAX_FALLBACK_ATTEMPTS` | `1` | Bounded fallback provider attempts per analysis (`1`–`2`) |
| `GROQ_API_KEY` | empty | Required when primary or fallback provider is `groq` |
| `GROQ_MODEL` | empty | Required when primary or fallback provider is `groq` |
| `GROQ_BASE_URL` | `https://api.groq.com/openai/v1` | Groq OpenAI-compatible API base URL |
| `GROQ_REQUEST_TIMEOUT_SECONDS` | `10` | Groq HTTP request timeout (`>= 1`) |
| `GROQ_MAX_OUTPUT_TOKENS` | `800` | Default Groq max output tokens (`>= 1`) |
| `GROQ_TEMPERATURE` | `0` | Default Groq inference temperature (`0`–`2`) |
| `GROQ_RESPONSE_FORMAT` | `json_schema` | Groq structured output mode: `json_schema`, `json_object`, or `none` |
| `BEDROCK_MODEL_ID` | empty | Required when primary or fallback provider is `bedrock` |
| `AWS_REGION` | `us-east-1` | AWS region for Bedrock runtime client |
| `BEDROCK_REQUEST_TIMEOUT_SECONDS` | `10` | Bedrock request timeout |
| `BEDROCK_MAX_RETRIES` | `0` | Bedrock client retry count |
| `BEDROCK_TEMPERATURE` | `0` | Default Bedrock inference temperature |
| `BEDROCK_MAX_TOKENS` | `800` | Default Bedrock max output tokens |

Groq and AWS credentials are not stored in the repository. When using Groq or Bedrock, provide credentials through environment variables, shared config/profile, or your deployment secret manager at runtime.

For local development and CI, keep:

```env
LLM_PROVIDER=fake
LLM_MAX_PRIMARY_ATTEMPTS=2
LLM_FALLBACK_ENABLED=false
```

Optional Groq configuration for a hosted public demo:

```env
LLM_PRIMARY_PROVIDER=groq
GROQ_API_KEY=gsk_...
GROQ_MODEL=llama-3.3-70b-versatile
GROQ_RESPONSE_FORMAT=json_schema
LLM_MAX_PRIMARY_ATTEMPTS=2
LLM_FALLBACK_ENABLED=false
```

Groq API keys are not stored in the repository. Provide credentials through environment variables or your deployment secret manager at runtime.

See also: [Groq LLM Provider](architecture/groq-llm-provider.md), [Real LLM Provider Adapter Boundary](architecture/real-llm-provider-adapter.md), [LLM Reliability Orchestration](architecture/llm-reliability-orchestration.md).

## Receptionist Response Generator

Response phrasing is **deterministic by default** for local development and CI.

This boundary is separate from LLM shadow analysis and slot filling. It controls how backend-planned replies are rendered for chat and optional voice `suggested_response_text`.

| Variable | Default | Description |
|----------|---------|-------------|
| `RECEPTIONIST_RESPONSE_MODE` | `deterministic` | Response rendering mode: `deterministic` or `llm` |
| `RECEPTIONIST_RESPONSE_LLM_PROVIDER` | empty | Optional dedicated provider for response phrasing. When unset, uses the primary LLM provider |
| `RECEPTIONIST_RESPONSE_MAX_TOKENS` | `400` | Max output tokens for LLM phrasing (`>= 1`) |
| `RECEPTIONIST_RESPONSE_TEMPERATURE` | `0` | LLM temperature for response phrasing (`0`–`2`) |
| `RECEPTIONIST_RESPONSE_VALIDATE_OUTPUT` | `true` | Enable post-generation output validation |

For local development and CI, keep:

```env
RECEPTIONIST_RESPONSE_MODE=deterministic
RECEPTIONIST_RESPONSE_VALIDATE_OUTPUT=true
```

Optional hosted public demo phrasing example:

```env
RECEPTIONIST_RESPONSE_MODE=llm
RECEPTIONIST_RESPONSE_LLM_PROVIDER=groq
RECEPTIONIST_RESPONSE_MAX_TOKENS=400
RECEPTIONIST_RESPONSE_TEMPERATURE=0
RECEPTIONIST_RESPONSE_VALIDATE_OUTPUT=true
```

Critical flows such as emergency guidance, human escalation, and booking confirmation remain controlled even when `RECEPTIONIST_RESPONSE_MODE=llm`.

See also: [Receptionist Response Generator](architecture/receptionist-response-generator.md).

## Retell

Retell is **disabled by default** for local development and CI.

| Variable | Default | Description |
|----------|---------|-------------|
| `RETELL_ENABLED` | `false` | Enable Retell provider callback/tool routes |
| `RETELL_API_KEY` | empty | Retell API key |
| `RETELL_WEBHOOK_VERIFICATION_ENABLED` | `true` | Verify `x-retell-signature` on protected Retell routes |
| `RETELL_WEBHOOK_SECRET` | empty | Webhook signing secret. Required when `RETELL_ENABLED=true` and `RETELL_WEBHOOK_VERIFICATION_ENABLED=true` |
| `RETELL_ALLOW_INSECURE_WEBHOOKS` | `false` | Skip signature verification. Allowed only when `APP_ENV` is `local`, `test`, or `development` |
| `RETELL_SIGNATURE_HEADER_NAME` | `x-retell-signature` | Request header carrying the Retell webhook signature |
| `RETELL_REQUEST_MAX_BODY_BYTES` | `262144` | Maximum raw request body size for Retell callbacks (`>= 1`) |

Protected Retell routes include tool callbacks under `/api/v1/retell/tools/*` and lifecycle ingestion at `/api/v1/retell/webhooks/lifecycle`.

For local development and CI, keep:

```env
RETELL_ENABLED=false
RETELL_WEBHOOK_VERIFICATION_ENABLED=true
RETELL_ALLOW_INSECURE_WEBHOOKS=false
```

Optional hosted public demo configuration:

```env
RETELL_ENABLED=true
RETELL_WEBHOOK_VERIFICATION_ENABLED=true
RETELL_WEBHOOK_SECRET=whsec_...
RETELL_ALLOW_INSECURE_WEBHOOKS=false
```

See also: [Retell Webhook Security](architecture/retell-webhook-security.md), [Retell Call Lifecycle](architecture/retell-call-lifecycle.md).

## Public Demo Guardrails

Guardrails are **disabled by default** for local development and CI.

| Variable | Default | Description |
|----------|---------|-------------|
| `PUBLIC_DEMO_MODE` | `false` | Marks the deployment as a public unauthenticated demo |
| `PUBLIC_DEMO_GUARDRAILS_ENABLED` | `false` | Enables Redis-backed demo rate limiting and quotas |
| `TRUST_PROXY_HEADERS` | `false` | When `true`, use the first IP from `X-Forwarded-For` for per-IP limits |

Per-IP limits (enforced when `PUBLIC_DEMO_GUARDRAILS_ENABLED=true`):

| Variable | Default | Description |
|----------|---------|-------------|
| `DEMO_CHAT_MESSAGES_PER_MINUTE_PER_IP` | `10` | Chat messages allowed per IP per minute |
| `DEMO_CHAT_MESSAGES_PER_DAY_PER_IP` | `100` | Chat messages allowed per IP per UTC day |
| `DEMO_RETELL_TOOL_CALLS_PER_MINUTE_PER_IP` | `30` | Retell tool calls allowed per IP per minute |
| `DEMO_RETELL_TOOL_CALLS_PER_DAY_PER_IP` | `300` | Retell tool calls allowed per IP per UTC day |
| `DEMO_APPOINTMENTS_PER_DAY_PER_IP` | `5` | Appointments bookable per IP per UTC day |
| `DEMO_CONFIRMATION_EMAILS_PER_DAY_PER_IP` | `5` | Confirmation emails queueable per IP per UTC day |

Global daily limits (enforced when `PUBLIC_DEMO_GUARDRAILS_ENABLED=true`):

| Variable | Default | Description |
|----------|---------|-------------|
| `DEMO_GLOBAL_CHAT_MESSAGES_PER_DAY` | `2000` | Total chat messages allowed per UTC day |
| `DEMO_GLOBAL_APPOINTMENTS_PER_DAY` | `200` | Total appointments bookable per UTC day |
| `DEMO_GLOBAL_CONFIRMATION_EMAILS_PER_DAY` | `200` | Total confirmation emails queueable per UTC day |

All numeric limits must be `>= 1`.

Hosted public demo example:

```env
PUBLIC_DEMO_MODE=true
PUBLIC_DEMO_GUARDRAILS_ENABLED=true
TRUST_PROXY_HEADERS=true
```

Enabling `PUBLIC_DEMO_MODE=true` without `PUBLIC_DEMO_GUARDRAILS_ENABLED=true` is allowed but unsafe for production because the demo is exposed without rate limits.

See also: [Public Demo Guardrails](architecture/public-demo-guardrails.md).

## Deployment Environment Templates

| File | Purpose |
|------|---------|
| `.env.example` | Safe local development and CI defaults with fake providers |
| `.env.demo.example` | Hosted public demo deployment template with grouped settings |

Copy `.env.example` to `.env` for local work. Use `.env.demo.example` as a checklist when configuring a production-like public demo in your secret manager or hosting platform. Never commit real API keys or webhook secrets.

For deploy steps, health checks, and troubleshooting, see [Public Demo Deployment Runbook](operations/public-demo-deployment.md).
