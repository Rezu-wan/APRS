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
