# Configuration

Application settings are loaded from environment variables and an optional local `.env` file through Pydantic settings.

See `.env.example` for a safe local template.

## Application

| Variable | Default | Description |
|----------|---------|-------------|
| `APP_NAME` | `ai-clinic-receptionist-platform` | Application name |
| `APP_ENV` | `local` | Environment label |
| `APP_DEBUG` | `true` | FastAPI debug mode |
| `API_V1_PREFIX` | `/api/v1` | API route prefix |

## Database and Infrastructure

| Variable | Default | Description |
|----------|---------|-------------|
| `DATABASE_URL` | local PostgreSQL URL | SQLAlchemy database URL |
| `REDIS_URL` | `redis://localhost:6379/0` | Redis connection URL |
| `RABBITMQ_URL` | local RabbitMQ URL | RabbitMQ connection URL |
| `APPOINTMENT_HOLD_TTL_SECONDS` | `300` | Redis hold TTL in seconds |

## Email Jobs

| Variable | Default | Description |
|----------|---------|-------------|
| `EMAIL_PROVIDER` | `fake` | Email provider selection |
| `EMAIL_JOB_DISPATCH_ENABLED` | `false` | Enable RabbitMQ dispatch after durable email jobs are created |
| `EMAIL_JOB_QUEUE_NAME` | `email_jobs` | RabbitMQ queue name for email jobs |
| `RESEND_API_KEY` | empty | Resend API key when using a real email provider |
| `HUMAN_ESCALATION_NOTIFICATION_EMAIL` | demo staff email | Default staff notification recipient |

## LLM Provider

| Variable | Default | Description |
|----------|---------|-------------|
| `LLM_PROVIDER` | `fake` | LLM provider selection: `fake` or `bedrock` |
| `LLM_ENABLED` | `true` | Enable LLM shadow analysis and slot-filling assistance |
| `BEDROCK_MODEL_ID` | empty | Required when `LLM_PROVIDER=bedrock` |
| `AWS_REGION` | `us-east-1` | AWS region for Bedrock runtime client |
| `BEDROCK_REQUEST_TIMEOUT_SECONDS` | `10` | Bedrock request timeout |
| `BEDROCK_MAX_RETRIES` | `0` | Bedrock client retry count |
| `BEDROCK_TEMPERATURE` | `0` | Default Bedrock inference temperature |
| `BEDROCK_MAX_TOKENS` | `800` | Default Bedrock max output tokens |

AWS credentials are not stored in the repository. When using Bedrock, provide credentials through standard AWS environment variables, shared config/profile, or an IAM role at runtime.

For local development and CI, keep:

```env
LLM_PROVIDER=fake
```

See also: [Real LLM Provider Adapter Boundary](architecture/real-llm-provider-adapter.md).

## Retell

| Variable | Default | Description |
|----------|---------|-------------|
| `RETELL_API_KEY` | empty | Retell API key |
| `RETELL_WEBHOOK_SECRET` | empty | Retell webhook secret |

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
