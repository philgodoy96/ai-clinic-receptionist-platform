# Public Demo Deployment Runbook

This runbook describes how to deploy the hosted **public unauthenticated demo** for the fictional clinic receptionist platform. It is intended for portfolio-style deployments on a managed platform (Railway, Render, Fly.io, ECS, etc.), not for a full production clinic system.

Use `.env.demo.example` as the environment checklist and [Configuration](../configuration.md) for variable reference.

## Deployment Architecture

```mermaid
flowchart TB
    subgraph clients [Clients]
        Browser[Browser / Next.js web demo]
        Retell[Retell voice platform]
    end

    subgraph platform [Your hosting platform]
        Web[Web service<br/>Next.js]
        API[API service<br/>uvicorn]
        Worker[Email worker<br/>run_email_worker]
    end

    subgraph data [Managed data services]
        PG[(PostgreSQL)]
        Redis[(Redis)]
        RMQ[(RabbitMQ)]
    end

    subgraph optional [Optional]
        OTEL[OTEL collector]
    end

    subgraph external [External providers]
        Groq[Groq LLM]
        Resend[Resend email]
        RetellAPI[Retell API / webhooks]
    end

    Browser --> Web
    Web -->|"/api/v1/* rewrite or edge proxy"| API
    Retell --> API
    API --> PG
    API --> Redis
    API --> RMQ
    Worker --> PG
    Worker --> RMQ
    Worker --> Resend
    API --> Groq
    API -.-> OTEL
    Worker -.-> OTEL
    API --> RetellAPI
```

| Component | Role | Required for public demo |
|-----------|------|------------------------|
| **Web service** | Next.js public demo UI: landing, chat panel, voice entry | Recommended for portfolio demo |
| **API service** | FastAPI app: chat, scheduling, Retell tools, health | Yes |
| **Email worker** | RabbitMQ consumer that processes durable `EmailJob` records | Yes when `EMAIL_JOB_DISPATCH_ENABLED=true` |
| **PostgreSQL** | Durable conversations, appointments, email jobs, audit logs | Yes |
| **Redis** | Appointment holds, demo guardrails, rate-limit counters | Yes when guardrails enabled |
| **RabbitMQ** | Wake-up queue for email job processing | Yes when dispatch enabled |
| **OTEL collector** | Optional traces/metrics export | No (not wired in env template yet) |
| **Groq** | Optional real LLM analysis / phrasing | No (fake LLM works for smoke tests) |
| **Resend** | Optional real confirmation email delivery | No (fake email works for smoke tests) |
| **Retell** | Optional voice tool + lifecycle webhooks + server-side web calls | No (chat-only demo is valid); see [Retell Dashboard Setup](retell-dashboard-setup.md) |

Deploy **one API container** and **one or more worker containers** from the **same Docker image** with different commands. Optionally deploy the **`web/`** Next.js app as a separate service for the browser UI. Infrastructure (Postgres, Redis, RabbitMQ) is usually managed services, not sidecars in the app image.

## Frontend (Web) Deployment

The public demo UI lives in [`web/`](../../web/). See [`web/README.md`](../../web/README.md) for local setup.

### Build and start

```bash
cd web
npm ci
npm run build
npm run start
```

Set the platform `PORT` if required. On Vercel, Railway, Render, etc., use the provider's Next.js preset or `npm run build` / `npm run start`.

### Required frontend environment variables

Only **`NEXT_PUBLIC_*`** vars belong in the web deployment. Do not put backend secrets in the frontend service.

| Variable | Required | Notes |
|----------|----------|-------|
| `NEXT_PUBLIC_API_BASE_URL` | Recommended | Public API origin (no trailing slash); used for display and as rewrite fallback |
| `API_PROXY_TARGET` | Recommended when API is on another host | Server-only; Next.js rewrites `/api/v1/*` to this origin |
| `NEXT_PUBLIC_GITHUB_URL` | Optional | Footer / landing link |
| `NEXT_PUBLIC_ARCHITECTURE_DOC_URL` | Optional | Footer / landing link |
| `NEXT_PUBLIC_VOICE_DEMO_ENABLED` | Optional | `false` = voice configuration preview; `true` = live Retell Web SDK voice demo (requires backend `RETELL_WEB_CALL_ENABLED`) |

