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
| `NEXT_PUBLIC_GITHUB_URL` | Repository link for the landing page and footer |
| `NEXT_PUBLIC_ARCHITECTURE_DOC_URL` | Architecture documentation link |
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

## Voice feature flag

`NEXT_PUBLIC_VOICE_DEMO_ENABLED` controls the voice panel only. It does not enable Retell on the backend.

| Value | Behavior |
|-------|----------|
| `false` (default) | **Call the clinic** opens a configuration preview: explains backend voice integration is prepared; **no microphone** access |
| `true` | Shows **Start call** with a placeholder/mock call flow; microphone is requested only after the user clicks **Start call**; no Retell Web SDK or private API keys yet |

Backend Retell routes remain independent (`RETELL_ENABLED` in `.env.demo.example`).

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
- Feature-flagged voice demo entry point (`VoiceCallPanel`, mock connection)
- No dashboard, no Retell private keys, no real Retell web call yet

Hosted deploy checklist: [`docs/operations/public-demo-deployment.md`](../docs/operations/public-demo-deployment.md).
