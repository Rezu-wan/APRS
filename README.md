# AI-Powered Payment Failure Recovery & Digital Twin System

End-to-end pipeline for detecting failed payment transactions, scoring
recovery risk with ML, deciding — via a **deterministic policy** — whether to
auto-release the customer's limit, and recording every state change in an
**append-only Digital Twin event log**.

Built in three completed stages; GenAI explanation and the operator frontend
are later stages.

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
>   installed here). The compose file is provided as-is and untested locally;
>   the SQLite path has been verified end-to-end.

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
                                       later stages:  GenAI   frontend
                                       (EXPLAIN decisions only)
```

Flow: the **generator** produces the synthetic dataset; the **ML engine**
trains XGBoost models (`models/*.joblib`) and exposes `predict_transaction()`;
the **FastAPI backend** ingests transaction events, advances each transaction
through a state machine (`INITIATED -> PROCESSING -> SUCCESS | FAILED/STALLED
-> RISK_ASSESSED -> RECOVERY_PENDING -> LIMIT_RELEASED | MANUAL_REVIEW |
RECOVERY_REJECTED`), runs ML assessment on failure, applies the recovery
policy, and atomically persists state + Digital Twin events. **GenAI and the
frontend are later stages** — GenAI will only ever *explain* decisions, never
make them.

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
| `api/db/models.py`, `api/db/migrations/` | SQLAlchemy models + Alembic |

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

## 6. Authentication

Every endpoint except `/health` requires the `X-API-Key` header. Each key maps
to exactly one role; roles come from the environment (section 4).

| Role | Dev key (local only) | May call |
|---|---|---|
| `SYSTEM` | `dev-system-key` | ingest events, recovery, read transactions/timelines |
| `ADMIN` | `dev-admin-key` | ingest events, recovery, read transactions/timelines |
| `SUPPORT` | `dev-support-key` | read transactions/timelines only |
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

## 7. API reference

Base URL: `http://127.0.0.1:8000`. Error shape everywhere:
`{"error": {"code": "...", "message": "..."}}` (400 invalid transition,
401 auth, 403 role, 404 not found, 409 conflict, 422 validation, 503 ML not
loaded).

### 7.1 `POST /api/v1/transaction/event` — ingest an event (SYSTEM, ADMIN)

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

### 7.2 `POST /api/v1/recovery/release-limit` — recovery decision (SYSTEM, ADMIN)

Runs the deterministic policy (section 9). The decision comes from the
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

### 7.3 `GET /api/v1/transactions/{transaction_id}` (all roles)

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

### 7.4 `GET /api/v1/transactions/{transaction_id}/timeline` (all roles)

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

### 7.5 `GET /health` — no auth

```bash
curl http://127.0.0.1:8000/health
```

Healthy:

```json
{"status": "healthy", "database": "connected", "ml_models": "loaded", "environment": "development"}
```

Degraded (DB down or models not loaded) returns HTTP `503` with
`"status": "degraded"`.

## 8. ML integration

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

## 9. Recovery workflow

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
comes from the policy alone; a later GenAI stage will produce human-readable
justifications for the decisions this system already recorded.

## 10. Digital Twin

- The event log is **append-only**: one `digital_twin_events` row per state
  hop, never updated or deleted.
- State and events are **committed atomically in one database transaction** —
  a transaction is never seen in a state without its events, and vice versa.
- Each event records `event_type`, `previous_state`, `new_state`, the ML
  assessment (on `ML_RISK_ASSESSED`), the reason, and metadata (e.g. who
  decided, the policy snapshot). See the timeline example in section 7.4 for a
  failed-then-released transaction:
  `INITIATED -> PROCESSING -> FAILED -> RISK_ASSESSED -> RECOVERY_PENDING ->
  LIMIT_RELEASED`.

## 11. Docker

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

## 12. Tests

```bash
python -m pytest tests/ -q
```

Tests cover the state machine, the recovery policy table, idempotent replay,
auth/role enforcement, and the API endpoints against the SQLite dev database.

## 13. Stage reference (ML engine, standalone)

Stages 1–2 also run standalone:

```bash
python scripts/generate_dataset.py        # data/transactions.csv (25k rows)
python -m ml.train                        # models/*.joblib + reports/
python -m ml.evaluate                     # reports/ metrics and plots
```
