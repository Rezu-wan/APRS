# AI-Powered Payment Failure Recovery & Digital Twin System

End-to-end pipeline for detecting failed payment transactions, scoring
recovery risk with ML, deciding — via a **deterministic policy** — whether to
auto-release the customer's limit, and recording every state change in an
**append-only Digital Twin event log**.

Built in five completed stages (dataset, ML engine, API, GenAI explanations,
React frontend).

> **Honest status / limitations**
> - **No real payment provider is connected.** `PaymentProvider` is a named
>   future seam; today transactions enter through the REST API, not a gateway.
> - **All ML models are trained on 100% synthetic data**
>   (`data/transactions.csv`, 25k rows, produced by Stage 1). Scores are
>   realistic in shape, not calibrated to real-world payment behavior.
> - **The dev API keys shipped as defaults are for local development only.**
>   The application logs a warning when they are in use and *refuses to start*
>   in `ENVIRONMENT=production` with them.
> - **`docker compose` could not be run on this machine** (Docker is not
>   installed here). The compose file — including the Stage 5 `frontend`
>   service — is provided as-is and untested locally; the SQLite path and the
>   frontend npm dev-server path have been verified end-to-end.

---

## 1. Architecture overview

```
 Stage 1               Stage 2                    Stage 3
+----------------+    +------------------+    +---------------------------+
| scripts/       |    | ml/              |    | FastAPI backend (api/)    |
| generate_      | -->| preprocess.py    | -->|  routes -> services       |
| dataset.py     |    | train.py         |    |   |            |          |
+----------------+    | evaluate.py      |    |   v            v          |
                      | predict.py       |    | recovery      digital     |
 data/transactions.csv|  -> models/*.joblib   | policy        twin event  |
 (25k synthetic rows) +------------------+    | (deterministic) log (append|
                                              |                only)       |
                                              +------------|--|------------+
                                                           |  |
                                     Stage 4: GenAI    Stage 5: React
                                     (EXPLAIN only)    frontend (frontend/)
                                                       thin client, no
                                                       authority of its own
```

Flow: the **generator** produces the synthetic dataset; the **ML engine**
trains XGBoost models (`models/*.joblib`) and exposes `predict_transaction()`;
the **FastAPI backend** ingests transaction events, advances each transaction
through a state machine (`INITIATED -> PROCESSING -> SUCCESS | FAILED/STALLED
-> RISK_ASSESSED -> RECOVERY_PENDING -> LIMIT_RELEASED | MANUAL_REVIEW |
RECOVERY_REJECTED`), runs ML assessment on failure, applies the recovery
policy, and atomically persists state + Digital Twin events. **GenAI (Stage 4)
sits strictly AFTER the decision** — it only ever *explains* decisions, never
makes them. The **React frontend (Stage 5)** is a thin client over the API:
it renders what the backend returns and submits actions for the backend to
authorize and execute — it holds no state machine, no policy, and no data of
its own.

Key files:

| Path | Purpose |
|---|---|
| `scripts/generate_dataset.py` | Stage 1 synthetic data generator |
| `ml/preprocess.py` / `train.py` / `evaluate.py` / `predict.py` | Stage 2 ML engine |
| `api/main.py` | FastAPI entrypoint (`api.main:app`) |
| `api/core/state_machine.py` | Single authority for legal transitions |
| `api/services/recovery_policy.py` | Deterministic decision policy |
| `api/services/recovery_service.py` | Idempotent recovery operations |
| `api/services/transaction_service.py` | Ingestion + assessment chaining |
| `api/services/digital_twin.py` | Append-only event log |
| `api/services/ml_service.py` | Bridge to the Stage 2 models |
| `api/services/ai/` | Stage 4 GenAI layer: `base.py` (AIProvider), `openai_provider.py`, `mock_provider.py`, `prompts.py`, `fallback.py`, `schemas.py` |
| `api/db/models.py`, `api/db/migrations/` | SQLAlchemy models + Alembic (`ai_explanations` table added in Stage 4) |
| `frontend/` | Stage 5 React SPA (Vite + TS + Tailwind); thin client over the API — see section 15 and `frontend/README.md` |
| `api/services/payment_provider.py` | Stage 8 mock sandbox provider (`MockPaymentProvider`, in-memory ledger, idempotent replay) |
| `api/services/recovery_executor.py` | Stage 8 idempotent executor: safety gate → provider → verification, bounded retries |
| `api/services/recovery_safety.py` | Stage 8 fresh-evidence safety gate (incl. new-settlement race protection) |
| `api/services/recovery_decision_policy.py` | Stage 8 deterministic recovery decision policy |
| `api/services/recovery_verifier.py` | Stage 8 post-execution verification (never `VERIFIED` without passing) |
| `api/routes/autonomous_recovery.py` | Stage 8 `recovery/process` / `recovery/evaluate` / `recovery` endpoints |
| `scripts/stage8_e2e.py` | Stage 8 live-API E2E verification (section 18) |
| `api/middleware.py` | Stage 9 security middleware: request IDs, security headers, rate limiting |
| `api/core/request_context.py` | Stage 9 request-context propagation (`X-Request-ID` end to end) |
| `api/services/audit.py` | Stage 9 `security_audit` trail (key names, never secrets; best-effort writes) |
| `api/routes/sandbox.py` | Stage 9 sandbox reset endpoint (SYSTEM/ADMIN only) |
| `scripts/seed_demo.py` | Stage 9 deterministic demo seeder (DEMO-S1..S6, idempotent re-runs) |
| `scripts/stage9_e2e.py` | Stage 9 live-API E2E verification: security flow T1-T13 incl. secret-leakage sweep |

## 2. Environment setup

Windows 11 / Python 3.12:

```bash
python -m venv .venv
.venv\Scripts\activate          # bash: source .venv/Scripts/activate
pip install -r requirements.txt

# configure the app
copy .env.example .env          # bash: cp .env.example .env
# then edit .env (see section 4)
```

## 3. Database

**Production / realistic local setup — PostgreSQL:**

```
DATABASE_URL=postgresql+psycopg://payment_app:CHANGE_ME@localhost:5432/payment_recovery
```

(uses the `psycopg` 3 driver, pinned in `requirements.txt`).

**Local development fallback — SQLite** (the default in `.env.example`):

```
DATABASE_URL=sqlite:///./data/app.db
```

Then apply migrations (required either way):

```bash
python -m alembic upgrade head
```

## 4. Environment variables

All variables are read by `api/core/config.py` from the environment or a
`.env` file at the project root (template: `.env.example`).

| Variable | Default | Meaning |
|---|---|---|
| `ENVIRONMENT` | `development` | `development` \| `test` \| `production`. Production refuses dev API keys. |
| `LOG_LEVEL` | `INFO` | Logging level. |
| `CORS_ORIGINS` | *(empty)* | Comma-separated browser origins; empty = no browser access. |
| `DATABASE_URL` | `sqlite:///./data/app.db` | SQLAlchemy URL (see section 3). |
| `API_KEY_SYSTEM` | `dev-system-key` | API key mapped to role `SYSTEM`. |
| `API_KEY_ADMIN` | `dev-admin-key` | API key mapped to role `ADMIN`. |
| `API_KEY_SUPPORT` | `dev-support-key` | API key mapped to role `SUPPORT`. |
| `API_KEY_CUSTOMER` | `dev-customer-key` | API key mapped to role `CUSTOMER`. |
| `RECOVERY_MIN_SAFE_PROBABILITY` | `0.90` | Policy: minimum `safe_to_release_probability` for auto-release. |
| `RECOVERY_MAX_AMOUNT` | `1500` | Policy: max amount eligible for auto-release. |
| `RECOVERY_MAX_PREVIOUS_FAILURES` | `3` | Policy: max prior failures eligible for auto-release. |
| `MODEL_DIR` | `models` | Directory containing the Stage 2 joblib artifacts. |
| `AI_PROVIDER` | `mock` | Stage 4 explanation provider: `mock` \| `openai`. |
| `OPENAI_API_KEY` | *(empty)* | OpenAI key — server-side only, never sent to any client. |
| `OPENAI_MODEL` | `gpt-4o-mini` | OpenAI model used when `AI_PROVIDER=openai`. |
| `AI_TIMEOUT_SECONDS` | `12` | Hard cap on provider calls so an explanation can never hang a request. |

Generate real keys for anything beyond local dev:

```bash
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

## 5. Running the API

From the project root:

```bash
uvicorn api.main:app --reload
```

Interactive docs: **http://127.0.0.1:8000/docs** (Swagger UI) and
**http://127.0.0.1:8000/redoc**.

On startup the app loads the ML artifacts **exactly once** (FastAPI lifespan —
no retraining, no data generation), checks the DB, and warns if dev keys are
in use. Verify with `GET /health`.

## 6. GenAI explanation layer (Stage 4)

### 6.1 Architecture — explanation ONLY

GenAI sits **strictly after the decision**. It receives the already-stored
recovery decision and produces a human-readable justification for it. It has
**no code path** to release limits, change transaction state, or modify the
recovery policy, and explanation generation never runs inside the
transaction/recovery path — it is strictly on-request afterwards.

```
Transaction -> ML Engine -> Recovery Policy -> Recovery Decision
                                    |
                                    v
                    Digital Twin / DB (stored decision)
                                    |
                                    v
                     GenAI Explanation (Stage 4, on request)
