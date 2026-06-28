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

## Scheduling Availability Policy

Availability lookup applies clinic-local scheduling policy before returning candidate slots. These settings control how far ahead callers may book and how soon the next slot may be offered.

| Variable | Default | Description |
|----------|---------|-------------|
| `SCHEDULING_MIN_BOOKING_LEAD_MINUTES` | `60` | Minimum minutes from clinic-local current time before a slot may appear in availability. Prevents offering times that are too close to "now" for realistic booking. |
| `SCHEDULING_BOOKING_HORIZON_DAYS` | `14` | Maximum number of days from clinic-local current time that availability lookup will return. **Single source of truth** for both availability visibility and demo availability generation (`python -m app.scripts.generate_demo_availability`). |

For local development and CI, the defaults are usually sufficient:

```env
SCHEDULING_MIN_BOOKING_LEAD_MINUTES=60
SCHEDULING_BOOKING_HORIZON_DAYS=14
```

Policy is enforced in `SchedulingService.check_availability` using `ClinicTimeService` for timezone-aware boundaries. Availability is an advisory read model; holds and booking apply separate consistency checks.

Demo operators refresh future slots through the booking horizon with `python -m app.scripts.generate_demo_availability`. See [Demo Availability Generation](operations/demo-availability-generation.md).

See also: [Scheduling Application Services](architecture/scheduling-services.md).

## Database and Infrastructure

| Variable | Default | Description |
|----------|---------|-------------|
| `DATABASE_URL` | local PostgreSQL URL | SQLAlchemy database URL |
| `REDIS_URL` | `redis://localhost:6379/0` | Redis connection URL |
| `RABBITMQ_URL` | local RabbitMQ URL | RabbitMQ connection URL |
| `APPOINTMENT_HOLD_TTL_SECONDS` | `300` | Redis hold TTL in seconds for Retell voice and default holds |
| `CHAT_APPOINTMENT_HOLD_TTL_SECONDS` | `600` | Redis hold TTL in seconds for written-chat holds (longer so users can pause while typing) |

For local development and CI:

```env
APPOINTMENT_HOLD_TTL_SECONDS=300
CHAT_APPOINTMENT_HOLD_TTL_SECONDS=600
```

Written chat booking and reschedule holds use `CHAT_APPOINTMENT_HOLD_TTL_SECONDS`. Retell voice tools use `APPOINTMENT_HOLD_TTL_SECONDS`. Hold TTL is backend-owned; provider tool arguments cannot extend it.

See [Appointment Slot Holds](architecture/appointment-holds.md) and [Chat Appointment Management](architecture/chat-appointment-management.md).

## Email Jobs

Postgres `EmailJob` records are the durable source of truth for delivery state. RabbitMQ carries wake-up messages only (`email_job_id`). Retry scheduling uses `next_attempt_at`; RabbitMQ TTL retry queues are not used for provider failures.

Booking, reschedule, and escalation flows **create durable `EmailJob` records only**. Chat, scheduling API, and Retell routes do not call Resend (or any email provider) directly. A background worker sends through the configured `EmailDeliveryProvider`.

Email delivery is **at-least-once**. Exactly-once delivery across Postgres and external providers is not guaranteed. When `EMAIL_PROVIDER=resend`, the worker passes `EmailJob.idempotency_key` (or an `email_job:{id}` fallback) to Resend as an `Idempotency-Key` header.

### Provider modes

