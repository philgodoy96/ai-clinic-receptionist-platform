# Retell Dashboard Setup Runbook

This runbook describes how to configure the **Retell dashboard** for the portfolio public demo. The backend and frontend in this repository already expose tool routes, verified webhooks, web-call creation, and the Retell Web SDK entry point. Retell project setup (agent, prompts, custom functions, webhooks) happens **outside the repo** in the Retell console.

Use this document together with:

- [Retell Master Prompt v3](retell-master-prompt-v3.md) — canonical agent system prompt (`retell-receptionist-v3`)
- [Retell Tool Descriptions](retell-tool-descriptions.md) — dashboard tool descriptions, arguments, errors, recovery
- [Retell Voice Smoke Scenarios](retell-voice-smoke-scenarios.md) — realistic end-to-end voice test scripts
- [Retell Conversation UX Playbook](retell-conversation-ux-playbook.md) — caller-facing dialogue examples
- [Public Demo Deployment](public-demo-deployment.md)
- [Configuration](../configuration.md)
- [Retell Tool-Calling Adapter](../architecture/retell-tool-calling-adapter.md)
- [Clinic Time Context and Tool Contracts](../architecture/clinic-time-context-and-tool-contracts.md)
- [Voice Patient Identity Resolution](../architecture/voice-patient-identity-resolution.md)
- [Retell Webhook Security](../architecture/retell-webhook-security.md)
- [Retell Call Lifecycle](../architecture/retell-call-lifecycle.md)

## Prerequisites

Before configuring Retell:

| Requirement | Notes |
|-------------|--------|
| **Hosted API** | Public HTTPS origin for the FastAPI app (for example `https://api.example.com`) |
| **PostgreSQL + Redis** | Required for voice persistence, holds, and public demo guardrails |
| **Migrations applied** | `python -m alembic upgrade head` |
| **Demo data (optional)** | `python -m scripts.seed_demo_data` if you rely on pre-seeded availability |
| **Retell account** | Retell project with API key access |
| **Public demo guardrails** | `PUBLIC_DEMO_MODE=true` and `PUBLIC_DEMO_GUARDRAILS_ENABLED=true` for internet-facing demos |
| **Frontend (optional)** | Next.js `web/` app with `NEXT_PUBLIC_VOICE_DEMO_ENABLED=true` for browser web calls |

Treat all demo traffic as **fictional clinic data**. The public demo must **not** collect real PHI (protected health information). Use only sample names, dates of birth, emails, and phone numbers intended for testing — for example seeded patients (`john.miller@example.test`) or clearly fictional contact details. Do not use real patient names, real medical record numbers, real insurance IDs, or production clinic data in prompts, test calls, recordings, or logs.

## UX documentation pack

Configure the Retell agent using these companion documents (copy/paste sources and smoke-test scripts):