```

Concretely: the backend builds a schema-controlled `ExplanationContext` from
the stored transaction state + decision, renders a versioned prompt
(`PROMPT_VERSION="v1"`), sends it to the configured provider, validates the
response, and stores it in the derived `ai_explanations` table. That table is a
**cache, not an authority** — the authoritative record of what happened remains
the transaction state and the append-only Digital Twin log.

### 6.2 Provider configuration

Providers are selected by name via `AI_PROVIDER` and produced by the
`get_ai_provider()` factory in `api/services/ai/`:

- `mock` (default) — a deterministic local provider plus a failing variant for
  tests. Needs no key, so everything is reproducible offline.
- `openai` — the real provider via the pinned OpenAI SDK.

Because all providers implement the same `AIProvider` interface
(`api/services/ai/base.py`), adding Gemini (or any other LLM) means adding one
new provider class + factory branch — **without touching the recovery service,
the policy, or any decision path**.

### 6.3 Environment variables

| Variable | Default | Meaning |
|---|---|---|
| `AI_PROVIDER` | `mock` | `mock` \| `openai`. |
| `OPENAI_API_KEY` | *(empty)* | OpenAI secret key. Server-side only — the frontend never sees it. |
| `OPENAI_MODEL` | `gpt-4o-mini` | Model used when `AI_PROVIDER=openai`. |
| `AI_TIMEOUT_SECONDS` | `12` | Hard timeout on the provider call; on timeout the fallback template is used. |

### 6.4 Setting up OpenAI (optional)

The default `AI_PROVIDER=mock` needs **no key and no network** — the endpoint
works fully deterministically out of the box. To exercise the live OpenAI
path:

1. `pip install -r requirements.txt` (pins `openai==3.23.0`).
2. Get an API key from https://platform.openai.com/api-keys.
3. In `.env`, set:

   ```
   AI_PROVIDER=openai
   OPENAI_API_KEY=sk-...
   ```

4. Restart the API. **Honest status:** the OpenAI integration is fully wired
   (provider, prompts, validation, fallback), but live calls require *your*
   key and were not exercised during development — the mock provider is the
   verified path.

### 6.5 API endpoint

`POST /api/v1/explanations/transaction` — roles `SYSTEM`, `ADMIN`, `SUPPORT`
(`CUSTOMER` excluded for the same IDOR reason as reads: no user-bound identity
exists yet, so an open customer role could fetch other customers'
explanations).

```bash
curl -X POST http://127.0.0.1:8000/api/v1/explanations/transaction \
  -H "X-API-Key: dev-support-key" -H "Content-Type: application/json" \
  -d '{"transaction_id": "TXN-123456", "language": "bn", "audience": "customer"}'
```

Response `200`:

```json
{
  "transaction_id": "TXN-123456",
  "language": "bn",
  "audience": "customer",
  "explanation": "আপনার পেমেন্টটি নির্ধারিত সময়ের মধ্যে গেটওয়ে সাড়া না পাওয়ায় সম্পন্ন হয়নি। সিস্টেম লেনদেনটি পর্যালোচনা করে পুনরুদ্ধারের জন্য নিরাপদ বলে নিশ্চিত হয়েছে। তাই আপনার সাময়িকভাবে আটকে থাকা লিমিট পুনরায় চালু করা হয়েছে।",
  "provider": "mock",
  "model": "mock",
  "prompt_version": "v1",
  "is_fallback": false,
  "cached": false,
  "generated_at": "2026-10-02T10:25:00Z"
}
```

**Caching:** the `ai_explanations` table stores generated explanations keyed by
(transaction state, decision, language, audience, `prompt_version`). An
identical request replays the stored explanation with `cached: true` — no new
provider call, no new cost, and the answer stays consistent.

### 6.6 Support-audience example (English)

```bash
curl -X POST http://127.0.0.1:8000/api/v1/explanations/transaction \
  -H "X-API-Key: dev-admin-key" -H "Content-Type: application/json" \
  -d '{"transaction_id": "TXN-123456", "language": "en", "audience": "support"}'
```

```json
{
  "transaction_id": "TXN-123456",
  "language": "en",
  "audience": "support",
  "explanation": "Payment timed out at the gateway and failed after 3 retries. The ML assessment scored risk at 0.21 with a 94% probability that release is safe, so the deterministic policy auto-released the held limit (decision: LIMIT_RELEASED). No manual action is required; the customer has been notified.",
  "provider": "mock",
  "model": "mock",
  "prompt_version": "v1",
  "is_fallback": false,
  "cached": false,
  "generated_at": "2026-10-02T10:27:00Z"
}
```

The support/system audiences receive the full context (risk score,
probabilities, timeline); the `customer` audience gets a **stripped payload**
with all of those removed — see 6.8.

### 6.7 Fallback behavior

The endpoint **never fails because of the AI provider**. Any provider failure —
timeout, rate limit, bad key, malformed output, empty response, or provider not
configured — falls back to a **deterministic template explanation** built from
the backend's own stored data. The response is then marked `is_fallback: true`
(and stored in the cache like any other, so replays stay stable). Fallback
templates never fabricate facts: they can only restate the recorded decision,
the reason, and pre-formatted amounts — nothing else.

### 6.8 Security model

- **API key stays server-side.** `OPENAI_API_KEY` is read only by the backend;
  it is never returned by any endpoint and never reaches the frontend.
- **Controlled context.** The LLM receives only the schema-controlled
  `ExplanationContext` — never raw request bodies, never database dumps.
- **Audience-filtered payloads.** The `customer` audience gets a stripped
  context: no `risk_score`, no probabilities, no timeline.
- **Pre-formatted numbers.** Amounts, probabilities and scores are formatted by
  the backend before prompting, so the LLM cannot mangle them.
- **No invention.** Prompts explicitly forbid inventing facts beyond the
  provided context.
- **Response validation.** Provider output is validated (including length
  caps) before it is returned or cached; invalid output triggers the fallback.

### 6.9 Why GenAI cannot make recovery decisions

This is a design invariant, not a convention:

- **The decision is deterministic and auditable.** `LIMIT_RELEASED` /
  `MANUAL_REVIEW` / `RECOVERY_REJECTED` come from the pure policy function in
  `api/services/recovery_policy.py` (section 10), with a `policy_snapshot`
  stored on every decision.
- **LLM output is non-deterministic.** The same prompt can yield different
  wording on different calls; a financial action must be reproducible byte for
  byte in an audit trail. An explanation can vary — a release limit cannot.
- **GenAI receives the decision, it does not produce one.** The explanation
  prompt contains the already-stored decision and its recorded reason.
- **There is no code path.** The GenAI layer has no access to state-machine
  transitions, recovery operations, or policy parameters. It cannot release a
  limit, revert a state, or influence a future decision — not by prompt
  injection, not by malformed output, not by configuration.

## 7. Authentication

Every endpoint except `/health` requires the `X-API-Key` header. Each key maps
to exactly one role; roles come from the environment (section 4).

| Role | Dev key (local only) | May call |
|---|---|---|
| `SYSTEM` | `dev-system-key` | ingest events, recovery, read transactions/timelines |
| `ADMIN` | `dev-admin-key` | ingest events, recovery, read transactions/timelines |
| `SUPPORT` | `dev-support-key` | read transactions/timelines, request explanations (section 6) |
| `CUSTOMER` | `dev-customer-key` | nothing yet — reads are excluded until identity is user-bound (no ownership scoping exists; unscoped reads would leak other customers' data) |

Missing or unknown key -> `401`; valid key, wrong role -> `403`:

```json
{"error": {"code": "FORBIDDEN", "message": "role SUPPORT may not perform this action (allowed: SYSTEM, ADMIN)"}}
```

> **Warning:** the `dev-*` keys are insecure defaults for local development.
> The app logs a warning when they are active and **refuses to start** when
> `ENVIRONMENT=production`. In production, set all four `API_KEY_*` variables
> to generated secrets (`secrets.token_urlsafe(32)`). The auth layer is a
> deliberate foundation: swapping in JWT/OAuth2 later means changing only
> `get_auth_context`, not the routes.

## 8. API reference

Base URL: `http://127.0.0.1:8000`. Error shape everywhere:
`{"error": {"code": "...", "message": "..."}}` (400 invalid transition,
401 auth, 403 role, 404 not found, 409 conflict, 422 validation, 503 ML not
loaded).

### 8.1 `POST /api/v1/transaction/event` — ingest an event (SYSTEM, ADMIN)

Advances the transaction through the state machine (one Digital Twin event per
hop), runs the ML assessment when the outcome is `FAILED` (a `STALLED`
transaction rests in `STALLED` — it may resolve via a `PROCESSING` retry
event, or an explicit recovery request assesses it on demand), and
commits state + events atomically.

```bash
curl -X POST http://127.0.0.1:8000/api/v1/transaction/event \
  -H "X-API-Key: dev-system-key" -H "Content-Type: application/json" \
  -d '{
    "transaction_id": "TXN-123456",
    "user_id": "USER-001",
    "merchant_id": "MERCHANT-001",
    "amount": 1250.00,
    "currency": "BDT",
    "gateway_latency_ms": 2800,
    "retry_count": 3,
    "network_quality": "Good",
    "previous_failures": 2,
    "account_age_days": 450,
    "status": "FAILED",
    "failure_reason": "Timeout"
  }'
```

Response `200` — outcome `FAILED` chains `FAILED -> RISK_ASSESSED ->
RECOVERY_PENDING` with an ML assessment attached:

```json
{
  "transaction_id": "TXN-123456",
  "current_state": "RECOVERY_PENDING",
  "created": true,
  "state_changed": true,
  "ml_assessment": {
    "failure_prediction": "Timeout",
    "failure_probability": 0.83,
    "failure_probabilities": {"Timeout": 0.83, "Gateway Error": 0.09, "Network Drop": 0.05, "Merchant Disconnect": 0.02, "Insufficient Balance": 0.01},
    "risk_score": 0.41,
    "safe_to_release_probability": 0.94,
    "safe_to_release": true
  },
  "new_events": [
    {"event_type": "TRANSACTION_CREATED", "previous_state": null, "new_state": "INITIATED"},
    {"event_type": "PAYMENT_PROCESSING", "previous_state": "INITIATED", "new_state": "PROCESSING"},
    {"event_type": "PAYMENT_FAILED", "previous_state": "PROCESSING", "new_state": "FAILED"},
    {"event_type": "ML_RISK_ASSESSED", "previous_state": "FAILED", "new_state": "RISK_ASSESSED"},
    {"event_type": "RECOVERY_CHECKED", "previous_state": "RISK_ASSESSED", "new_state": "RECOVERY_PENDING"}
  ]
}
```