Example (split deployment):

```env
NEXT_PUBLIC_API_BASE_URL=https://api.example.com
API_PROXY_TARGET=https://api.example.com
NEXT_PUBLIC_VOICE_DEMO_ENABLED=false
```

### CORS and origin reminder

The web chat client calls **`/api/v1/chat/messages` on the frontend origin**. Next.js rewrites (via `API_PROXY_TARGET`) proxy to the API server-side, so **browser CORS is not required** for the default setup.

The FastAPI app does **not** include CORS middleware today. If you bypass rewrites and point the browser directly at a cross-origin API URL, you must add CORS on the API or terminate both UI and `/api/v1` behind the same public domain.

### Frontend smoke checks

- [ ] Landing page loads over HTTPS.
- [ ] **Talk to the receptionist** opens chat; a greeting returns an assistant reply.
- [ ] Demo disclaimer is visible (fictional clinic; no real patient data).
- [ ] With `NEXT_PUBLIC_VOICE_DEMO_ENABLED=false`, **Call the clinic** shows the configuration preview and does not request a microphone.
- [ ] With `NEXT_PUBLIC_VOICE_DEMO_ENABLED=true` and backend `RETELL_WEB_CALL_ENABLED=true`, **Call the clinic** starts a web call via the public demo API and Retell Web SDK (see [Retell Dashboard Setup](retell-dashboard-setup.md)).
- [ ] `npm run check:env-safety` passes in CI or before release (no forbidden `NEXT_PUBLIC_*` secret names).

## Enabling Voice Demo

Voice requires **both** backend web-call configuration and the frontend feature flag. Retell agent, tool, and webhook setup is documented in [Retell Dashboard Setup](retell-dashboard-setup.md) — not duplicated here.

### Backend environment variables (web calls)

| Variable | Required when | Notes |
|----------|---------------|-------|
| `RETELL_WEB_CALL_ENABLED` | Browser web calls | `true` to allow `POST /api/v1/demo/voice/retell-web-call` |
| `RETELL_API_KEY` | `RETELL_WEB_CALL_ENABLED=true` | Server-side only; never expose to the browser |
| `RETELL_AGENT_ID` | `RETELL_WEB_CALL_ENABLED=true` | Must match the Retell dashboard agent |
| `RETELL_AGENT_VERSION` | Optional | Pin agent version when set |
| `RETELL_WEB_CALL_TIMEOUT_SECONDS` | Optional | Provider HTTP timeout |

For tool callbacks and lifecycle webhooks during voice calls, also set `RETELL_ENABLED=true` with webhook verification (`RETELL_WEBHOOK_SECRET` when `RETELL_WEBHOOK_VERIFICATION_ENABLED=true`).

### Frontend environment variable

| Variable | Notes |
|----------|-------|
| `NEXT_PUBLIC_VOICE_DEMO_ENABLED` | `false` (default): configuration preview, no microphone. `true`: live voice UI; requires backend `RETELL_WEB_CALL_ENABLED=true`. |

### Recommended deployment order

1. Deploy API with chat and guardrails working (`PUBLIC_DEMO_MODE`, Redis, migrations).
2. Configure Retell agent, tools, and webhooks per [Retell Dashboard Setup](retell-dashboard-setup.md).
3. Set backend `RETELL_ENABLED=true`, `RETELL_WEB_CALL_ENABLED=true`, `RETELL_API_KEY`, and `RETELL_AGENT_ID`; redeploy API.
4. Smoke-test `POST /api/v1/demo/voice/retell-web-call` — response should include `call_id` and `access_token` only (no API keys).
5. Set `NEXT_PUBLIC_VOICE_DEMO_ENABLED=true` on the web service; redeploy `web/`.
6. Verify end-to-end browser voice (microphone only after **Start call**, agent tools reach the API).

