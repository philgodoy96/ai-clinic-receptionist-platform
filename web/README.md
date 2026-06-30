# AI Clinic Receptionist — Public Demo Frontend

Minimal Next.js shell for the portfolio public demo. The UI calls the FastAPI backend for chat; it does not embed business logic or provider secrets.

**Demo safety:** This app presents a **fictional clinic** scenario. Do not submit real personal or medical information.

## Prerequisites

- Node.js 20+
- npm
- Running backend API (see [`docs/operations/local-development.md`](../docs/operations/local-development.md))

Frontend dependencies install with npm inside `web/`. This is independent of the Python virtual environment used for the backend.

You can also run npm scripts from the repository root (`npm run dev`, `npm run build`, etc.); they delegate to `web/`.

## Setup

```bash
cd web
cp .env.example .env.local
npm install
```

Edit `.env.local` for your GitHub links and feature flags. Never add API keys, webhook secrets, or database URLs to this app.

## Local run

```bash
cd web
npm run dev
```

Open [http://localhost:3000](http://localhost:3000).

With default settings, the browser calls `/api/v1/*` on the Next.js origin. `next.config.ts` rewrites those requests to `API_PROXY_TARGET` (default `http://localhost:8000`), so you do not need backend CORS for local development.

## Build and production serve

```bash
cd web
npm run build
npm run start
```

From the repository root:

```bash
npm run build
npm run start
```

## Environment variables

### Public (`NEXT_PUBLIC_*`)

Safe to expose to the browser. Copy from [`.env.example`](.env.example).

| Variable | Description |
|----------|-------------|
| `NEXT_PUBLIC_API_BASE_URL` | Backend API origin for display and server-side references (no trailing slash). Default: `http://localhost:8000` |
| `NEXT_PUBLIC_GITHUB_URL` | Repository link for the landing page and footer (optional; defaults to the portfolio repo in `lib/config.ts`) |
| `NEXT_PUBLIC_ARCHITECTURE_DOC_URL` | Architecture documentation link (optional; defaults to GitHub `docs/architecture` in `lib/config.ts`) |
| `NEXT_PUBLIC_VOICE_DEMO_ENABLED` | Voice demo feature flag (`true` / `false`) |

### Server-only

| Variable | Description |
|----------|-------------|
| `API_PROXY_TARGET` | Origin used by Next.js rewrites for `/api/v1/*` → backend. Defaults to `NEXT_PUBLIC_API_BASE_URL` or `http://localhost:8000`. Not exposed to the browser. |

Run `npm run check:env-safety` to scan for forbidden public env names (for example `API_KEY`, `SECRET`, `GROQ`, `RETELL`).

## Backend URL configuration

The chat client always requests **`/api/v1/chat/messages`** on the frontend origin. Configure where those requests are proxied:

| Environment | Typical setup |
|-------------|----------------|
| **Local** | `API_PROXY_TARGET=http://localhost:8000` in `.env.local` |
| **Hosted (split services)** | Set `API_PROXY_TARGET` to the public API URL at build/deploy time |
| **Hosted (same domain)** | Terminate `/api/v1` on the API service at your edge, or keep Next rewrites pointed at the internal API URL |

The backend does **not** ship CORS middleware. Prefer same-origin access via Next.js rewrites or a reverse proxy. If the browser must call the API on a different origin directly, you must add CORS on the API separately (not configured in this repo today).

## Voice demo

`NEXT_PUBLIC_VOICE_DEMO_ENABLED` controls the **frontend** voice panel only. It does not enable Retell on the backend (`RETELL_WEB_CALL_ENABLED` on the API).

### Flag behavior

| Value | Behavior |
|-------|----------|
| `false` (default) | **Call the clinic** shows a configuration preview. **No microphone** permission is requested. |
| `true` | **Start call** is shown. When the user starts a call, the app fetches a server-issued token and connects via the Retell Web SDK. |

### Backend web call endpoint

When voice is enabled on both frontend and backend:

| | |
|---|---|
| **Endpoint** | `POST /api/v1/demo/voice/retell-web-call` (proxied via `/api/v1/*` like chat) |
| **Request body** | Optional `{ "demo_session_id": null, "conversation_id": null }` |
| **Success response** | `provider`, `call_id`, `access_token`, `expires_in_seconds`, optionally `conversation_id` |
| **Errors** | Standard API error envelope (`voice_demo_disabled`, `rate_limited`, `provider_unavailable`, etc.) |

The client is `createRetellWebCall` in `lib/api-client.ts`. The backend never returns `RETELL_API_KEY` or raw Retell provider payloads.

### Microphone permission

- **Disabled (`false`):** The voice panel never loads the Retell SDK or calls `getUserMedia`.
- **Enabled (`true`):** Microphone access is requested only after the user clicks **Start call** and the backend returns an `access_token`. Denied permission surfaces a user-safe error; the app does not auto-retry.

### No Retell secrets in the frontend

- `RETELL_API_KEY`, webhook secrets, and agent configuration stay on the FastAPI backend.
- The browser keeps the short-lived `access_token` in memory for the active session only (not `localStorage`).
- Run `npm run check:env-safety` to scan for forbidden `NEXT_PUBLIC_*` names (for example `RETELL`, `API_KEY`).

Retell tool and lifecycle webhooks are controlled separately (`RETELL_ENABLED`). Dashboard and hosted deploy steps: [`docs/operations/retell-dashboard-setup.md`](../docs/operations/retell-dashboard-setup.md) and [`docs/operations/public-demo-deployment.md`](../docs/operations/public-demo-deployment.md).

## Scripts

```bash
npm run dev              # local development server
npm run lint             # ESLint
npm run typecheck        # TypeScript (no emit)
npm run check:env-safety # scan for forbidden public env names
npm run build            # production build
npm run start            # serve production build
```

## Scope

This app is intentionally small:

- Landing page, layout, and demo disclaimer
- Public config helper in `lib/config.ts`
- Backend-powered chat demo panel (`ChatPanel`) with safe API error handling
- Feature-flagged voice demo entry point (`VoiceCallPanel`, Retell Web SDK behind `NEXT_PUBLIC_VOICE_DEMO_ENABLED`)
- No dashboard, no Retell private keys in the browser

Hosted deploy checklist: [`docs/operations/public-demo-deployment.md`](../docs/operations/public-demo-deployment.md).