`failure_reason` is required when `status` is `FAILED`; supported values:
`Timeout`, `Merchant Disconnect`, `Insufficient Balance`, `Network Drop`,
`Gateway Error`.

### 8.2 `POST /api/v1/recovery/release-limit` — recovery decision (SYSTEM, ADMIN)

Runs the deterministic policy (section 10). The decision comes from the
policy applied to the ML assessment — never from the caller, never from GenAI.
Idempotent: calling on an already-decided transaction replays the stored
decision (`already_applied: true`) with no side effects.

**Request:**

```bash
curl -X POST http://127.0.0.1:8000/api/v1/recovery/release-limit \
  -H "X-API-Key: dev-admin-key" -H "Content-Type: application/json" \
  -d '{"transaction_id": "TXN-123456"}'
```

**`LIMIT_RELEASED`** — ML says safe and confidence >= 0.90, amount and
previous-failures caps respected:

```json
{
  "transaction_id": "TXN-123456",
  "decision": "LIMIT_RELEASED",
  "safe_to_release": true,
  "safe_to_release_probability": 0.94,
  "risk_score": 0.41,
  "reason": "Recovery conditions satisfied",
  "decided_by": "ADMIN",
  "decided_at": "2026-10-02T10:15:30.123456Z",
  "current_state": "LIMIT_RELEASED",
  "already_applied": false
}
```

**`RECOVERY_REJECTED`** — a hard business rule fired first (here: amount above
the cap):

```json
{
  "transaction_id": "TXN-999999",
  "decision": "RECOVERY_REJECTED",
  "safe_to_release": false,
  "safe_to_release_probability": 0.91,
  "risk_score": 0.38,
  "reason": "amount 2500.00 exceeds auto-release cap 1500.0: manual handling required",
  "decided_by": "SYSTEM",
  "decided_at": "2026-10-02T10:18:02.654321Z",
  "current_state": "RECOVERY_REJECTED",
  "already_applied": false
}
```

(`previous_failures` above `3` rejects the same way.)

**`MANUAL_REVIEW`** — neither rejected nor confidently safe:

```json
{
  "transaction_id": "TXN-777777",
  "decision": "MANUAL_REVIEW",
  "safe_to_release": false,
  "safe_to_release_probability": 0.72,
  "risk_score": 0.58,
  "reason": "Recovery conditions not satisfied",
  "decided_by": "SYSTEM",
  "decided_at": "2026-10-02T10:20:11.000111Z",
  "current_state": "MANUAL_REVIEW",
  "already_applied": false
}
```

**Idempotent replay** — same request again after the decision was stored:

```json
{
  "transaction_id": "TXN-123456",
  "decision": "LIMIT_RELEASED",
  "safe_to_release": true,
  "safe_to_release_probability": 0.94,
  "risk_score": 0.41,
  "reason": "Recovery conditions satisfied",
  "decided_by": "ADMIN",
  "decided_at": "2026-10-02T10:15:30.123456Z",
  "current_state": "LIMIT_RELEASED",
  "already_applied": true
}
```

Concurrent duplicates are also safe: the `UNIQUE(transaction_id)` constraint
on recovery decisions is the source of truth, and the race loser replays the
winner's decision. Calling on a non-recoverable transaction (e.g. `SUCCESS`,
still `PROCESSING`) returns `409`.

### 8.3 `GET /api/v1/transactions/{transaction_id}` (all roles)

```bash
curl http://127.0.0.1:8000/api/v1/transactions/TXN-123456 \
  -H "X-API-Key: dev-customer-key"
```

```json
{
  "transaction_id": "TXN-123456",
  "user_id": "USER-001",
  "merchant_id": "MERCHANT-001",
  "amount": "1250.00",
  "currency": "BDT",
  "timestamp": "2026-10-02T10:14:59Z",
  "gateway_latency_ms": 2800,
  "retry_count": 3,
  "network_quality": "Good",
  "previous_failures": 2,
  "account_age_days": 450,
  "failure_reason": "Timeout",
  "current_state": "LIMIT_RELEASED",
  "failure_prediction": "Timeout",
  "failure_probability": 0.83,
  "risk_score": 0.41,
  "safe_to_release_probability": 0.94,
  "safe_to_release": true,
  "created_at": "2026-10-02T10:15:30Z",
  "updated_at": "2026-10-02T10:16:04Z"
}
```

### 8.4 `GET /api/v1/transactions/{transaction_id}/timeline` (all roles)

The Digital Twin view: every state hop, in order.

```bash
curl http://127.0.0.1:8000/api/v1/transactions/TXN-123456/timeline \
  -H "X-API-Key: dev-support-key"
```

```json
{
  "transaction_id": "TXN-123456",
  "current_state": "LIMIT_RELEASED",
  "event_count": 6,
  "events": [
    {"event_id": "evt_01", "event_type": "TRANSACTION_CREATED", "timestamp": "2026-10-02T10:15:30Z", "previous_state": null, "new_state": "INITIATED", "failure_prediction": null, "risk_score": null, "safe_to_release_probability": null, "safe_to_release": null, "reason": "transaction registered", "metadata": null},
    {"event_id": "evt_02", "event_type": "PAYMENT_PROCESSING", "timestamp": "2026-10-02T10:15:30Z", "previous_state": "INITIATED", "new_state": "PROCESSING", "failure_prediction": null, "risk_score": null, "safe_to_release_probability": null, "safe_to_release": null, "reason": null, "metadata": null},
    {"event_id": "evt_03", "event_type": "PAYMENT_FAILED", "timestamp": "2026-10-02T10:15:30Z", "previous_state": "PROCESSING", "new_state": "FAILED", "failure_prediction": null, "risk_score": null, "safe_to_release_probability": null, "safe_to_release": null, "reason": null, "metadata": null},
    {"event_id": "evt_04", "event_type": "ML_RISK_ASSESSED", "timestamp": "2026-10-02T10:15:30Z", "previous_state": "FAILED", "new_state": "RISK_ASSESSED", "failure_prediction": "Timeout", "risk_score": 0.41, "safe_to_release_probability": 0.94, "safe_to_release": true, "reason": "ML risk assessment completed", "metadata": {"policy": {"min_safe_probability": 0.9, "max_amount": 1500.0, "max_previous_failures": 3}}},
    {"event_id": "evt_05", "event_type": "RECOVERY_CHECKED", "timestamp": "2026-10-02T10:15:30Z", "previous_state": "RISK_ASSESSED", "new_state": "RECOVERY_PENDING", "failure_prediction": null, "risk_score": null, "safe_to_release_probability": null, "safe_to_release": null, "reason": "awaiting recovery decision", "metadata": null},
    {"event_id": "evt_06", "event_type": "LIMIT_RELEASED", "timestamp": "2026-10-02T10:16:04Z", "previous_state": "RECOVERY_PENDING", "new_state": "LIMIT_RELEASED", "failure_prediction": null, "risk_score": 0.41, "safe_to_release_probability": 0.94, "safe_to_release": true, "reason": "Recovery conditions satisfied", "metadata": {"decided_by": "ADMIN", "policy": {"min_safe_probability": 0.9, "max_amount": 1500.0, "max_previous_failures": 3}}}
  ]
}
```

### 8.5 `GET /health` — no auth

```bash
curl http://127.0.0.1:8000/health
```

Healthy:

```json
{"status": "healthy", "database": "connected", "ml_models": "loaded", "environment": "development"}
```

Degraded (DB down or models not loaded) returns HTTP `503` with
`"status": "degraded"`.

## 9. ML integration

- **Models are loaded ONCE at startup** (FastAPI lifespan in `api/main.py`
  calls `MLService.load()`, which reads `models/*.joblib` via
  `ml.predict.load_models()`). Nothing is retrained and no synthetic data is
  generated at request time or startup.
- `predict_transaction()` is **adapted, not rewritten**: it returns the
  recovery-risk output under the Stage 2 key `recovery_risk`; the API layer
  exposes the same value as **`risk_score`**. That single documented mapping
  happens only in `api/services/ml_service.py`.
- **Stage 2 leakage rule preserved by construction:** the models never receive
  `risk_score` or `safe_to_release` as inputs — only raw transaction
  attributes (amount, latency, retries, network quality, previous failures,
  account age) plus the observed status/failure_reason. These targets are
  outputs of the models, never features.
- Unknown categorical values and missing optional fields are safe: Stage 2
  preprocessing uses `OneHotEncoder(handle_unknown='ignore')` plus imputers,
  so odd client input cannot crash the API.

## 10. Recovery workflow

The decision is a **pure, auditable function** of (ML assessment, transaction
attributes, policy parameters) in `api/services/recovery_policy.py`. First
match wins:

1. **Hard business rules -> `RECOVERY_REJECTED`**: amount >
   `RECOVERY_MAX_AMOUNT` (1500) or `previous_failures` >
   `RECOVERY_MAX_PREVIOUS_FAILURES` (3).
2. **ML safe AND `safe_to_release_probability` >= `RECOVERY_MIN_SAFE_PROBABILITY`
   (0.90) -> `LIMIT_RELEASED`.**
3. **Everything else -> `MANUAL_REVIEW`.**

