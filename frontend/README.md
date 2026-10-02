# Payment Recovery Frontend (Stage 5)

React + Vite + TypeScript + Tailwind single-page app for the Payment Recovery
Digital Twin. It is a **thin client**: it renders data from, and issues
actions to, the FastAPI backend — it never fabricates or simulates recovery
data of its own.

## Architecture

```
 Browser
   |
   v
 React SPA (this directory)
   |  react-router-dom routes, TanStack Query for fetching/caching,
   |  axios client (attaches X-API-Key from sessionStorage), zod for
   |  response validation
   v
 FastAPI backend (../api/)  <-- AUTHORITATIVE
   |  state machine, deterministic recovery policy, ML risk scores,
   |  append-only Digital Twin event log, role-based X-API-Key auth
   v
 PostgreSQL / SQLite
```

The backend is the single source of truth. Every number shown in the UI
(risk score, safe-to-release probability, decision, timeline events,
aggregate stats) comes from an API response; if the backend returns nothing,
the UI shows nothing rather than a placeholder statistic.

## Prerequisites

- Node.js 20+ and npm
- The FastAPI backend running (default `http://localhost:8000`, see the
  root `README.md` section 5)

## Getting started

```bash
npm install
cp .env.example .env     # point VITE_API_BASE_URL at the FastAPI backend
npm run dev              # Vite dev server on http://localhost:5173
```

## Scripts

| Script | What it does |
|---|---|
| `npm run dev` | Vite dev server (http://localhost:5173) |
| `npm run build` | Typecheck (`tsc -b`) + production build into `dist/` |
| `npm run preview` | Serve the production build locally |
| `npm run test` | Vitest suite (jsdom), one-shot |
| `npm run test:watch` | Vitest in watch mode |
| `npm run typecheck` | `tsc --noEmit` |

Tests live in `src/__tests__/`.

## Environment variables

| Variable | Default | Meaning |
|---|---|---|
| `VITE_API_BASE_URL` | `http://localhost:8000/api/v1` | Base URL of the FastAPI backend. |

> **`VITE_` variables are public.** Vite inlines anything prefixed `VITE_`
> into the JavaScript bundle served to every browser. **Never put a secret**
> (API key of real value, provider key, database URL) in a `VITE_` variable.
> The only frontend-configurable value here is a URL.

## Routes

| Path | Page | Notes |
|---|---|---|
| `/login` | API key entry | Validates the key via `GET /auth/me`, stores it in `sessionStorage` |
| `/dashboard` | Aggregate stats | `GET /stats/summary` |
| `/transactions` | Transaction list / lookup | `GET /transactions/{id}` |
| `/transactions/:transactionId` | Transaction detail + timeline + actions | `GET /transactions/{id}`, `GET /transactions/{id}/timeline`, release-limit and explanation actions per role |

## Authentication flow

1. The user enters an API key on `/login`.
2. The app calls `GET /api/v1/auth/me` with that key as the `X-API-Key`
   header. Failure (`401`) keeps the user on `/login`.
3. On success the key (and the returned role) are stored in
   **`sessionStorage`** — cleared when the browser tab closes.
4. Every subsequent axios request attaches `X-API-Key` automatically.
5. The stored role drives **UI visibility only** (which buttons/nav items
   render). **The backend enforces authorization** — hiding a button in the
   UI is convenience, not security. A `403` from the backend is always
   surfaced to the user.

## Role matrix (what each role sees)

| Role | Dev key (local dev only) | Dashboard stats | Transaction/timeline reads | Release-limit button | Explanation generation |
|---|---|---|---|---|---|
| `SYSTEM` | `dev-system-key` | yes | yes | yes | yes |
| `ADMIN` | `dev-admin-key` | yes | yes | yes | yes |
| `SUPPORT` | `dev-support-key` | yes | yes | no (hidden) | yes |
| `CUSTOMER` | `dev-customer-key` | limited probe via `/auth/me` | no — reads are backend-restricted until identity is user-bound | no | no |

Again: this table describes the UI. The backend independently rejects any
call the role is not allowed to make (`403`), regardless of what the UI
renders. The `dev-*` keys are insecure placeholders for local development —
the backend refuses them in `ENVIRONMENT=production`.

## API endpoints used

| Endpoint | Method | Used by |
|---|---|---|
| `/health` | GET | Backend availability check (no auth) |
| `/api/v1/auth/me` | GET | Login role probe |
| `/api/v1/stats/summary` | GET | Dashboard aggregates |
| `/api/v1/transactions/{id}` | GET | Transaction detail |
| `/api/v1/transactions/{id}/timeline` | GET | Digital Twin timeline view |
| `/api/v1/recovery/release-limit` | POST | SYSTEM/ADMIN action button |
| `/api/v1/explanations/transaction` | POST | SYSTEM/ADMIN/SUPPORT; body `{transaction_id, language: "bn"\|"en", audience: "customer"\|"support"\|"system"}` |

## No fake data principle

The frontend contains **no hardcoded transactions, statistics, scores, or
timelines**. Every displayed value originates from a backend response; empty
or loading states are rendered explicitly. This mirrors the project-wide
honesty stance: the backend is authoritative, and the UI would rather show
"nothing" than invent something.

## Docker

A multi-stage `Dockerfile` builds the SPA (`node:20-alpine`) and serves
`dist/` via `nginx:alpine` with gzip, SPA fallback (`try_files ... /index.html`),
no caching for `index.html`, and long-lived caching for hashed assets. The
API base URL is supplied at **build time** via the `VITE_API_BASE_URL` build
arg (the root `docker-compose.yml` sets it). No secrets are baked into the
image.

> **Honest status:** Docker is not installed on the machine this project was
> built on, so the frontend image build and `docker compose up` were **not
> executed here** — the Dockerfile and `nginx.conf` are provided untested,
> matching the root README's Docker note. The npm dev-server path is the
> verified way to run the frontend.

## Troubleshooting

- **Requests fail with a CORS error** — the backend's `CORS_ORIGINS` must
  include `http://localhost:5173` (the Vite dev server origin). Set it in the
  backend `.env` and restart the API.
- **"Network error" / nothing loads** — the backend is probably not running.
  Start it (`uvicorn api.main:app --reload`, root README section 5) and check
  `GET http://localhost:8000/health` first.
- **401 after login worked before** — the key lives in `sessionStorage`, so it
  is gone in a new tab; log in again.
- **403 on an action** — your role is not allowed that action; this is the
  backend enforcing authorization, working as designed.
- **Build errors about missing env var** — `VITE_API_BASE_URL` defaults are
  compiled in; copy `.env.example` to `.env` or pass the build arg when using
  Docker.