| Document | Purpose |
|----------|---------|
| [Retell Master Prompt v3](retell-master-prompt-v3.md) | Canonical system prompt (`retell-receptionist-v3`) — paste [paste-ready block](retell-master-prompt-v3.md#paste-ready-retell-master-prompt) into agent instructions |
| [Retell Tool Descriptions](retell-tool-descriptions.md) | Dashboard tool descriptions, argument contracts, error recovery wording |
| [Retell Voice Smoke Scenarios](retell-voice-smoke-scenarios.md) | End-to-end voice smoke tests after dashboard setup |
| [Retell Conversation UX Playbook](retell-conversation-ux-playbook.md) | Caller-facing dialogue examples and anti-patterns |

## Who controls what

| Layer | Controls | Does not control |
|-------|----------|------------------|
| **Retell agent prompt** | Conversation flow, tone, one question at a time, when to call tools, natural scheduling language, existing vs new patient intake dialogue, `end_call` timing, recovery phrasing after tool errors | Availability, holds, booking outcomes, business hours, patient record creation policy |
| **Backend tools + services** | Scheduling invariants, hold TTL, booking/cancel/reschedule side effects, clinic time resolution, patient lookup vs demo auto-create (`VOICE_PATIENT_INTAKE_MODE`), idempotency, guardrails, webhook signature verification | Spoken turn-taking, filler words, or whether the agent asks a follow-up question |

The **prompt controls conversation flow**. The **backend controls invariants**. A successful tool result (`status: succeeded`) is required before the agent confirms booked, cancelled, or rescheduled. Failed tools return structured `error_code` values; the agent should use natural recovery language from the master prompt and tool descriptions — never read internal codes or UUIDs aloud.

Optional `suggested_response_text` on tool results is backend-generated phrasing hints (deterministic templates). Retell may use or paraphrase them; they do not change tool contracts or scheduling outcomes (see [Receptionist Response Generator](../architecture/receptionist-response-generator.md)).

## Environment Variable Checklist

Configure these on the **API service** (see `.env.demo.example` and [Configuration](../configuration.md)). Secrets belong in the platform secret manager, not in Git or `NEXT_PUBLIC_*` frontend env.

### Retell inbound (tools + lifecycle webhooks)

| Variable | Required when | Notes |
|----------|---------------|--------|
| `RETELL_ENABLED` | Voice tools/webhooks | `true` to accept Retell callbacks |
| `RETELL_API_KEY` | Web calls + Retell API use | Server-side only; never in `web/` |
| `RETELL_WEBHOOK_VERIFICATION_ENABLED` | Hosted demo | Keep `true` |
| `RETELL_WEBHOOK_SECRET` | `RETELL_ENABLED=true` and verification on | From Retell webhook settings |
| `RETELL_ALLOW_INSECURE_WEBHOOKS` | Never in production-like env | `false` only outside local/test |
| `RETELL_SIGNATURE_HEADER_NAME` | Optional | Default `x-retell-signature` |
| `RETELL_REQUEST_MAX_BODY_BYTES` | Optional | Default `262144` |

### Retell web calls (browser demo)

| Variable | Required when | Notes |
|----------|---------------|--------|
| `RETELL_WEB_CALL_ENABLED` | Public web voice demo | `true` to allow `POST /api/v1/demo/voice/retell-web-call` |
| `RETELL_AGENT_ID` | `RETELL_WEB_CALL_ENABLED=true` | Agent ID from Retell dashboard |
| `RETELL_AGENT_VERSION` | Optional | Numeric version or tag (for example `1`, `prod`) |
| `RETELL_WEB_CALL_TIMEOUT_SECONDS` | Optional | Default `10` |

### Clinic context (must match agent instructions)

| Variable | Notes |
|----------|--------|
| `CLINIC_TIMEZONE` | IANA timezone (default `America/New_York`) |
| `CLINIC_BUSINESS_DAYS` | Comma-separated weekdays |
| `CLINIC_BUSINESS_HOURS_START` / `CLINIC_BUSINESS_HOURS_END` | `HH:MM` 24-hour |
| `CLINIC_NAME` | Spoken/display name for demo |

### Voice patient intake (booking identity)

| Variable | Notes |
|----------|--------|
| `VOICE_PATIENT_INTAKE_MODE` | `lookup_only` (default, production-like) or `demo_auto_create` (public demo). See [Configuration — Voice patient intake](../configuration.md#voice-patient-intake). |

- **`lookup_only`** — `book_appointment` requires a pre-existing patient match on name, date of birth, and email. Use seeded demo patients or create records ahead of time. On mismatch, the agent should re-collect details naturally; never say "patient not found" to the caller.
- **`demo_auto_create`** — For public demo only: when identity is new and the caller provides a syntactically valid email, the backend may create a minimal patient record before booking. Explicit confirmation, active hold, and booking idempotency are unchanged. Public demo prompts should still discourage real PHI; the backend does not enforce `.test` domains.

Recommended: `lookup_only` for production-like environments; `demo_auto_create` in `.env.demo.example` for hosted public demo voice testing.

### Frontend (web service only)

| Variable | Notes |
|----------|--------|
| `NEXT_PUBLIC_VOICE_DEMO_ENABLED` | `true` to show live voice UI |
| `API_PROXY_TARGET` | Proxies `/api/v1/*` to API origin |
| **Never** | `NEXT_PUBLIC_RETELL_*`, API keys, or webhook secrets |

### Safe smoke-test fallback (chat-only)

```env
RETELL_ENABLED=false
RETELL_WEB_CALL_ENABLED=false
NEXT_PUBLIC_VOICE_DEMO_ENABLED=false
```

## Agent Setup Steps

1. **Create or select an agent** in the Retell dashboard for the fictional demo clinic receptionist.
2. **Copy the agent ID** into `RETELL_AGENT_ID` when web calls are enabled.
3. **Choose a response engine** supported by your Retell project (Retell LLM or conversation flow). Tool definitions must call the backend HTTP endpoints documented below.
4. **Set the agent language/locale** to match `CLINIC_LOCALE` (default `en-US`).
5. **Publish the agent version** you intend to use; set `RETELL_AGENT_VERSION` if you pin a specific version or tag.
6. **Disable phone/PSTN routing** for the portfolio web-demo path unless you explicitly need telephony; this runbook targets **web calls** via the public demo API and Retell Web SDK.
7. **Smoke-test in Retell’s test UI** only with fictional patient details after backend env vars and webhooks are configured.

## Prompt Guidance

Copy the **paste-ready** system prompt from **[Retell Master Prompt v3](retell-master-prompt-v3.md#paste-ready-retell-master-prompt)** (`retell-receptionist-v3`) into the Retell agent instructions. The backend tools are the source of truth for scheduling, holds, identity resolution, booking, cancellation, and rescheduling. The LLM must not invent availability, patient contact details, or confirm appointments without a successful tool result (`status: succeeded`).

Summary rules (details and caller-facing language are in the master prompt and [UX playbook](retell-conversation-ux-playbook.md)):

1. **Sample contact information only** — one privacy sentence at call start; no repeated demo language. **Do not collect real PHI** in the public demo.
2. **Do not calculate relative dates** — call `get_clinic_context` before “today”, “tomorrow”, or weekday scheduling language.
3. **Structured scheduling arguments** — prefer `date_expression` and optional `time_window_expression` in `check_availability`.
4. **Natural scheduling language** — say “appointment time”, “opening”, “schedule”, “that time”. **Never** say “slot”, “hold reference”, or read UUIDs aloud.
5. **Hold before book** — `hold_appointment_slot` after the caller chooses a time; say **“I can hold that time while I get your details.”**
6. **Resolve identity before book** — `resolve_patient_identity` after name + DOB (and email/phone when collected); `confirm_patient_identity` after `possible_match`.
7. **Summary + explicit confirmation** — read back details; call `book_appointment` with `patient_resolution_id` when available, only after a clear yes with `explicit_confirmation: true`.
8. **Never invent email** — use only the address the caller provided and confirmed.
9. **Confirm only after tool success** — do not say booked, cancelled, or rescheduled until the side-effect tool returns `status: succeeded`.
10. **Recover from hold expiry** — say **“That time may no longer be available. Let me check the schedule again.”** then re-run availability and hold.

See [Clinic Time Context and Tool Contracts](../architecture/clinic-time-context-and-tool-contracts.md) for scheduling expression reference.

## Patient identity (existing vs new)

Configure the agent prompt so intake follows the master prompt’s **Patient identity** section and uses the dedicated resolution tools:

| Caller type | Agent behavior | Backend tools |
|-------------|----------------|---------------|
| **Existing patient** | Ask full name and DOB one at a time; confirm DOB naturally; collect/confirm email when needed. Call `resolve_patient_identity`. On `possible_match`, ask `confirmation_question` then `confirm_patient_identity`. On `multiple_matches`, ask for email or phone once and re-resolve. | `resolve_patient_identity` → `confirm_patient_identity` (if needed) → `book_appointment` with `patient_resolution_id` |
| **New patient** | Collect name, DOB, and email one question at a time; confirm before resolving. **Always** call `resolve_patient_identity` with `caller_claims_existing_patient: false` and `allow_demo_patient_creation: true` only after the caller confirms a valid email — even if they said they are new. Backend may return `possible_match` before creating a demo record. Never call a tool in the same turn after asking "is that correct?" | `resolve_patient_identity` → (`confirm_patient_identity` if needed) → `book_appointment` with `patient_resolution_id` |

Inline `patient_name` / `patient_date_of_birth` / `patient_email` on `book_appointment` remain a fallback when no resolution token is used; prefer `patient_resolution_id` for all new dashboard setups.

Seeded demo patients for smoke tests (see [Voice Smoke Scenarios](retell-voice-smoke-scenarios.md)):

| Name | DOB | Email |
|------|-----|-------|
| John Miller | 1985-04-12 | `john.miller@example.test` |
| Ava Thompson | 1992-09-03 | `ava.thompson@example.test` |
| Michael Lee Reed | 1988-03-15 | `michael.lee.reed@example.test` |

For the public demo, prefer fictional sample contact details (for example seeded patients or `felipe.logan@example.test`). The backend accepts any syntactically valid email when `demo_auto_create` is enabled. Never invent email — the caller must speak and confirm it before `resolve_patient_identity` or `book_appointment`.

## end_call Rules

`end_call` is a **Retell agent action**, not a backend tool. The prompt controls when the agent ends the call; the backend does not trigger `end_call`.

**Critical:** Manual testing showed the agent must not call `end_call` immediately after asking “Have you been seen at this clinic before?” while a hold is active. Configure the prompt with the [v3 end_call rules](retell-master-prompt-v3.md#paste-ready-retell-master-prompt).

Configure the prompt so the agent:

| Do | Do not |
|----|--------|
| End after the caller clearly says goodbye, that's all, no thanks, or explicitly asks to stop | **Never** call `end_call` after asking any question |
| End only after active scheduling is resolved (booked, cancelled, rescheduled, or hold released) | **Never** call `end_call` while an appointment time is being held |
| End after a polite closing (“Thank you for calling”) once the caller confirms nothing else is needed | **Never** call `end_call` before patient identity is resolved or the hold is released |
| Release a held time (`release_appointment_hold`) if the caller abandons booking, then close | **Never** call `end_call` while waiting for: existing/new patient answer, name, DOB, email, possible-match confirmation, or final booking confirmation |
| Wait through brief silence while the caller checks a calendar | End during identity collection, hold, or tool execution |
| Ask “Is there anything else you need today?” after a successful booking and **wait for the answer** | End on “um” or short pauses mid-flow |
| Continue the conversation when unsure whether the caller is finished | End the call while a tool request is in flight |

Full anti-patterns and dialogue examples: [Retell Conversation UX Playbook — Caller wants to end call](retell-conversation-ux-playbook.md#9-caller-wants-to-end-the-call) and [Smoke Scenario 8](retell-voice-smoke-scenarios.md#scenario-8--no-premature-end_call).

## Custom Function / Tool Setup Checklist

Register **custom functions** (or equivalent HTTP tools) in the Retell dashboard pointing at your **public API origin**. Use the unified tool executor unless you rely on legacy per-tool routes.

### Retell payload envelope (use Retell defaults)

Custom Functions should use Retell’s **default payload envelope** — do **not** set **Payload: args only** in the Retell dashboard.

Retell sends native tool callbacks shaped like:

```json
{
  "name": "get_clinic_context",
  "call": { "call_id": "call_..." },
  "args": {}
}
```

The backend verifies the raw `x-retell-signature` on that body, then **normalizes** Retell-native fields into the internal tool contract (`tool_name`, `provider_call_id`, `arguments`, `tool_call_id`) before running the existing adapter.

**Payload: args only must stay OFF** in the Retell dashboard. When enabled, Retell omits envelope fields the backend needs for `call_id`, signature verification context, and `tool_call_id` idempotency.

Your agent **prompt** controls conversation flow and the JSON inside `args`. It does **not** define the HTTP envelope. The backend owns the provider integration boundary and scheduling invariants.

Manual `curl` tests may still send the normalized internal shape; both are accepted after signature verification.

### Unified tool endpoint (recommended)

| Setting | Value |
|---------|--------|
| **URL** | `https://<api-host>/api/v1/retell/tools` |
| **Method** | `POST` |
| **Auth** | Retell webhook signature (Retell sends `x-retell-signature`; backend verifies with `RETELL_WEBHOOK_SECRET`) |

### Supported tool names (allowlist)

Register only tools implemented by the backend:

| Tool name | Side effects | Notes |
|-----------|--------------|--------|
| `get_clinic_context` | No | Call before discussing date/time/hours |
| `check_availability` | No | Prefer `date_expression` |
| `hold_appointment_slot` | Yes | Redis-backed hold |
| `release_appointment_hold` | Yes | Releases caller’s hold |
| `resolve_patient_identity` | Yes* | Identity resolution; idempotent retries; demo create when allowed |
| `confirm_patient_identity` | Yes* | Confirms or rejects `possible_match` tokens |
| `book_appointment` | Yes | Requires hold + resolved identity + confirmation; prefers `patient_resolution_id` |
| `cancel_appointment` | Yes | Requires confirmation |
| `reschedule_appointment` | Yes | Requires confirmation + target hold/slot |

Do **not** register tools that bypass this allowlist. Unsupported tools return `unsupported_retell_tool`.

\* `resolve_patient_identity` and `confirm_patient_identity` are classified as side-effecting for idempotency (`tool_call_id` deduplication). They do not book appointments or send email.

### Per-tool checklist

- [ ] Tool name matches the backend allowlist exactly (snake_case).
- [ ] Custom Function payload mode is **Retell default envelope** (not **Payload: args only**).
- [ ] Retell sends `name`, `call.call_id`, and `args`; backend normalizes these automatically.
- [ ] Dashboard **description** for each tool pasted from [Retell Tool Descriptions](retell-tool-descriptions.md).
- [ ] Tool URL uses HTTPS and matches the deployed API host.
- [ ] Retell project webhook secret matches `RETELL_WEBHOOK_SECRET`.
- [ ] `get_clinic_context` is available and invoked before relative date discussion in prompt tests.
- [ ] `check_availability` test uses `date_expression` (not free-form date math).
- [ ] Side-effecting tools are tested with fictional identity data only.

Legacy per-tool routes under `/api/v1/retell/tools/*` remain for compatibility; new dashboard setup should prefer the unified route.

### Local tunneling (ngrok)

When exposing a local API to Retell webhooks via ngrok or a similar tunnel:

1. Point Retell **tool** and **lifecycle** webhook URLs at the tunnel HTTPS origin (for example `https://<subdomain>.ngrok-free.app/api/v1/retell/tools` and `.../api/v1/retell/webhooks/lifecycle`).
2. Set `TRUST_PROXY_HEADERS=true` on the API so per-IP demo guardrails see the caller IP from `X-Forwarded-For`, not the tunnel edge.
3. **Preserve the `x-retell-signature` header** — Retell signs the raw request body; do not strip or rewrite signature headers at the tunnel or reverse proxy. If verification fails with a valid secret, confirm the proxy forwards `x-retell-signature` unchanged.
4. Keep **Payload: args only** **OFF** so Retell sends `call.call_id` and provider `tool_call_id` for idempotency (see [Smoke Scenario 10](retell-voice-smoke-scenarios.md#scenario-10--provider-tool_call_id-behavior)).
5. Use the same tunnel base URL for all nine custom functions and the lifecycle webhook unless you intentionally split hosts.
6. For local-only testing without real signatures, use `RETELL_ALLOW_INSECURE_WEBHOOKS=true` in `local` / `development` `APP_ENV` only (see [Retell Webhook Security](../architecture/retell-webhook-security.md)).

## Webhook Setup Checklist

### Lifecycle webhook

| Setting | Value |
|---------|--------|
| **URL** | `https://<api-host>/api/v1/retell/webhooks/lifecycle` |
| **Verification** | **Must stay enabled** — `RETELL_WEBHOOK_VERIFICATION_ENABLED=true` |
| **Secret** | Copy into `RETELL_WEBHOOK_SECRET` |

Retell sends native lifecycle payloads (`event`, `call.call_id`, `call.start_timestamp` / `call.end_timestamp`, top-level `event_timestamp`, and provider metadata such as `agent_version`). The backend verifies the signature on the raw body, coerces provider metadata to the internal schema (for example `agent_version: 0` → `"0"`), normalizes timestamps (epoch ms/s or ISO) into `occurred_at`, drops sensitive fields like `access_token`, then persists via the existing lifecycle pipeline. Manual tests may still send the normalized internal shape.

Lifecycle events create/update `VoiceCall` and `VoiceCallEvent` records. They do **not** execute scheduling tools.

### Security requirements

- [ ] `RETELL_WEBHOOK_VERIFICATION_ENABLED=true` in every production-like environment.
- [ ] `RETELL_ALLOW_INSECURE_WEBHOOKS=false` in production-like `APP_ENV`.
- [ ] Webhook secret stored only in the API secret manager.
- [ ] Public demo guardrails enabled (`PUBLIC_DEMO_GUARDRAILS_ENABLED=true`).
- [ ] `TRUST_PROXY_HEADERS=true` when behind a reverse proxy so per-IP limits work.

Invalid signatures are rejected **before** guardrails or tool logic run (see [Retell Webhook Security](../architecture/retell-webhook-security.md)).

## Web Call Setup Steps

Web calls let the browser join a Retell room without exposing `RETELL_API_KEY` to the client.

### Backend

1. Set `RETELL_WEB_CALL_ENABLED=true`.
2. Set `RETELL_API_KEY` and `RETELL_AGENT_ID` (and optional `RETELL_AGENT_VERSION`).
3. Keep `RETELL_ENABLED=true` if you also use tool and lifecycle webhooks on the same deployment.
4. Deploy the API and confirm `POST /api/v1/demo/voice/retell-web-call` returns `access_token` and `call_id` (with guardrails and demo mode configured).

### Frontend

1. Set `NEXT_PUBLIC_VOICE_DEMO_ENABLED=true` on the web service.
2. Set `API_PROXY_TARGET` to the API origin (or serve UI and API on the same public host).
3. Deploy `web/` (`npm run build` / `npm run start`).
4. Confirm the voice panel requests **no microphone** when the flag is `false`.

### Retell dashboard

1. Use the same **agent ID** as `RETELL_AGENT_ID`.
2. Web calls do not require a separate Retell “phone number”; the browser uses the Retell Web SDK with the server-issued `access_token`.
3. Start the browser call within **30 seconds** of receiving the access token (Retell invalidates stale tokens).

Flow:

```mermaid
sequenceDiagram
    participant Browser
    participant Web as Next.js web
    participant API as FastAPI API
    participant Retell as Retell API

    Browser->>Web: Start call
    Web->>API: POST /api/v1/demo/voice/retell-web-call
    API->>Retell: create-web-call (server-side)
    Retell-->>API: access_token, call_id
    API-->>Web: provider-safe token + call_id
    Web->>Retell: Retell Web SDK startCall(accessToken)
    Retell->>API: lifecycle webhooks + tool calls
```

## Safety Boundaries

| Rule | Rationale |
|------|-----------|
| **Prompt controls flow; backend controls invariants** | Agent decides what to ask and when; tools enforce holds, hours, confirmation, and booking outcomes |
| **Backend tools are source of truth** | Retell must not calculate availability, dates, or appointment outcomes locally |
| **No relative date math in Retell** | Use `get_clinic_context` + structured `date_expression` |
| **Webhook verification stays enabled** | Prevents unsigned tool and lifecycle callbacks |
| **No real PHI in public demo** | Fictional names, `.test` emails, and sample DOBs only; no production patient data |
| **No API keys in frontend** | `RETELL_API_KEY` only on API; browser uses short-lived `access_token` |
| **Do not store access tokens in localStorage** | Frontend keeps tokens in memory for the active session only |
| **Public demo guardrails** | Rate limits on chat, Retell tools, and voice web-call creation |
| **Signature before guardrails** | Invalid Retell signatures do not consume rate-limit counters |
| **Natural voice wording** | Avoid “slot”, hold references, and UUIDs in spoken confirmation (see master prompt and backend `suggested_response_text` templates) |

The adapter cannot bypass `AppointmentBookingService`, hold rules, clinic business hours, patient intake mode, or confirmation requirements (see [Retell Tool-Calling Adapter](../architecture/retell-tool-calling-adapter.md)).

## Smoke Test Checklist

Run the scripted scenarios in **[Retell Voice Smoke Scenarios](retell-voice-smoke-scenarios.md)** after dashboard and deployment configuration.

After dashboard and deployment configuration:

### API / Retell inbound

- [ ] `GET /health` and `GET /health/dependencies` return `200`.
- [ ] With `RETELL_ENABLED=false`, Retell routes return `503` / `retell_disabled` (expected until enabled).
- [ ] With `RETELL_ENABLED=true`, unsigned tool request returns signature error (not `200`).
- [ ] Retell test tool call to `get_clinic_context` returns clinic name, timezone, `current_date`, and business hours.
- [ ] Lifecycle webhook delivery creates a `VoiceCall` row for the provider `call_id`.
- [ ] `check_availability` with `date_expression: { "kind": "tomorrow" }` returns slots or a structured error (not a crash).

### Web call + frontend

- [ ] With `NEXT_PUBLIC_VOICE_DEMO_ENABLED=false`, voice panel shows configuration preview and **does not** request microphone.
- [ ] With flags enabled, **Start call** returns `access_token` via backend (check network tab: no `RETELL_API_KEY` in browser).
- [ ] Retell Web SDK connects and call reaches connected state with fictional test speech.
- [ ] **End call** resets UI to ended/idle.
- [ ] Backend error (for example disabled web call) shows a safe message (`voice_demo_disabled`, `rate_limited`, etc.), not raw provider payloads.

### Abuse / demo safety

- [ ] Repeated voice or tool requests eventually return `429` when guardrails are enabled.
- [ ] Demo disclaimer visible in UI (fictional clinic).
- [ ] Callers use sample contact information only — no real PHI collected or spoken.
- [ ] `VOICE_PATIENT_INTAKE_MODE` matches environment (`demo_auto_create` for public demo; `lookup_only` otherwise).
- [ ] Transcript review: no “slot”, “hold reference”, “patient not found”, or UUID strings in agent speech (see [Smoke Scenarios — transcript review](retell-voice-smoke-scenarios.md)).

## Rollback Steps

1. **Disable voice in browser:** `NEXT_PUBLIC_VOICE_DEMO_ENABLED=false`; redeploy web.
2. **Disable web calls on API:** `RETELL_WEB_CALL_ENABLED=false`; redeploy API.
3. **Disable Retell inbound:** `RETELL_ENABLED=false`; Retell routes fail closed with `retell_disabled` while chat continues.
4. **Revert agent in Retell dashboard:** point webhooks to a previous agent version or disable webhooks temporarily.
5. **Rotate compromised secrets:** update `RETELL_WEBHOOK_SECRET` and `RETELL_API_KEY` in Retell and in deployment env; restart API.
6. **Application rollback:** redeploy previous known-good API/web image tags (see [Public Demo Deployment](public-demo-deployment.md)).

Prefer forward-fix for database schema; voice call rows are audit artifacts and usually do not require downgrade.

## Related Documentation

- [Retell Master Prompt v3](retell-master-prompt-v3.md)
- [Retell Tool Descriptions](retell-tool-descriptions.md)
- [Retell Voice Smoke Scenarios](retell-voice-smoke-scenarios.md)
- [Retell Conversation UX Playbook](retell-conversation-ux-playbook.md)
- [Public Demo Deployment](public-demo-deployment.md)
- [Local Development](local-development.md)
- [Public demo web frontend](../../web/README.md)
- [Retell Tool API](../api/retell-tools.md)