The policy is deliberately **separate from the model**: thresholds live in the
environment, so business rules change by editing `.env` and restarting — no
retraining. Each stored decision also embeds a `policy_snapshot` of the exact
parameters in force, so past decisions stay auditable after thresholds change.

**GenAI will only ever EXPLAIN decisions, never make them.** The decision
comes from the policy alone; Stage 4 (section 6) produces human-readable
justifications for the decisions this system already recorded.

## 11. Digital Twin

- The event log is **append-only**: one `digital_twin_events` row per state
  hop, never updated or deleted.
- State and events are **committed atomically in one database transaction** —
  a transaction is never seen in a state without its events, and vice versa.
- Each event records `event_type`, `previous_state`, `new_state`, the ML
  assessment (on `ML_RISK_ASSESSED`), the reason, and metadata (e.g. who
  decided, the policy snapshot). See the timeline example in section 8.4 for a
  failed-then-released transaction:
  `INITIATED -> PROCESSING -> FAILED -> RISK_ASSESSED -> RECOVERY_PENDING ->
  LIMIT_RELEASED`.

## 12. Docker

```bash
docker compose up --build
```

Provide the required environment variables (compose reads them from the
shell or a `.env` at the project root), at minimum:

```
POSTGRES_PASSWORD=CHANGE_ME
DATABASE_URL=postgresql+psycopg://payment_app:${POSTGRES_PASSWORD}@db:5432/payment_recovery
API_KEY_SYSTEM=... API_KEY_ADMIN=... API_KEY_SUPPORT=... API_KEY_CUSTOMER=...
```

> **Honesty note:** Docker is not installed on the machine this project was
> built on, so `docker compose up --build` was **not executed here**. The
> SQLite setup (section 3) is the verified path. Apply migrations inside the
> container on first run: `docker compose exec api python -m alembic upgrade head`.
> The Stage 5 `frontend` service (section 15) is included in the same compose
> file and shares this untested-locally status.

## 13. Tests

```bash
python -m pytest tests/ -q
```

Tests cover the state machine, the recovery policy table, idempotent replay,
auth/role enforcement, and the API endpoints against the SQLite dev database.

## 14. Stage reference (ML engine, standalone)

Stages 1–2 also run standalone:

```bash
python scripts/generate_dataset.py        # data/transactions.csv (25k rows)
python -m ml.train                        # models/*.joblib + reports/
python -m ml.evaluate                     # reports/ metrics and plots
```

## 15. React frontend (Stage 5)

`frontend/` is a thin-client single-page app over the API: React 18 + Vite +
TypeScript + Tailwind, `react-router-dom` for routing, TanStack Query for
fetching/caching, axios for transport, zod for response validation,
`lucide-react` for icons. Full details in `frontend/README.md`.

### 15.1 Architecture summary

```
 Browser -> React SPA (frontend/) --X-API-Key header--> FastAPI backend
              renders only what the        (authoritative: state machine,
              backend returns; no fake      deterministic policy, ML scores,
              data, ever                    Digital Twin log, role checks)
```

The frontend holds **no authority**: it cannot decide recovery outcomes,
cannot fabricate statistics, and its role checks are UI visibility only —
the backend independently enforces authorization on every request (`403`).

### 15.2 Prerequisites

- Node.js 20+ and npm (frontend)
- Python 3.12 environment set up per section 2 (backend)

### 15.3 Running (two terminals)

```bash
# Terminal 1 — backend (section 5)
uvicorn api.main:app --reload

# Terminal 2 — frontend
cd frontend
npm install
cp .env.example .env          # VITE_API_BASE_URL=http://localhost:8000/api/v1
npm run dev                   # http://localhost:5173
```

CORS: the backend's `CORS_ORIGINS` must include `http://localhost:5173` (the
Vite dev origin) or the browser will block every request — see section 4.

### 15.4 Environment variables (frontend)

| Variable | Default | Meaning |
|---|---|---|
| `VITE_API_BASE_URL` | `http://localhost:8000/api/v1` | Backend base URL the browser calls. |

> **`VITE_` variables are public** — Vite inlines them into the served JS
> bundle. Never put a secret in one. Real API keys stay in the backend
> `.env` and never reach the frontend.

### 15.5 Authentication

The frontend uses the backend's `X-API-Key` auth (section 7). The user enters
a key on `/login`; it is validated via `GET /api/v1/auth/me` and stored in
`sessionStorage` (cleared when the tab closes). Every axios request attaches
the header afterwards. For local development the documented `dev-*` keys
(section 7) work; the same standing warning applies — they are insecure
placeholders and rejected in `ENVIRONMENT=production`.

| Role | Sees in the UI |
|---|---|
| `SYSTEM` / `ADMIN` | dashboard stats, transaction/timeline views, release-limit action, explanation generation |
| `SUPPORT` | dashboard stats, transaction/timeline views, explanation generation (no release-limit button) |
| `CUSTOMER` | login probe only — transaction reads are backend-restricted until identity is user-bound |

Frontend role checks are **UI visibility only; the backend enforces
authorization**.

### 15.6 API integration

| Endpoint | Method | Purpose in the UI |
|---|---|---|
| `/health` | GET | backend availability check (no auth) |
| `/api/v1/auth/me` | GET | login role probe |
| `/api/v1/stats/summary` | GET | dashboard aggregates (honest, computed from stored data) |
| `/api/v1/transactions/{id}` | GET | transaction detail |
| `/api/v1/transactions/{id}/timeline` | GET | Digital Twin timeline view |
| `/api/v1/recovery/release-limit` | POST | SYSTEM/ADMIN action |
| `/api/v1/explanations/transaction` | POST | SYSTEM/ADMIN/SUPPORT; body `{transaction_id, language: "bn"\|"en", audience: "customer"\|"support"\|"system"}` |

### 15.7 Available routes

| Path | Page |
|---|---|
| `/login` | API key entry |
| `/dashboard` | Aggregate stats |
| `/transactions` | Transaction list / lookup |
| `/transactions/:transactionId` | Detail + timeline + role-gated actions |

### 15.8 Production build

```bash
cd frontend
npm run build     # typecheck + bundle -> frontend/dist/
npm run preview   # serve the build locally
```

### 15.9 Docker

A multi-stage `frontend/Dockerfile` builds the SPA (`node:20-alpine`,
`npm ci` + `npm run build`, with `VITE_API_BASE_URL` as a **build arg**) and
serves `dist/` via `nginx:alpine` (gzip, SPA fallback via
`try_files ... /index.html`, no cache for `index.html`, long cache for hashed
`assets/`). The root compose file adds a `frontend` service on port `5173:80`
with `depends_on: api`. No secrets are baked into the image.

> **Honesty note:** Docker is not installed on this machine, so the frontend
> image build was **not executed here** (see section 12). The npm
> dev-server path in 15.3 is the verified way to run the frontend.

### 15.10 No fabricated statistics

The frontend renders **only** what the backend returns — there are no
hardcoded transactions, scores, timelines, or summary numbers anywhere in the
client, and loading/empty/error states are shown explicitly. The backend
remains the single authority for every fact the UI displays.

---

## 16. Payment event reconstruction engine (Stage 6)

Stage 6 adds an **evidence layer** beneath the recovery system: it reconstructs
*what actually happened* inside a payment flow and identifies — deterministically,
from stored evidence — where the flow stalled or failed.

> **Scope boundary (important):** Stage 6 answers **"What happened?"** only.
> It never releases limits, never changes transaction state, and never makes
> recovery decisions. Those remain the Stage 3 policy's exclusive authority.
> Anomaly/fraud classification ("Is this suspicious?") is a later stage.

### 16.1 Architecture

```
Payment Events (payment_events table)
      ↓
Event Ordering (by event_timestamp — NOT insertion order)
      ↓
Lifecycle Reconstruction (deterministic, pure function)
      ↓
Evidence Extraction (per-stage statuses + missing events)
      ↓
Root Cause Identification (first-match evidence rules)
      ↓
Digital Twin (ROOT_CAUSE_IDENTIFIED observation, append-only)
      ↓
GenAI Explanation (Stage 4 consumes evidence as authoritative context)
```

### 16.2 Payment event model

Fine-grained payment-domain events are stored in the `payment_events` table
(migration `56a442a6e5b1`), separate from the transaction-state Digital Twin:

| Field | Purpose |
|---|---|
| `provider_event_id` | UNIQUE — idempotency anchor; real payment systems redeliver events, replays are safe |
| `event_type` | 14-type vocabulary (`api/core/payment_lifecycle.py`): customer debit / gateway / merchant confirmation / settlement, each with confirmed/failed/timeout/error/not-confirmed outcomes |
| `source` | BANK \| GATEWAY \| MERCHANT \| SETTLEMENT \| SYSTEM |
| `event_timestamp` | domain time — the **authoritative ordering key** (insertion order is never trusted) |
| `status`, `latency_ms`, `reference_id`, `metadata` | evidence details |

Stored events are **observed facts only**. "Not observed" is never stored —
it is *derived* for absent evidence at reconstruction time, so the system
never pretends an event happened.

### 16.3 Reconstruction engine

`api/services/event_reconstruction.py` — a pure, deterministic function.
No LLM, no randomness, no guessing:

- **Per-stage status** from observed terminal events; progress-only stages are
  `OBSERVED`; absent stages are `NOT_OBSERVED` (explicit uncertainty).
- **Root cause** by first-match priority: debit failure → gateway timeout/error →
  merchant timeout/error → settlement failure/not-confirmed → full success (`NONE`)
  → otherwise `INCOMPLETE` (evidence exists but no terminal outcome — reported as
  unknown, never as a guessed failure).
- **Confidence** = deterministic evidence coverage over the 7-event happy path
  (e.g. success 1.0, merchant timeout 0.71, gateway timeout 0.43, debit failure 0.14).
