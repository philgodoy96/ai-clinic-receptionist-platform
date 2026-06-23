# AI Clinic Receptionist — Public Demo Frontend

Minimal Next.js shell for the portfolio public demo. It reads public configuration from environment variables and does not embed backend secrets or provider keys.

## Prerequisites

- Node.js 20+
- npm
- Running backend API (see repository root [`docs/operations/local-development.md`](../docs/operations/local-development.md))

Frontend dependencies install with npm inside `web/`. This is independent of the Python virtual environment used for the backend.

## Local setup

```bash
cd web
cp .env.example .env.local
npm install
npm run dev
```

Open [http://localhost:3000](http://localhost:3000).

## Environment variables

All frontend variables are `NEXT_PUBLIC_*` and safe to expose to the browser.

| Variable | Description |
|----------|-------------|
| `NEXT_PUBLIC_API_BASE_URL` | Backend API origin (default: `http://localhost:8000`) |
| `NEXT_PUBLIC_GITHUB_URL` | Repository link for the footer |
| `NEXT_PUBLIC_ARCHITECTURE_DOC_URL` | Architecture documentation link |
| `NEXT_PUBLIC_VOICE_DEMO_ENABLED` | Placeholder flag for a future voiceover web voice demo |

Do not add API keys, webhook secrets, or database URLs to this app.

## Scripts

```bash
npm run dev        # local development server
npm run lint       # ESLint
npm run typecheck  # TypeScript (no emit)
npm run build      # production build
npm run start      # serve production build
```

## Scope

This app is intentionally small:

- Basic layout and landing content
- Public config helper in `lib/config.ts`
- No dashboard
- No Retell web call integration yet
- No chat UI yet