| Mode | Setting | Use case |
|------|---------|----------|
| **Fake** (default) | `EMAIL_PROVIDER=fake` | Local development, CI, and smoke tests. No API keys required. Outbound messages are recorded in memory by `FakeEmailProvider`. |
| **Resend** (opt-in) | `EMAIL_PROVIDER=resend` | Real outbound email through [Resend](https://resend.com). Requires `RESEND_API_KEY` and `EMAIL_FROM_ADDRESS`. Enable only in deployment secret managers — never commit real keys. |

Resend is intentionally opt-in. Startup validation rejects `EMAIL_PROVIDER=resend` without `RESEND_API_KEY` and `EMAIL_FROM_ADDRESS`.

### Environment variables

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
| `RABBITMQ_URL` | local RabbitMQ URL | Required when `EMAIL_JOB_DISPATCH_ENABLED=true` |
| `HUMAN_ESCALATION_NOTIFICATION_EMAIL` | demo staff email | Default staff notification recipient |

### Runtime requirements

Real appointment confirmation delivery requires all of the following:

1. `EMAIL_PROVIDER=resend`
2. Valid `RESEND_API_KEY` and `EMAIL_FROM_ADDRESS` (from a verified sending domain or subdomain in Resend)
3. A worker process running (`python -m scripts.run_email_job_worker` for polling, or `python -m scripts.run_email_worker` / `python -m scripts.run_email_job_consumer` when RabbitMQ dispatch is enabled)
4. When `EMAIL_JOB_DISPATCH_ENABLED=true`: RabbitMQ reachable from API and worker, with dispatch enabled on the **API** so booking flows publish wake-up messages

With `EMAIL_JOB_DISPATCH_ENABLED=false`, the polling worker can process due jobs without RabbitMQ wake-ups.

Confirming a booking or reschedule creates a pending `EmailJob` and returns as soon as the appointment is committed. **Email is not sent until the worker processes the job.** Sending is asynchronous and best-effort.

### Real Resend configuration

Real outbound delivery is opt-in. Set these values locally or in a deployment secret manager — never commit real API keys or `.env`:

```env
EMAIL_PROVIDER=resend
RESEND_API_KEY=<set locally, never commit>
EMAIL_FROM_ADDRESS="AI Clinic Demo <appointments@email.example.com>"
EMAIL_REPLY_TO=
EMAIL_JOB_DISPATCH_ENABLED=false
```

Notes:

- The project uses `EMAIL_FROM_ADDRESS` as the Resend `from` address. There is no separate `RESEND_FROM_EMAIL` setting.
- The domain or subdomain in `EMAIL_FROM_ADDRESS` must be **verified in Resend** before sends succeed.
- For local manual smoke with dispatch disabled, run `python -m scripts.run_email_job_worker --once` after confirming a booking or reschedule.
- With `EMAIL_JOB_DISPATCH_ENABLED=true`, the API publishes wake-up messages and a RabbitMQ consumer (`python -m scripts.run_email_worker` or `python -m scripts.run_email_job_consumer`) processes jobs from the queue.

### Resend sending domain and DNS

Use a **verified domain or subdomain** for transactional mail. A dedicated subdomain is recommended for demo and portfolio deployments (for example `email.example.com`) so sending reputation stays isolated from your primary site domain.

Resend requires DNS verification records on the sending domain, typically including:

- **DKIM** — cryptographic signing for recipient providers
- **SPF** — authorized sending hosts for the domain
- **Return-Path** — bounce handling for the subdomain

**DMARC** is recommended on the sending subdomain. For early demo or portfolio phases, a permissive policy is enough to start monitoring without blocking legitimate test mail:

```txt
v=DMARC1; p=none; pct=100
```

Even with SPF, DKIM, and DMARC passing, **new domains and subdomains may still land in spam or junk** until sender reputation builds. Production deployments should use verified sending domains and operational monitoring (Resend dashboard, job failure metrics). Public demos should keep `EMAIL_PROVIDER=fake` unless auth, rate limits, and cost controls are in place.

### Appointment confirmation behavior

| Trigger | Job type | Idempotency key |
|---------|----------|-----------------|
| Written-chat booking confirmation | `appointment_confirmation` | `appointment_confirmation:{appointment_id}` |
| Scheduling API / Retell voice booking | `appointment_confirmation` | same |
| Reschedule confirmation (new active appointment) | `appointment_confirmation` | same (keyed to the **new** appointment id) |

Jobs store `recipient_email`, `patient_name`, `doctor_name`, `specialty_name`, and `appointment_start_time` when available. The worker renders plain-text subject/body at send time using **clinic-local** date and time (`CLINIC_TIMEZONE`). Templates avoid raw UTC timestamps. Reschedule confirmations use a distinct subject line when `confirmation_reason=reschedule` is present in the job payload.

Appointment email content is transactional and structured. Booking confirmation and reschedule emails include appointment details when available: specialty, clinician, clinic-local date, and clinic-local time. Branded HTML templates are future work; mail is plain text today.

Email delivery does not roll back confirmed appointments. A failed or missing email leaves the appointment in place.

### Idempotency

Appointment confirmation jobs use:

```
appointment_confirmation:{appointment_id}
```

A partial unique index on `email_jobs.idempotency_key` prevents duplicate confirmation jobs for the same appointment. Retries and worker crash recovery reuse the same job record; they do not create a second job for the same key. When `EMAIL_PROVIDER=resend`, the same key is passed to Resend as the `Idempotency-Key` header where supported.

### Failure behavior

| Condition | Behavior |
|-----------|----------|
| Missing `recipient_email` | Worker records `recipient_email_missing` on the job, retries until `EMAIL_JOB_MAX_ATTEMPTS`, then `failed` |
| Provider send failure | Retries with exponential backoff via `next_attempt_at`; terminal `failed` after max attempts |
| Successful send | Job `sent`, `provider_message_id` stored when the provider returns one |
| Appointment already confirmed | Unaffected — email failure does not undo booking or reschedule |

For local development and CI, keep:

```env
EMAIL_PROVIDER=fake
EMAIL_JOB_DISPATCH_ENABLED=false
```

### Current limitations

- **Cancellation emails** are not implemented; only booking and reschedule confirmations enqueue jobs today.
- **Plain text only** — no rich branded HTML templates yet.
- **Sender reputation** — verified DNS does not guarantee inbox placement; new subdomains may land in spam/junk.
- **Public demos** — keep `EMAIL_PROVIDER=fake` by default unless `PUBLIC_DEMO_GUARDRAILS_ENABLED` and cost controls are in place.

See also:

- [Email Job Worker](architecture/email-job-worker.md)
- [Email Confirmation Jobs](architecture/email-confirmation-jobs.md)
- [Email Dispatch Reliability](architecture/email-dispatch-reliability.md)
- [Local Development](operations/local-development.md) — manual smoke checklists

## LLM Provider

Supported providers in this version:

- `fake` — local development, CI, and deterministic tests (default)
- `groq` — real-provider validation path for hosted public demo
- `bedrock` — optional AWS enterprise-style adapter

OpenAI is intentionally not implemented in this version. There is no `OPENAI_API_KEY` setting or OpenAI provider adapter.

Provider selection:

- `LLM_PRIMARY_PROVIDER=fake|groq|bedrock` (preferred)
- `LLM_PROVIDER=fake|groq|bedrock` (backward compatible; used when `LLM_PRIMARY_PROVIDER` is unset)
- `LLM_FALLBACK_PROVIDER` — leave unset when `LLM_FALLBACK_ENABLED=false`; when fallback is enabled, set to `fake`, `groq`, or `bedrock`

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

For Bedrock, configure `BEDROCK_MODEL_ID` and `AWS_REGION`. AWS access uses the standard boto3 credential chain (for example `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY`, shared credentials file, or an IAM instance/task role). No additional Bedrock-specific credential variables are required beyond the model and region settings above.

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
VOICE_PATIENT_INTAKE_MODE=demo_auto_create
```

Enabling `PUBLIC_DEMO_MODE=true` without `PUBLIC_DEMO_GUARDRAILS_ENABLED=true` is allowed but unsafe for production because the demo is exposed without rate limits.

See also: [Public Demo Guardrails](architecture/public-demo-guardrails.md).

## Voice patient intake

Controls whether voice booking may create minimal demo patients when identity is not found.

| Variable | Default | Description |
|----------|---------|-------------|
| `VOICE_PATIENT_INTAKE_MODE` | `lookup_only` | `lookup_only` requires a pre-existing patient record. `demo_auto_create` creates a minimal patient for voice booking when identity is new and the email passes schema validation. |

Recommended:

- **Production-like / safe default:** `lookup_only`
- **Public demo / local voice testing:** `demo_auto_create` (see `.env.demo.example`)

Voice intake does not change chat booking or `AppointmentBookingService` rules. Explicit confirmation, hold validation, and booking idempotency remain unchanged.

Demo auto-create stores `patient_phone` only when the caller provided it; omitted phone is stored as `NULL` (no synthetic contact data).

Future: fuzzy identity resolution — see [Voice Patient Identity Resolution](operations/voice-patient-identity-resolution.md).

## Chat turn understanding

Controls whether chat uses the structured turn understanding interpreter for:

- **Appointment intake** — specialty, doctor, date, time, soonest intent, and offered-doctor selection before availability routing
- **Booking identity intake** — patient status and identity fields during hold confirmation
- **Appointment management** — optional identity extraction during lookup, cancel, and reschedule `patient_identity` steps

| Variable | Default | Description |
|----------|---------|-------------|
| `CHAT_TURN_UNDERSTANDING_INTERPRETER` | `disabled` | Interpreter selection: `disabled`, `fake`, or `groq` |

Supported values:

- `disabled` — default; preserves deterministic fallback parsing for appointment and identity intake
- `fake` — deterministic local/demo interpreter with no provider calls; recommended for reproducible manual testing
- `groq` — real provider-backed interpreter for controlled local testing with a Groq API key

When `CHAT_TURN_UNDERSTANDING_INTERPRETER=groq`, these settings are required independently of `LLM_PRIMARY_PROVIDER` / `LLM_PROVIDER`:

- `GROQ_API_KEY`
- `GROQ_MODEL`

Optional Groq settings reused by chat turn understanding:

- `GROQ_BASE_URL`
- `GROQ_REQUEST_TIMEOUT_SECONDS`
- `GROQ_MAX_OUTPUT_TOKENS`
- `GROQ_TEMPERATURE`
- `GROQ_RESPONSE_FORMAT` — use `json_schema` for structured `ChatTurnUnderstandingResult` output

Recommended:

- **Production-like / safe default:** `disabled`
- **Local demo / reproducible appointment and identity testing:** `fake`
- **Controlled local Groq testing:** `groq` with credentials configured locally

```env
CHAT_TURN_UNDERSTANDING_INTERPRETER=disabled
# CHAT_TURN_UNDERSTANDING_INTERPRETER=fake
# CHAT_TURN_UNDERSTANDING_INTERPRETER=groq
# GROQ_API_KEY=gsk_...
# GROQ_MODEL=llama-3.3-70b-versatile
# GROQ_RESPONSE_FORMAT=json_schema
```

Do not expose public Groq-backed chat turn understanding without authentication, rate limits, and cost controls.

This setting does not change public Chat API schemas, hold creation rules, booking confirmation, or Retell behavior. Appointment management flows (lookup, cancel, reschedule) use the same orchestration and domain services regardless of interpreter mode.

See [Chat Turn Understanding Architecture](architecture/chat-turn-understanding.md), [Chat Appointment Management](architecture/chat-appointment-management.md), and [Chat Appointment Intake Manual Testing](testing/chat-appointment-intake.md).

## Deployment Environment Templates

| File | Purpose |
|------|---------|
| `.env.example` | Safe local development and CI defaults with fake providers |
| `.env.demo.example` | Hosted public demo deployment template with grouped settings |

Copy `.env.example` to `.env` for local work. Use `.env.demo.example` as a checklist when configuring a production-like public demo in your secret manager or hosting platform. Never commit real API keys or webhook secrets.

For deploy steps, health checks, and troubleshooting, see [Public Demo Deployment Runbook](operations/public-demo-deployment.md).