- **Evidence summary**: human-readable English lines per stage + a conclusion.

### 16.4 APIs

| Endpoint | Purpose |
|---|---|
| `POST /api/v1/transactions/{id}/payment-events` | Batch ingestion (SYSTEM/ADMIN). Idempotent on `provider_event_id`; source/status are derived from the vocabulary, not client claims |
| `GET /api/v1/transactions/{id}/reconstruction` | Run the engine (SYSTEM/ADMIN/SUPPORT). Records a `ROOT_CAUSE_IDENTIFIED` Digital Twin observation on first reconstruction per root cause (idempotent — repeats return the same result without appending) |

### 16.5 Payment event simulator

`scripts/payment_event_simulator.py` generates realistic event sequences for
eight scenarios (`success`, `gateway_timeout`, `gateway_error`, `merchant_timeout`,
`merchant_error`, `settlement_failure`, `settlement_not_confirmed`, `debit_failure`):

```bash
# print a deterministic merchant-timeout sequence
py -m scripts.payment_event_simulator --transaction-id TXN-SIM-1 --scenario merchant_timeout --seed 42

# create the transaction, then ingest its events (idempotent — re-run safely)
curl -s -X POST http://127.0.0.1:8000/api/v1/transaction/event -H "Content-Type: application/json" \
  -H "X-API-Key: dev-system-key" -d '{"transaction_id":"TXN-SIM-1","user_id":"U-1","merchant_id":"M-1","amount":1200,"status":"FAILED","failure_reason":"Timeout"}'
py -m scripts.payment_event_simulator --transaction-id TXN-SIM-1 --scenario merchant_timeout --seed 42 --ingest
```

### 16.6 Digital Twin integration

Reconstruction appends an **observation** (not a state transition):
`ROOT_CAUSE_IDENTIFIED` with structured metadata (root_cause, failure_stage,
last_successful_stage, all four stage statuses, missing_events,
reconstruction_version). Append-only, like every twin event; duplicates for an
unchanged root cause are suppressed.

### 16.7 GenAI integration

`ExplanationContext` now optionally carries `ReconstructionEvidence` (root cause,
stage statuses, evidence lines). `PROMPT_VERSION` is now **v2**; the fingerprint
includes the reconstruction, so cached explanations regenerate when the evidence
changes. Customer payloads exclude `missing_events` (internal bookkeeping) but
include the evidence story; support payloads include everything. Deterministic
fallback templates speak the root cause in Bangla and English.

### 16.8 Limitations (honest)

- **Synthetic/sandbox payment environment** — events come from the local
  simulator; there is **no real bank/gateway/settlement integration**. Do not
  interpret stage statuses as real banking facts.
- `INCOMPLETE` reconstructions report unknown outcomes rather than guessing.
- Confidence is evidence coverage, not a probability of correctness.
- Event redelivery beyond the first occurrence is tolerated (deduplicated) but
  duplicates remain visible in `ordered_events` for audit purposes.

---

## 17. Risk & anomaly classification (Stage 7)

Stage 7 adds the **classification layer** above Stage 6's evidence: it answers
**"What does the evidence indicate?"** for a reconstructed payment — one
deterministic anomaly category, a risk score/level, and whether the payment is
a recovery candidate. Like Stage 6, this is **decision evidence, NOT action**:
Stage 7 never releases limits, never changes transaction state, and never
executes recovery — that remains the Stage 3 policy's exclusive authority.

> **Scope boundary:** Stage 7 classifies; it does not act. `recovery_candidate`
> is *routing evidence for a later stage*, not a recovery decision.

### 17.1 Architecture

```
Payment Events → Reconstruction → Deterministic Evidence + ML Anomaly Signal → Hybrid Risk Engine → Risk Assessment → GenAI Explanation
```

### 17.2 Why deterministic rules are the authority

The ML anomaly model can be **unavailable** (not loaded, artifact missing,
inference error). Stage 7 must still assess every transaction in that state:
the deterministic rule engine (`api/services/anomaly_rules.py`, a pure
function, `rule_version "1"`) produces a **complete** assessment on stored
evidence alone (`model_version: "rules-only"`, `ml_anomaly_score: null`).
Degradation loses the ML signal — never the classification. This mirrors the
Stage 3 invariant that decisions must be reproducible and auditable, not
model-dependent.

### 17.3 How ML is used — supporting signal, pinned precedence

The dedicated scenario classifier (`synthetic-v1`, trained by
`ml/train_anomaly.py`, served by `ml/predict_anomaly.py` through
`MLService`) contributes exactly one number — `ml_anomaly_score` — and is
bound by a precedence contract pinned in `api/services/risk_engine.py`:

1. **The deterministic category ALWAYS wins.** ML never rewrites the
   `anomaly_type` the rules established: if the rules say
   `DOUBLE_DEDUCTION`, the classification is `DOUBLE_DEDUCTION` no matter
   what the model scores.
2. **ML may upgrade uncertainty only.** When the rules could not determine
   an outcome (`INCOMPLETE` or `UNKNOWN`) **and** `ml_anomaly_score >= 0.7`,
   the assessment is upgraded to `SUSPICIOUS` (`risk_level HIGH`,
   `recovery_candidate False`) with an explicit `ML_HIGH_ANOMALY` evidence
   item citing the score and the model's predicted scenario.
3. **Blended score, one-way level drift.**
   `risk_score = round(max(det, 0.5*ml + 0.5*det), 2)` — the blend can never
   fall below the deterministic score. `risk_level` may rise **one** step
   (LOW → MEDIUM → HIGH → CRITICAL) if the blend crosses 0.75; it may
   **never** fall below the rules' level.
4. **ML unavailable → rules-only.** `ml_anomaly_score` is `null`, the
   assessment still completes, and the API logs once.

### 17.4 The 9-value anomaly taxonomy

| `anomaly_type` | Meaning |
|---|---|
| `NONE` | Clean, fully evidenced success — nothing anomalous, nothing to recover. |
| `GENUINE_FAILURE` | Money left the customer but the flow failed downstream (gateway/merchant/settlement failure with nothing after it) — the recoverable case. |
| `DOUBLE_DEDUCTION` | Two distinct provider events confirmed the customer's debit — money moved twice; manual financial review. |
| `DUPLICATE_TRANSACTION` | A provider reference shared with *another* transaction — duplicate submission (amount similarity alone never triggers this). |
| `SUCCESSFUL_BUT_UNCONFIRMED` | Settlement confirmed without merchant confirmation — reconciliation, not recovery. |
| `FALSE_COMPLAINT` | Full success chain **plus** an explicit customer-reported-failure flag; never inferred from success alone. |
| `SUSPICIOUS` | Unusual retry/attempt pattern without a determinable outcome, or an `INCOMPLETE`/`UNKNOWN` case the ML upgrade promoted (17.3 rule 2). |
| `INCOMPLETE` | Too little evidence to say anything — uncertainty, honestly reported. |
| `UNKNOWN` | Fallback when even incompleteness could not be established; never a guessed verdict. |

Rules fire **first match wins**, financial-integrity rules (double deduction,
duplicate transaction) outranking outcome rules, and every observed fact used
is recorded as an `EvidenceItem` (code, description, source, severity) — the
full audit trail is stored even when a rule did not fire.

### 17.5 Risk score, risk level — and an honesty note

Five risk levels exist: `LOW`, `MEDIUM`, `HIGH`, `CRITICAL` (rules) and
`UNKNOWN` (uncertainty, e.g. `INCOMPLETE` evidence). The **level** is the
firing rule's verdict; the **score** is the evidence weight behind it
(`round(min(1.0, 0.85*n_high + 0.5*n_medium + 0.2*n_low), 2)`, then the ML
blend of 17.3).

> **Honesty note:** `risk_score` is **NOT a calibrated fraud probability.**
> It is a deterministic evidence-weighting number (plus an ML blend) trained
> and tuned on synthetic data. Report it as **"Risk Score: 0.87 / Risk
> Level: HIGH"** — never as "87% chance of fraud". No stage of this system
> makes a probabilistic claim about a person.

### 17.6 `recovery_candidate` semantics

`recovery_candidate: true` **only** for clean genuine failures (`GENUINE_FAILURE`
backed by unambiguous single-failure evidence). Every other classification is
`false` and carries a `recovery_block_reason`, e.g.:

| Classification | Block reason (examples) |
|---|---|
| `DOUBLE_DEDUCTION` / `DUPLICATE_TRANSACTION` | "multiple customer debit confirmations require manual financial review" / "same provider reference found on another transaction" |
| `SUCCESSFUL_BUT_UNCONFIRMED` | "settlement confirmed; funds moved — manual reconciliation required" |
| `NONE` | "no failure to recover" |
| `FALSE_COMPLAINT` | "payment completed successfully — customer-reported failure contradicted by full evidence chain" |
| `SUSPICIOUS` | "unusual retry/attempt pattern without a determinable payment outcome" |
| `INCOMPLETE` / `UNKNOWN` | "insufficient payment evidence" |

This flag is evidence for a later recovery stage — Stage 7 never acts on it.

### 17.7 APIs

| Endpoint | Roles | Purpose |
|---|---|---|
| `POST /api/v1/transactions/{id}/risk-assessment` | `SYSTEM`, `ADMIN` | Run the hybrid engine, persist the assessment evidence + one `ANOMALY_CLASSIFIED` twin observation, in one commit. Body `{"customer_reported_failure": false}` (flag required for `FALSE_COMPLAINT` — never inferred). |
| `GET /api/v1/transactions/{id}/risk-assessment` | `SYSTEM`, `ADMIN`, `SUPPORT` | Latest stored assessment, read-only (`reused: true`; nothing recomputed, no twin append). |

