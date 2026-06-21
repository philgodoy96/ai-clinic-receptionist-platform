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