Chat-only demos can skip steps 2–6 with `RETELL_ENABLED=false`, `RETELL_WEB_CALL_ENABLED=false`, and `NEXT_PUBLIC_VOICE_DEMO_ENABLED=false`.

## Required Environment Variables

Copy values from `.env.demo.example` into your platform secret manager. At minimum for a production-like public demo:

### Always required (public demo)

| Variable | Notes |
|----------|-------|
| `APP_ENV` | `production` (or your platform label; must not be `local`/`test`/`development` for production rules) |
| `APP_DEBUG` | `false` |
| `PUBLIC_DEMO_MODE` | `true` |
| `PUBLIC_DEMO_GUARDRAILS_ENABLED` | `true` (auto-defaults when unset and `PUBLIC_DEMO_MODE=true`) |
| `DATABASE_URL` | Managed PostgreSQL URL (**non-localhost**) |
| `REDIS_URL` | Managed Redis URL (**non-localhost**) |
| `TRUST_PROXY_HEADERS` | `true` when behind a reverse proxy / load balancer |

### Required when email dispatch is enabled

| Variable | Notes |
|----------|-------|
| `EMAIL_JOB_DISPATCH_ENABLED` | `true` for hosted demo with real email jobs |
| `RABBITMQ_URL` | Managed RabbitMQ URL |
| `EMAIL_JOB_QUEUE_NAME` | Default `email_jobs` |

### Required only when a provider is enabled

| Provider | Enable with | Also required |
|----------|-------------|---------------|
| **Groq** | `LLM_PRIMARY_PROVIDER=groq` or `LLM_PROVIDER=groq` | `GROQ_API_KEY`, `GROQ_MODEL` |
| **Resend** | `EMAIL_PROVIDER=resend` | `RESEND_API_KEY`, `EMAIL_FROM_ADDRESS` (verified domain or subdomain in Resend) |
| **Retell** | `RETELL_ENABLED=true` | `RETELL_WEBHOOK_SECRET` when `RETELL_WEBHOOK_VERIFICATION_ENABLED=true`; `RETELL_API_KEY` for web calls |
| **Retell web calls** | `RETELL_WEB_CALL_ENABLED=true` | `RETELL_API_KEY`, `RETELL_AGENT_ID`; optional `RETELL_AGENT_VERSION`, `RETELL_WEB_CALL_TIMEOUT_SECONDS` |
| **Bedrock** | `LLM_PRIMARY_PROVIDER=bedrock` | `BEDROCK_MODEL_ID`, runtime AWS credentials |

Resend is **opt-in**. Keep `EMAIL_PROVIDER=fake` for smoke tests without outbound mail or committed secrets. Booking and reschedule only create `EmailJob` rows; the worker sends asynchronously. Email is not delivered until the worker processes each job.

Example Resend configuration (secrets in the platform only):

```env
EMAIL_PROVIDER=resend
RESEND_API_KEY=<set in secret manager, never commit>
EMAIL_FROM_ADDRESS="AI Clinic Demo <appointments@email.example.com>"
EMAIL_REPLY_TO=
EMAIL_JOB_DISPATCH_ENABLED=true
```

### Resend sending domain

Before enabling real mail on a public demo:

1. Add and verify a sending subdomain in Resend (recommended: `email.example.com` rather than your apex domain).
2. Publish Resend's DNS records (DKIM, SPF, Return-Path) on that subdomain.
3. Add a DMARC record when ready; for early demo phases, `v=DMARC1; p=none; pct=100` is sufficient to start monitoring.
4. Set `EMAIL_FROM_ADDRESS` to an address on the verified subdomain.
5. Enable `PUBLIC_DEMO_GUARDRAILS_ENABLED` before `EMAIL_PROVIDER=resend` on an internet-facing deployment.