`CUSTOMER` is excluded from both: the assessment carries internal decision
evidence (triggered rules, ML scores, block reasons) — a customer-facing view
of anti-fraud evidence would leak risk-model internals and enable gaming the
rules. Response shape (both endpoints):

```json
{
  "assessment": {
    "anomaly_type": "GENUINE_FAILURE",
    "risk_level": "LOW",
    "risk_score": 0.5,
    "ml_anomaly_score": 0.31,
    "recovery_candidate": true,
    "recovery_block_reason": null,
    "evidence": [...],
    "triggered_rules": [{"rule_id": "R3", "name": "genuine gateway failure"}],
    "model_version": "synthetic-v1",
    "rule_version": "1"
  },
  "reused": false,
  "digital_twin_event_recorded": true
}
```

**Idempotency:** an `evidence_fingerprint` (sha256 over the transaction id,
sorted event evidence, reconstruction root cause + confidence, complaint
flag, effective model version, rule version) decides reuse — the same input
evidence replays the stored assessment with `reused: true`, no re-insert and
no duplicate twin event. A model upgrade alone produces a fresh assessment.

### 17.8 Persistence and Digital Twin

Assessments are stored in the new **`risk_assessments`** table (migration
`dfd33619148d`) including the full evidence and triggered-rule JSON, the
fingerprint, and both deterministic and blended scores. Classification
appends one **`ANOMALY_CLASSIFIED`** observation to the append-only Digital
Twin — an observation, not a state transition (`previous_state ==
new_state`), structured metadata (anomaly type, level, score, rule ids,
model/rule versions, fingerprint), idempotent on the fingerprint.

### 17.9 GenAI integration (v3)

`ExplanationContext` now carries the assessment; `PROMPT_VERSION` is **v3**,
so cached explanations regenerate when a classification changes. Wording is
customer-neutral by design: **no anomaly data reaches customers** — the
customer audience receives neither the classification, the triggered rules,
nor the ML score, and prompts never use "fraud" language about persons (a
payment may be `SUSPICIOUS`; a customer is never accused). Support/system
audiences see the full evidence trail.

### 17.10 End-to-end verification script

`scripts/stage7_e2e.py` drives the **live API** through five scenarios
(create transaction → ingest payment events → POST risk-assessment → GET
read-back → assert an `ANOMALY_CLASSIFIED` twin event in the timeline) and
prints a PASS/FAIL verdict table (stdlib only, deterministic ids from
`--seed`):

```bash
uvicorn api.main:app --reload          # terminal 1
py -m scripts.stage7_e2e               # terminal 2 (defaults below)
py -m scripts.stage7_e2e --api-url http://127.0.0.1:8000 --seed 42 --verbose
```

| Scenario | Evidence | Expected |
|---|---|---|
| S1 `gateway_timeout` | debit confirmed, request sent, gateway times out | `GENUINE_FAILURE`, candidate `true` |
| S2 double deduction | customer debited **twice**, one gateway attempt fails | `DOUBLE_DEDUCTION`, candidate `false` |
| S3 success | all 7 happy-path events | `NONE`, candidate `false` |
| S4 incomplete | a single debit confirmation | `INCOMPLETE`, candidate `false` |
| S5 suspicious-looking | gateway timeout on a high-risk transaction (5 retries, 4 prior failures, poor network) | `GENUINE_FAILURE` — **evidence wins precedence** over the elevated ML score; the score is printed for inspection |

### 17.11 Key Stage 7 files

| Path | Purpose |
|---|---|
| `api/services/anomaly_rules.py` | Versioned deterministic rule engine (R1–R10, evidence-first) |
| `api/services/risk_engine.py` | Hybrid engine: pinned ML precedence, fingerprint idempotency, twin observation |
| `api/routes/risk_assessment.py` | POST/GET risk-assessment endpoints |
| `ml/train_anomaly.py` / `ml/predict_anomaly.py` | Anomaly scenario classifier (`synthetic-v1`) |
| `scripts/generate_anomaly_dataset.py` | Scenario-labeled training data generator |
| `scripts/stage7_e2e.py` | Live-API E2E verification (17.10) |
| `reports/stage7_anomaly_model.md` | Model training report (honest metrics) |

> **Stage 7 uses synthetic/sandbox data and is not validated against real banking fraud datasets.**

---

## 18. Autonomous recovery (Stage 8)

Stages 6–7 answered *"what happened?"* and *"what does the evidence indicate?"*.
Stage 8 finally **acts** — but only through a deterministic, auditable pipeline
whose every step is owned by the backend:

```
Stage 6 Reconstruction → Stage 7 Risk Assessment → Recovery Policy → Safety Gate → Idempotent Executor → Sandbox Provider → Verification → Digital Twin
```

**The frontend never releases anything.** There is no human "release" click in
the autonomous path: `POST /recovery/process` is backend-owned orchestration —
safety and policy decide server-side, the executor runs, the twin records, and
the frontend only **visualizes** the outcome (read-only evidence from
`GET /recovery` and the timeline). The existing manual
`POST /recovery/release-limit` (section 10) keeps working unchanged for the
`MANUAL_REVIEW` path — see 18.8.

### 18.1 Eligibility — the decision policy

`api/services/recovery_decision_policy.py` is a pure function of (transaction,
stored Stage 7 assessment, Stage 6 reconstruction). Exactly one shape is
eligible:

> `GENUINE_FAILURE` **+** risk `LOW`/`MEDIUM` **+** `recovery_candidate: true`
> **+** exactly one debit confirmation **+** settlement not confirmed
> → **`RELEASE_LIMIT`**.

Every other taxonomy value is blocked with a stable reason code:

| Case | Action | `blocked_reason` |
|---|---|---|
| `GENUINE_FAILURE` but risk rose to `HIGH`/`CRITICAL` since assessment (or caps exceeded) | `MANUAL_REVIEW` | `RISK_NO_LONGER_PERMITS` / `NOT_ELIGIBLE` |
| `DOUBLE_DEDUCTION` | `NO_ACTION` | `DOUBLE_DEDUCTION` (manual financial review) |
| `DUPLICATE_TRANSACTION`, `SUCCESSFUL_BUT_UNCONFIRMED`, `FALSE_COMPLAINT`, `SUSPICIOUS`, `UNKNOWN` | `NO_ACTION` / `MANUAL_REVIEW` | `NOT_ELIGIBLE` |
| `INCOMPLETE` — not enough evidence to say anything | `NO_ACTION` | `INSUFFICIENT_EVIDENCE` |
| `NONE` — payment completed | `NO_ACTION` | `ALREADY_SUCCESS` |

`HIGH`/`CRITICAL` genuine failures are queued (`MANUAL_REVIEW_QUEUED`), never
auto-released: the autonomous pipeline widens the funnel, it does not lower the
bar.

### 18.2 The safety gate — fresh evidence, nothing trusted

Eligibility was decided on a snapshot. Before a single unit moves,
`api/services/recovery_safety.py` **re-derives everything** from fresh data:
events are reloaded, the reconstruction rebuilt, the latest stored assessment
re-read, existing recovery rows checked. The recheck list:

1. Transaction still in a recoverable state (`FAILED`, not already
   `LIMIT_RELEASED` / recovered — `ALREADY_SUCCESS`, `ALREADY_RECOVERED`).
2. **No new successful settlement** since the assessment —
   `NEW_SUCCESSFUL_SETTLEMENT`. This is the race protection for spec section
   10's scenario: a settlement confirmation that lands *while* recovery is
   being considered must abort the release, otherwise the customer would be
   repaid for a payment that actually succeeded.
3. Still exactly one debit confirmation (`DOUBLE_DEDUCTION` otherwise).
4. A current failure still exists and reconstruction still points at one
   (`INSUFFICIENT_EVIDENCE` otherwise).
5. Risk level still permits release (`RISK_NO_LONGER_PERMITS`).

**When uncertain, DO NOT RECOVER** — the gate blocks on any doubt and the row
lands in `BLOCKED` with the reason code.

### 18.3 Idempotency and retries

- The idempotency key is `sha256(transaction_id + action + policy_version +
  evidence_fingerprint)`, enforced by a **DB UNIQUE constraint**
  (`RecoveryActionRecord.idempotency_key`). Identical evidence replays the
  stored row — the provider is never called twice for the same recovery.
- The provider level is independently idempotent: `MockPaymentProvider`
  detects replays of an already-processed release and returns the original
  reference (`already_processed`) instead of double-releasing.
- Retries are **bounded: max 3 attempts** on the same row, and only for
  technical `FAILED` executions. Business blocks (`BLOCKED` rows) are
  **never retried** — a policy refusal is an answer, not a transient error.

### 18.4 The mock provider + sandbox ledger

`api/services/payment_provider.py` implements `MockPaymentProvider`: an
**in-memory** ledger (`ensure_hold` / `release_limit` move real state within
it — a hold must exist before it can be released, a reference is issued per
release). It resets on server restart. Every row, response, and twin event is
labeled **SIMULATED** (`simulated: true` throughout the API surface).

> **Autonomous recovery operates on a simulated sandbox provider. No real
> financial transaction is performed.**

### 18.5 Verification

After execution, `api/services/recovery_verifier.py` runs 6 checks (provider
reference issued, amount matches, hold state consistent, ledger entry present,
status consistent, currency match). The row reaches **`VERIFIED`** — and the
transaction **`LIMIT_RELEASED`** — **only if every check passes**; otherwise
the row lands in a safe state (`FAILED`, bounded-retry eligible) and the
transaction stays out of `LIMIT_RELEASED`.

### 18.6 Digital Twin recovery lifecycle

Append-only, as everywhere else. The recovery lifecycle is recorded as
observations (`previous_state == new_state`) plus one real validated
transition at the end:

```
RECOVERY_ELIGIBILITY_ASSESSED → RECOVERY_APPROVED → RECOVERY_STARTED
→ RECOVERY_EXECUTED → RECOVERY_VERIFIED   (then the transaction's one real hop: FAILED → LIMIT_RELEASED)
                             ↘ RECOVERY_BLOCKED     (blocked: reason code)
                             ↘ RECOVERY_FAILED      (execution failure: safe state)
```

Eligibility assessments and approvals are **observations, never transitions** —
the state machine is only ever moved by the executor's explicit, validated
`LIMIT_RELEASED` hop.

### 18.7 Failure handling

Every failure path lands in a **safe state**: policy refusal → `BLOCKED` row +
`RECOVERY_BLOCKED` twin event; gate block → same, with the reason code;
execution failure → `FAILED` row + `RECOVERY_FAILED` (retryable up to 3);
verification failure → never `VERIFIED`, never `LIMIT_RELEASED`. The system's
standing rule: **when uncertain, DO NOT RECOVER.**

### 18.8 Manual convergence

Support keeps the Stage 3 flow: `POST /recovery/release-limit` still works on
`MANUAL_REVIEW` outcomes and **replays idempotently** against the same ledger.
What support **cannot** do is bypass the safety gate — no force-release
endpoint exists. That is a deliberate, documented decision: a human override
that skips fresh-evidence checks would reintroduce exactly the
settlement-race / double-deduction classes Stage 8 exists to prevent.

### 18.9 APIs

| Endpoint | Roles | Purpose |
|---|---|---|
| `POST /api/v1/transactions/{id}/recovery/process` | `SYSTEM`, `ADMIN` | Full pipeline, commit ONCE. Response `decision`: `AUTO_RECOVERED` \| `RECOVERY_BLOCKED` \| `MANUAL_REVIEW_QUEUED` \| `ALREADY_RECOVERED`, plus `action`, `status`, `recovery_id`, `provider_reference`, `reason`, `simulated`. |
| `POST /api/v1/transactions/{id}/recovery/evaluate` | `SYSTEM`, `ADMIN`, `SUPPORT` | Policy + safety only — no writes, no provider call ("why wasn't this auto-recovered?"). |
| `GET /api/v1/transactions/{id}/recovery` | `SYSTEM`, `ADMIN`, `SUPPORT` | Latest recovery action row, read-only evidence. |

### 18.10 End-to-end verification script

`scripts/stage8_e2e.py` drives the **live API** (create → ingest payment
events → POST risk-assessment → POST recovery/process → assertions on
`GET /recovery` + `GET /timeline`):

```bash
uvicorn api.main:app --reload          # terminal 1
py -m scripts.stage8_e2e               # terminal 2 (defaults below)
py -m scripts.stage8_e2e --api-url http://127.0.0.1:8000 --seed 42 --verbose
py -m scripts.stage8_e2e --demo        # S1 only, narrated step by step
```

| Scenario | Evidence | Expected |
|---|---|---|
| S1 genuine failure | debit OK, merchant confirmation times out | `AUTO_RECOVERED`, `RELEASE_LIMIT`, `VERIFIED`, simulated, sandbox reference, twin lifecycle present, tx `LIMIT_RELEASED` |
| S2 double deduction | customer debited twice, gateway times out | `RECOVERY_BLOCKED`, `NO_ACTION` — provider never called (no reference), `DOUBLE_DEDUCTION` |
| S3 success | all 7 happy-path events | `RECOVERY_BLOCKED`, `ALREADY_SUCCESS` |
| S4 incomplete | a single debit confirmation | `RECOVERY_BLOCKED`, `INSUFFICIENT_EVIDENCE` |
| S5 race (spec 43) | genuine failure, then a `SETTLEMENT_CONFIRMED` lands | `RECOVERY_BLOCKED` (`NEW_SUCCESSFUL_SETTLEMENT` or `ALREADY_SUCCESS`), **never** auto-recovered, tx never released |
| S6 duplicate | `process` called twice | 1st `AUTO_RECOVERED`, 2nd `ALREADY_RECOVERED`, same `recovery_id`, released exactly once |
| S7/S8 provider failure / verifier | failure hook is server-side — not injectable via the API | safe-state contract asserted live; full paths covered by `tests/test_recovery_executor.py` + `tests/test_recovery_verifier.py` |

### 18.11 Dashboard

The Stage 5 dashboard's recovery views surface the sandbox metrics (recovery
rows with `simulated: true`, blocked reasons, released amounts) read-only —
consistent with the no-human-release-click design above.

### 18.12 Key Stage 8 files

| Path | Purpose |
|---|---|
| `api/services/payment_provider.py` | `MockPaymentProvider` + in-memory sandbox ledger (idempotent) |
| `api/services/recovery_safety.py` | Fresh-evidence safety gate |
| `api/services/recovery_decision_policy.py` | Deterministic eligibility policy |
| `api/services/recovery_executor.py` | Idempotent executor (gate → provider → verification, bounded retries) |
| `api/services/recovery_verifier.py` | Post-execution verification (6 checks) |
| `api/services/autonomous_recovery.py` | Orchestration: assessment → policy → gate → execute, one commit |
| `api/routes/autonomous_recovery.py` | `process` / `evaluate` / `GET recovery` endpoints |
| `scripts/stage8_e2e.py` | Live-API E2E verification (18.10) |

## 19. Security & reliability hardening (Stage 9)

Stage 9 hardens the perimeter of the Stage 1–8 system without touching the
recovery policy, the state machine, or any ML decision path. As everywhere
else in this document, the honest caveats stand: **everything is sandbox /
simulation only — no real payment provider is connected**; **all ML models
are trained on 100% synthetic data** (`data/transactions.csv`, Stage 1) and
**have not been validated against real banking or fraud datasets**. Docker
also remains unverifiable on the machine this was written on (see the
standing limitation in the header note).

The full threat model — attack vectors, implemented defenses, and the test
that verifies each — lives in `reports/stage9_security.md`.

### 19.1 Authorization & ownership

Roles are unchanged (SYSTEM > ADMIN > SUPPORT > CUSTOMER), but CUSTOMER
access is now scoped to its own data:

| Resource | SYSTEM | ADMIN | SUPPORT | CUSTOMER |
|---|---|---|---|---|
| Transaction detail / timeline / reconstruction / explanations | all | all | all | **own only** (`user_id` match) |
| Risk assessment, recovery process/evaluate, stats, audit, sandbox reset | yes | yes | (see Stage 3–8 rules) | **excluded** |

- Config gains `customer_api_keys`, mapping key **names** to customer IDs,
  e.g. `"dev-customer-alice:alice,dev-customer-bob:bob"`; the legacy
  `dev-customer-key` maps to `"dev-customer"` for backwards compatibility.
- `AuthContext` now carries `customer_id`. Customer reads of transaction,
  timeline, reconstruction and explanations must match `user_id`; any other
  customer's resource returns **403 with a non-enumerating body** (same
  shape as 404, so a caller cannot probe which transaction IDs exist).
- The standing rule holds: **frontend role checks are UI-only.** The React
  app hides buttons, but every authorization decision is made server-side;
  the frontend has no authority of its own.

### 19.2 Authentication

- API keys are supplied via environment variables (`API_KEY_SYSTEM`,
  `API_KEY_ADMIN`, `API_KEY_SUPPORT`, plus the `customer_api_keys` map).
  Comparison is constant-time to avoid timing oracles.
- **No secrets in logs or audit rows:** the audit trail stores the key
  *name* (e.g. `dev-customer-alice`), never the key itself. Error bodies
  and logs never echo credentials. Plaintext keys in env is a documented
  residual limitation (see `reports/stage9_security.md`).
- The existing guard remains: dev keys are refused at startup when
  `ENVIRONMENT=production`.

### 19.3 Request correlation + security headers

Every request gets an `X-Request-ID`:

- A client-supplied ID is accepted only if it matches
  `^[A-Za-z0-9_-]{1,64}$`; otherwise one is generated.
- The ID is echoed on the response and included in **every** error body and
  log line, so a support engineer can trace one failing request end to end.

Middleware also sets security headers on every response:
`X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`,
`Referrer-Policy: strict-origin-when-cross-origin`,
`Permissions-Policy: camera=(), microphone=(), geolocation=()`. The
frontend nginx config mirrors these plus a CSP (see `frontend/nginx.conf`).

Key files: `api/middleware.py`, `api/core/request_context.py`.

### 19.4 Rate limiting

In-process, per key-name buckets (429 `RATE_LIMITED` with a `Retry-After`
header):

| Bucket | Limit |
|---|---|
| `explanations` | 30 / minute |
| `recovery` (process/evaluate) | 20 / minute |
| `risk-assessment` | 30 / minute |
| `payment-events` | 60 / minute |
| auth failures | 60 / minute |
| everything else (default) | 120 / minute |

**Documented limitations:** the limiter is per-process (not shared across
workers/replicas), resets on restart, and is not distributed — a real
deployment would need a shared store (e.g. Redis). It is **disabled
automatically when `ENVIRONMENT=test`** so the test suite is never
throttled.

### 19.5 Event ingestion hardening

- **Conflict detection:** ingesting a `provider_event_id` that already
  exists with a **materially different payload** returns
  `409 EVENT_CONFLICT` and writes an audit row. Material difference is
  decided by comparing payload **digests** (the digest function is
  documented in the ingestion code); only digests — never full payloads —
  are stored in audit metadata.
- **Payload limits:** provider event IDs must match a strict pattern,
  `metadata` is capped at 4096 characters, latency must be in
  `0..3600000` ms, and timestamps may be at most 24 h in the future
  (clock-skew tolerance). Violations are rejected with
  `PAYMENT_EVENT_REJECTED` audit rows.