New subdomains may still land in spam/junk until sender reputation improves, even when DNS checks pass. See [Configuration](../configuration.md#resend-sending-domain-and-dns).

Safe smoke-test defaults (no external provider keys):

```env
LLM_PROVIDER=fake
EMAIL_PROVIDER=fake
RETELL_ENABLED=false
```

Startup validation rejects incomplete provider configuration and unsafe production settings (for example `RETELL_ALLOW_INSECURE_WEBHOOKS=true` in production-like `APP_ENV`).

## Pre-deploy: Database Migrations

Run once per release **before** or **during** deploy, with network access to Postgres and the application code (including the `migrations/` directory):

```bash
python -m alembic upgrade head
```

Recommended pattern:

1. Run migrations from a one-off release job or CI step.
2. Then roll out API and worker containers.

Optional seed for demo scheduling data:

```bash
python -m scripts.seed_demo_data
python -m app.scripts.generate_demo_availability
```

Seed creates the fictional clinic roster and a short rolling slot window. The generator extends availability through `SCHEDULING_BOOKING_HORIZON_DAYS` (default 14) and is idempotent. See [Demo Availability Generation](demo-availability-generation.md).

Do not run migrations automatically on API startup.

## API Command

Production command (also the Docker default):

```bash
python -m uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}
```

Docker example (same image, default CMD):

```yaml
command: sh -c "python -m uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"
```

Do **not** use `--reload` in hosted environments.

## Worker Command

Production email worker (RabbitMQ consumer):

```bash
python -m scripts.run_email_worker
```

Docker example (same image, different command):

```yaml
command: python -m scripts.run_email_worker
```

Requirements:

- `EMAIL_JOB_DISPATCH_ENABLED=true` on the **API** so booking flows publish wake-up messages.
- Worker reachable to Postgres, RabbitMQ, and the configured email provider.
- At least one worker replica; scale horizontally for throughput.

Polling fallback (local or emergency only):

```bash
python -m scripts.run_email_job_worker
```

## Health and Readiness Checks

| Endpoint | Purpose | Expected |
|----------|---------|----------|
| `GET /health` | Liveness | `200`, `"status": "ok"` |
| `GET /health/dependencies` | Readiness (database) | `200`, `"status": "ok"` when Postgres is reachable; `"status": "degraded"` when database check fails |

Notes:

- Dependency health currently checks **PostgreSQL only**. Redis and RabbitMQ are not probed on this endpoint yet.
- When `PUBLIC_DEMO_GUARDRAILS_ENABLED=true`, protected routes **fail closed** if Redis is unavailable (`503`, `demo_guardrail_store_unavailable`).
- Configure platform health checks against `/health` for liveness and `/health/dependencies` for readiness before receiving traffic.

## Smoke Test Checklist

After deploy, verify:

- [ ] `GET /health` returns `200` with `"status": "ok"`.
- [ ] `GET /health/dependencies` returns `200` with `"dependencies.database.status": "ok"`.
- [ ] Migrations applied: `python -m alembic current` shows expected head revision.
- [ ] (Optional) Demo data seeded if you rely on pre-created availability slots.
- [ ] `POST /api/v1/chat/messages` with a simple greeting returns `200` and a `conversation_id`.
- [ ] (Optional) Public web UI: chat panel returns a receptionist reply for a test message.
- [ ] Redis guardrails active: repeated chat requests eventually return `429` when limits are exceeded (only in load test environments).
- [ ] With `EMAIL_JOB_DISPATCH_ENABLED=true`, book an appointment in chat and confirm a worker log line such as `email_job_consumer_started` / job processing.
- [ ] With `EMAIL_PROVIDER=fake`, confirm an `EmailJob` is created as `pending` after booking, then reaches `sent` after the worker runs (`provider_message_id` like `fake-0`).
- [ ] (Optional Resend) With verified sending subdomain, `EMAIL_PROVIDER=resend`, valid `RESEND_API_KEY` and `EMAIL_FROM_ADDRESS`, and worker running: confirm booking or reschedule, run or wait for worker, verify `EmailJob` moves `pending` → `sent` with `provider_message_id`, Resend dashboard shows the send, and mail arrives (inbox or spam/junk). Do not store real keys in Git.
- [ ] With `RETELL_ENABLED=false`, Retell routes return `503` with `retell_disabled` (expected until voice is configured).
- [ ] (Optional) With `RETELL_WEB_CALL_ENABLED=true` and frontend voice flag on, `POST /api/v1/demo/voice/retell-web-call` returns `access_token` and `call_id` without exposing API keys in the response body.

## Rollback Notes

1. **Application rollback:** redeploy the previous known-good container image tag for API and worker.
2. **Database rollback:** prefer **forward-fix** migrations. Alembic downgrade is available (`python -m alembic downgrade -1`) but review migration diffs before using in shared environments.
3. **Configuration rollback:** revert environment variables in the secret manager; restart API and worker.
4. **Provider rollback:** switch `LLM_PROVIDER=fake` and/or `EMAIL_PROVIDER=fake` to remove external dependency without code rollback.
5. **Traffic:** remove the new revision from the load balancer if readiness checks fail; keep worker running until in-flight email jobs finish or reach terminal state.

## Secret Handling

- Never commit `.env`, real API keys, or webhook secrets to Git.
- Store secrets in the platform secret manager; reference `.env.demo.example` for key names only.
- Required secret groups when providers are enabled:
  - `GROQ_API_KEY`
  - `RESEND_API_KEY`
  - `RETELL_WEBHOOK_SECRET`, `RETELL_API_KEY`
  - Database, Redis, and RabbitMQ connection URLs with embedded credentials
- Settings fields for API keys use `repr=False` so secrets are not exposed in model repr output.
- Rotate Retell webhook secrets and provider keys on compromise; update deployment env and restart services.

## Demo Safety Notes

- Enable **`PUBLIC_DEMO_GUARDRAILS_ENABLED=true`** for any internet-facing demo.
- Set **`TRUST_PROXY_HEADERS=true`** when the platform terminates TLS and forwards `X-Forwarded-For`; otherwise all clients may appear as the proxy IP.
- Use **`APP_DEBUG=false`** in hosted environments.
- Keep **`RETELL_ALLOW_INSECURE_WEBHOOKS=false`** outside local/test/development.
- Treat all data as **fictional demo data**; do not load real patient information.
- Confirmation email quotas may skip outbound mail while still allowing bookings — this is intentional abuse protection.
- Review demo limit env vars (`DEMO_*`) before launch; defaults are conservative but not a substitute for edge WAF/CAPTCHA.
- Before enabling **`EMAIL_PROVIDER=resend`**, verify a sending subdomain in Resend, enable guardrails, and use `EMAIL_FROM_ADDRESS` on that verified domain. Cancellation emails are not implemented; appointment mail is plain text with clinic-local date/time. New subdomains may land in spam/junk despite passing DNS checks.

## Intentionally Not Production-Ready

This deployment target is a **portfolio public demo**, not a HIPAA-ready clinic product:

| Area | Current scope |
|------|----------------|
| **Patient data** | Fictional demo clinic only; no real PHI |
| **Authentication / RBAC** | Public chat and Retell tool routes are unauthenticated; internal admin APIs are not hardened for open internet |
| **Abuse protection** | Redis-backed demo guardrails and quotas — not a full abuse platform, WAF, or bot management |
| **Retell dashboard** | Agent, prompts, and custom functions are configured in the Retell console; see [Retell Dashboard Setup](retell-dashboard-setup.md) |
| **Observability** | Structured JSON logs to stdout; optional OTEL collector not configured in template |
| **Email delivery** | At-least-once semantics; plain-text appointment confirmations with clinic-local date/time; cancellation emails not implemented; verified Resend subdomain recommended for real mail; new domains may land in spam/junk |
| **Multi-tenancy / SLA** | Single demo clinic tenant |

## Troubleshooting

### Database connection failures

**Symptoms:** `/health/dependencies` shows `"database": {"status": "unavailable"}`; API errors on chat/booking.

**Checks:**

- Verify `DATABASE_URL` format: `postgresql+psycopg://user:pass@host:5432/dbname`
- Confirm migrations ran: `python -m alembic upgrade head`
- Confirm network/firewall from API and worker to Postgres
- Check connection pool limits on small managed tiers

### Redis connection failures

**Symptoms:** `503` on chat or Retell tools with `demo_guardrail_store_unavailable`; holds may fail.

**Checks:**

- Verify `REDIS_URL` (non-localhost in production public demo)
- Confirm Redis is reachable from API (not only from worker)
- Check TLS/`rediss://` requirements from your Redis provider

### RabbitMQ connection failures

**Symptoms:** Worker exits or cannot start; email jobs stay `pending` after booking.

**Checks:**

- Verify `RABBITMQ_URL` and vhost/user permissions
- Confirm queue `EMAIL_JOB_QUEUE_NAME` exists (worker declares it on startup)
- Ensure `EMAIL_JOB_DISPATCH_ENABLED=true` on the API
- Confirm API and worker use the same queue name

### Missing provider keys

**Symptoms:** Process fails at startup with validation errors (`GROQ_API_KEY`, `RESEND_API_KEY`, `RETELL_WEBHOOK_SECRET`, etc.).

**Checks:**

- Provider keys are required **only when that provider is enabled**
- For smoke tests, use `LLM_PROVIDER=fake`, `EMAIL_PROVIDER=fake`, `RETELL_ENABLED=false`
- See `.env.demo.example` comments for conditional requirements

### Email worker not processing

**Symptoms:** Jobs remain `pending` in Postgres; no `email_worker_iteration_completed` / consumer logs.

**Checks:**

- Worker command is `python -m scripts.run_email_worker` (not the API command)
- `EMAIL_JOB_DISPATCH_ENABLED=true` on API
- RabbitMQ consumer is running and connected
- Job `next_attempt_at` is in the past (retries are schedule-driven in Postgres)
- Polling fallback: `python -m scripts.run_email_job_worker` processes due jobs without RabbitMQ wake-up

### Groq fallback mode

**Symptoms:** LLM analysis fails or latency spikes; chat still responds.

**Behavior:**

- Default demo config keeps `LLM_FALLBACK_ENABLED=false`
- Primary Groq failures fall back to **deterministic** receptionist behavior and structured repair paths inside the reliability layer
- Optional `LLM_FALLBACK_PROVIDER` (for example `fake` or `bedrock`) requires explicit `LLM_FALLBACK_ENABLED=true`
- For deterministic demo without Groq outages, use `LLM_PROVIDER=fake`

See [Groq LLM Provider](../architecture/groq-llm-provider.md) and [LLM Reliability Orchestration](../architecture/llm-reliability-orchestration.md).

### Retell disabled route behavior

**Symptoms:** Retell tool or lifecycle webhook returns `503`.

**Expected when `RETELL_ENABLED=false`:**

- Protected Retell routes reject with `503` and code `retell_disabled`
- Chat and scheduling APIs continue to work
- To enable voice: set `RETELL_ENABLED=true`, configure `RETELL_WEBHOOK_SECRET`, deploy with signature verification, configure tools and webhooks in the Retell dashboard ([runbook](retell-dashboard-setup.md))

See [Retell Webhook Security](../architecture/retell-webhook-security.md).

## Related Documentation

- [Configuration](../configuration.md)
- [Local Development](local-development.md)
- [Retell Dashboard Setup](retell-dashboard-setup.md)
- [Public demo web frontend](../../web/README.md)
- [Public Demo Guardrails](../architecture/public-demo-guardrails.md)
- [Email Dispatch Reliability](../architecture/email-dispatch-reliability.md)
- [Email Job Worker](../architecture/email-job-worker.md)