### 19.6 Audit trail

Stage 9 adds a **`security_audit`** table, which is distinct from the
Digital Twin: the twin records *what happened to transactions* (domain
events, append-only); `security_audit` records *who did what to the
system* (security events, append-only, best-effort — a failed audit write
never breaks the request it is auditing).

- Columns: `audit_id`, `actor_type` / `actor_id` (the key **name**, never
  the secret), `action`, `resource`, `request_id`, `result`
  (`ALLOWED` / `DENIED`), `metadata`.
- Action vocabulary: `AUTH_FAILURE`, `RATE_LIMITED`,
  `PAYMENT_EVENT_CONFLICT`, `PAYMENT_EVENT_REJECTED`, `FORBIDDEN`,
  `DEMO_RESET`, `RECOVERY_PROCESS`.
- `GET /api/v1/audit` is available to SYSTEM/ADMIN only.

Key file: `api/services/audit.py`.

### 19.7 Sandbox ledger persistence + reset

- **Persistence:** the Stage 8 `MockPaymentProvider` ledger is now
  write-through to a `sandbox_ledger_entries` table and restored at startup
  (lifespan hook), so a restart no longer orphans recovery references.
  This is still **100% SIMULATED** — it persists the mock provider's
  fictional ledger, nothing more.
- **Reset:** `POST /api/v1/sandbox/reset` (SYSTEM/ADMIN) wipes the
  simulated ledger only and audits `DEMO_RESET`.
- **Seeding:** `scripts/seed_demo.py` creates a deterministic demo
  (transactions `DEMO-S1`..`DEMO-S6`) and is idempotent on re-runs.

Key files: `api/routes/sandbox.py`, `scripts/seed_demo.py`.

### 19.8 Failure modes

Every dependency can fail; the rules for each are fixed and tested:

| Failure | Behavior |
|---|---|
| ML inference fails (missing/corrupt model, exception) | **Rules-only assessment** — never interpreted as safe |
| GenAI provider fails / times out | Deterministic fallback explanation; decisions unaffected |
| Payment provider fails mid-execution | Result `FAILED` + bounded retries; never `VERIFIED` without passing verification |
| Recovery path | Unaffected by ML/GenAI failures — assessment and explanation degrade, execution and verification do not |

Verified by `tests/test_ml_genai_failure_modes.py` and
`tests/test_recovery_executor.py`.

### 19.9 Concurrency & idempotency recap

Same contract as Stage 8, now load-tested:

- The idempotency key is `sha256(transaction_id + action + policy_version +
  fingerprint)` and is UNIQUE in the database.
- A replay returns the original result; a concurrent duplicate loses the
  insert race, catches `IntegrityError`, and replays the winner's result —
  bounded retries, one commit per orchestration.
- Verified by `tests/test_recovery_concurrency_hard.py`.

### 19.10 Stage 9 architecture (hardening view)

```
                        request
                           |
              +------------v-------------+
              |   api/middleware.py      |
              |  X-Request-ID (validate/ |
              |  generate/echo)          |
              |  security headers        |
              |  rate limiter (per key,  |
              |  in-process)             |
              +------------+-------------+
                           |
              +------------v-------------+
              |   auth (constant-time)   |
              |  AuthContext + role +    |
              |  customer_id             |
              +------------+-------------+
                           |
        +------------------+------------------+
        |                  |                  |
+-------v-------+  +-------v--------+  +------v-----------+
| customer-owned|  | ingestion      |  | recovery /       |
| reads (403 on |  | provider_event |  | risk / stats     |
| foreign user) |  | digest conflict|  | (CUSTOMER excl.) |
+-------+-------+  +-------+--------+  +------+-----------+
        |                  |                  |
        +--------+---------+---------+--------+
                 |                     |
      +----------v----------+  +-------v------------------+
      | digital_twin (domain|  | audit.py: security_audit |
      | events, append-only)|  | (ALLOWED/DENIED, key     |
      +----------+----------+  | NAMES, request_id)       |
                 |             +--------------------------+
      +----------v----------+
      | sandbox_ledger_     |  scripts/seed_demo.py (DEMO-S1..S6)
      | entries (persisted, |  POST /sandbox/reset (staff)
      | SIMULATED)          |
      +---------------------+
```

Verification: `scripts/stage9_e2e.py` runs the security flow (T1–T13,
including a secret-leakage sweep) against a live API; unit coverage in
`tests/test_customer_ownership.py`, `test_rate_limiting.py`,
`test_request_ids.py`, `test_audit.py`, `test_event_conflicts.py`,
`test_sandbox_persistence.py`, `test_ml_genai_failure_modes.py`,
`test_recovery_concurrency_hard.py`.

## 20. Hackathon readiness & demo engineering (Stage 10)

Stage 10 adds no new business capability. It makes the finished Stage 1–9
system **demoable**: a judge can follow one transaction — debit, gateway
timeout, reconstruction, risk, policy, safety gate, sandbox execution,
verification, customer explanation — without reading code. The standing
disclaimers are unchanged and louder than ever: **simulated sandbox, no real
money moves; all models trained on 100% synthetic data.** The theme:
*automate recovery without automating trust.*

### 20.1 Demo control (backend)

- `api/services/demo_scenarios.py` is the single source of truth for the six
  deterministic scenarios (`DEMO-S1..S6`): fixtures, event chains, fixed
  timestamps, and the expected outcome of each. `scripts/seed_demo.py` now
  imports from it (CLI behavior preserved; S5 is redefined as the
  **settlement race** — prepare, then inject a late `SETTLEMENT_CONFIRMED`
  event, then process → the fresh-evidence safety gate blocks).
- `POST /api/v1/demo/scenarios/{key}/prepare` (SYSTEM/ADMIN) creates the
  transaction + event evidence + risk assessment **through the same services
  as the public API and never runs recovery** — there is no demo endpoint
  that bypasses the engine; the panel drives the real
  `/recovery/process`. `POST /api/v1/demo/scenarios/S5/inject-late-settlement`
  adds the race evidence (S5-only). `POST /api/v1/demo/reset` purges all
  `DEMO-S*` rows in FK-safe order, resets the sandbox ledger, re-prepares all
  six, and writes a `DEMO_RESET` audit row. `GET /api/v1/demo/scenarios` and
  `GET /api/v1/demo/status` (staff) report live state and a console health
  block (database / ML / GenAI / sandbox provider). `GET /api/v1/sandbox/ledger`
  (staff) exposes the simulated ledger. All demo mutations are audited
  (`DEMO_SEED` / `DEMO_RESET`) and rate-limited (60/min bucket).
- One real fix fell out of demo verification: the sandbox ledger **restore**
  subtracted full holds even for released entries, so a restart drifted the
  simulated balance negative. Restore now mirrors live operation
  (`INITIAL − Σ(held − released)`); the pinned persistence test was updated.

### 20.2 Judge-facing frontend

- **`/demo` — Demo Mode panel** (staff; actions SYSTEM/ADMIN): six scenario
  cards with live status, Prepare / Inject-late-settlement buttons, deep
  links to the transaction pages, and a confirmed **Reset demo** action
  ("affects simulated data only").
- **`/status` — System status console:** API / database / ML / GenAI /
  sandbox provider dots, with the honest `FALLBACK MODE` state when the AI
  provider is unavailable (the demo continues on deterministic fallback
  explanations).
- **Transaction page story:** a 7-step recovery pipeline strip (EVENTS → … →
  VERIFICATION) with the final chip `VERIFIED` / `BLOCKED`; an 8-row
  **safety gate checklist** derived only from real data (unknown → "—",
  never a fabricated ✓); a prominent **verification card**; the **simulated
  sandbox ledger** card; and structured **RECOVERY BLOCKED** reasons
  (reason / action / provider NOT CALLED) instead of a bare "failed".
- **Separation of concerns, visible:** the recovery panel is labeled a
  *deterministic decision* ("No AI involvement"); the explanation card is
  labeled *AI explanation* with a Customer view / Support view chip and a
  footer stating it never authorizes or changes a recovery outcome.
- **App chrome:** a permanent top banner **"SIMULATED SANDBOX — NO REAL
  MONEY MOVES"**, a dashboard hero + "How it works" 8-step explainer, and an
  opt-in **Judge mode** toggle (enlarges the pipeline / gate / verification,
  hides distractions; same real UI, same real backend).

### 20.3 Demo tooling & documents

- `py -m scripts.stage10_demo_check` — pre-flight gate (API, database, ML,
  sandbox, GenAI, demo data, optional frontend probe). Prints
  `READY FOR DEMO` or `NOT READY` (exit 1) — never claims readiness it
  cannot verify.
- `py -m scripts.stage10_e2e` — the full judge story against a live API:
  reset → S1 (prepare → reconstruct → risk → process → VERIFIED → ledger →
  bn/customer explanation → audit rows) → S2 blocked/provider-not-called →
  S5 race → S6 idempotency → §29 security sweep, with measured per-step
  timings. **18/18 checks.**
- Documents: `reports/stage10_demo_script.md` (timed 3–5 min run sheet),
  `stage10_judge_qa.md` (17 Q&As), `stage10_pitch.md`,
  `stage10_demo_check.md` (captured outputs + timings),
  `stage10_final_readiness.md` (the readiness matrix).

Verification (all green on 2026-10-03): **289 passed, 1 skipped** backend ·
**75/75** frontend · `tsc` clean · production build · Stage 8 E2E **7/7** ·
Stage 9 E2E **13/13** · Stage 10 E2E **18/18** · seed **6/6** · demo check
**READY FOR DEMO**. New tests: `tests/test_demo_router.py` (roles, no-bypass,
S5 race, reset determinism, audit) and nine frontend suites for the panel,
console, pipeline, safety gate and verification cards.
