# Stage 9 — Security threat model and hardening report

Stage 9 is a production-hardening pass over the Stage 1–8 system. It does not
change the recovery policy, the state machine, or any ML decision path. What
it changes is the perimeter: who may read or mutate which resource
(ownership), how requests are correlated and limited (request IDs, rate
limiting), how duplicate or tampered provider events are rejected (conflict
detection), how security-relevant actions are recorded (an append-only
`security_audit` trail that stores key *names*, never secrets), and how the
system behaves when its own dependencies fail (ML, GenAI, provider). Every
defense listed below as "Verified" is backed by an automated test; everything
else is stated as an honest residual limitation.

## Threat model

| Threat | Attack vector | Defense (implemented) | Status |
|---|---|---|---|
| IDOR — a customer reads another customer's transactions (timeline, reconstruction, explanations) | Authenticated CUSTOMER passes someone else's `transaction_id` | Ownership check on every customer-facing read: `AuthContext.customer_id` must match the transaction's `user_id`; mismatch → 403 with a non-enumerating body (same shape as 404 — the caller cannot probe which IDs exist) | Verified (`tests/test_customer_ownership.py`) |
| Idempotency-key replay of a recovery | Re-submitting the same recovery request to release funds twice | Deterministic idempotency key `sha256(transaction_id + action + policy_version + fingerprint)` with a UNIQUE constraint; a replay returns the original result instead of re-executing | Verified (`tests/test_recovery_executor.py`) |
| Double release under concurrency | Two executors race on the same transaction and both pass the eligibility check | UNIQUE-constraint insert is the arbiter: the loser catches `IntegrityError` and replays the winner's result; bounded retries, one commit per orchestration | Verified (`tests/test_recovery_concurrency_hard.py`) |
| Provider-event tampering / duplicate ingestion with altered payload | Re-sending a known `provider_event_id` with modified amount or status to distort the twin log | Conflict detection: same id + materially different payload (payload digest) → `409 EVENT_CONFLICT` + audit row; only digests stored in metadata; payload hardening (id pattern, metadata ≤ 4096 chars, latency 0..3600000 ms, ≤ 24 h future skew) | Verified (`tests/test_event_conflicts.py`) |
| Secret leakage in logs, error bodies, or audit rows | Reading logs/audit to harvest API keys | Audit rows store the key **name** (e.g. `dev-customer-alice`), never the secret; constant-time key comparison; no secrets in error bodies or logs; E2E secret-leakage sweep | Verified (`scripts/stage9_e2e.py` T11 + audit stores key names) |
| AI manipulation — prompt/payload steering a GenAI response into a decision | Malicious transaction fields injected into prompts to influence recovery outcomes | Architectural: GenAI is **explanation-only** and sits strictly after the deterministic decision; provider failure falls back to a deterministic template; no AI output is ever interpreted as a decision or a safety signal | Verified (`tests/test_ai_service.py`) |
| ML failure misread as "safe" | Model file missing/corrupt, or inference error at assessment time | ML failure degrades to a **rules-only** assessment that is never interpreted as safe; recovery path unaffected | Verified (`tests/test_ml_genai_failure_modes.py`) |
| Payment provider failure mid-recovery | Sandbox provider unavailable or erroring during release | Execution result FAILED with bounded retries; the transaction is never marked VERIFIED without passing verification | Verified (`tests/test_recovery_executor.py`) |
| New-settlement race during auto-recovery | A fresh settlement event lands between the eligibility check and execution | Fresh-evidence safety gate re-checks evidence at execution time and blocks stale recoveries | Verified (`scripts/stage8_e2e.py` S5 + safety gate) |
| Rate abuse / brute force on keyed endpoints | Hammering `explanations`, `recovery`, `risk-assessment`, `payment-events`, or the auth endpoints | Per-key-name in-process rate limiting (429 `RATE_LIMITED` + `Retry-After`); see bucket table in README §19.4 | Verified (`tests/test_rate_limiting.py`) |
| State tampering — driving the twin log through illegal transitions or forging state fields | Forging events or calling APIs out of order to skip states | The state machine remains the single transition authority; illegal transitions are rejected, and DB constraints/unique keys back the idempotency and integrity layer | Verified (`tests/test_state_integrity.py`) |

## Residual limitations

Stated plainly — these are known gaps, deliberately not papered over:

- **In-process rate limiter.** The limiter is per-process, resets on restart,
  and is not distributed. Behind multiple uvicorn workers or replicas each
  process keeps its own buckets. Disabled entirely when
  `ENVIRONMENT=test`. A production deployment wanting real limits needs a
  shared store (e.g. Redis) — out of scope for this stage.
- **In-memory session/auth model.** There are no user sessions, tokens, or
  refresh flows; every request re-authenticates via its API key. Simple and
  auditable, but there is no per-request revocation beyond key rotation.
- **API keys live in environment variables as plaintext.** Hashing them at
  rest would break the demo auth model (the key *is* the credential the
  client presents; there is no separate lookup secret). Documented instead
  of hidden; production keys must be injected via the environment/secret
  manager, and dev keys are refused in `ENVIRONMENT=production`.
- **The sandbox ledger is simulation.** `sandbox_ledger_entries` persists
  the **mock** provider's ledger so restarts keep recovery references
  verifiable — it is still 100% SIMULATED and connected to no real money
  movement.
- **No TLS termination locally.** Nothing in this repo serves TLS; the
  nginx config assumes a reverse proxy in production terminates TLS in
  front of the API and the frontend container.
- **Docker unverified.** Docker is not installed on the development machine
  that produced this stage; the Dockerfiles and compose file were improved
  (non-root user, healthchecks, security headers, CSP) but never built or
  run here. README §19 carries the same standing disclaimer.

## Design-level constraint guarantees

Constraints that back the hardening claims above, as they exist in the
SQLAlchemy models (`api/db/models.py`) and Alembic migrations:

- **Idempotency:** UNIQUE constraint on the recovery idempotency key
  (`sha256(transaction_id + action + policy_version + fingerprint)`). This
  single constraint is what makes replay-returns-original and
  IntegrityError-replay-under-concurrency correct; the application logic
  treats constraint violation as "someone already did this" rather than an
  error path.
- **Provider-event conflicts:** provider events are keyed by
  `provider_event_id`; the conflict check compares payload digests for a
  matching id (digests only — never full payloads — are written to audit
  metadata).
- **Digital Twin:** append-only event log — updates and deletes are not part
  of the service API surface; each row records transaction, from/to state,
  actor and timestamp.
- **`security_audit`:** append-only security trail (`audit_id`,
  `actor_type`/`actor_id` = key name, `action`, `resource`, `request_id`,
  `result` ALLOWED/DENIED, `metadata`). Writes are best-effort — an audit
  failure never breaks the request it is auditing.
- **`sandbox_ledger_entries`:** write-through mirror of the in-memory
  `MockPaymentProvider` ledger, restored at startup via the lifespan hook;
  simulated only.

## Database constraint audit

Live Postgres introspection (`payment_recovery`, 2026-10-03): unique
constraints/indexes on the five evidence-bearing tables, with rationale.

### `payment_events`
| Constraint / index | Kind | Rationale |
| --- | --- | --- |
| `ix_payment_events_provider_event_id` | UNIQUE | Idempotent ingestion: provider redelivery can never double-insert; also the arbiter of the concurrent-delivery race handled in `ingest_events`. |
| `ix_payment_events_event_id` | UNIQUE | Public event identifier — stable handle for reads/API responses. |
| `ix_payment_events_transaction_id` | INDEX | Per-transaction evidence fetch (`get_payment_events` scans by transaction). |
| `ix_payment_events_event_timestamp` | INDEX | Domain-time ordering key for reconstruction; keeps the ORDER BY from sorting in memory. |
| `payment_events_pkey` | PK (id) | Surrogate key. |

### `recovery_actions`
| Constraint / index | Kind | Rationale |
| --- | --- | --- |
| `ix_recovery_actions_idempotency_key` | UNIQUE | Recovery execution is exactly-once even under concurrent triggers. |
| `ix_recovery_actions_recovery_id` | UNIQUE | One action per recovery decision — no duplicate execution of the same decision. |
| `ix_recovery_actions_transaction_id` | INDEX | Transaction-history joins. |
| `ix_recovery_actions_risk_assessment_id` | INDEX | Traceability from action back to the assessment that authorized it. |
| `ix_recovery_actions_status` | INDEX | Operational dashboards filter by action status. |
| `ix_recovery_actions_created_at` | INDEX | Time-window queries / audits. |

### `risk_assessments`
| Constraint / index | Kind | Rationale |
| --- | --- | --- |
| `ix_risk_assessments_assessment_id` | UNIQUE | Stable public identifier per assessment. |
| `ix_risk_assessments_evidence_fingerprint` | INDEX | Evidence-hash idempotency lookups (dedup of repeated assessments on identical evidence). |
| `ix_risk_assessments_transaction_id` | INDEX | Assessment history per transaction. |
| `ix_risk_assessments_anomaly_type`, `ix_risk_assessments_risk_level` | INDEX | Triage/analytics filters. |
| `ix_risk_assessments_created_at` | INDEX | Time-window queries. |

### `digital_twin_events`
| Constraint / index | Kind | Rationale |
| --- | --- | --- |
| `ix_digital_twin_events_event_id` | UNIQUE | Stable twin-event identifier. |
| `ix_digital_twin_events_transaction_id` | INDEX | Twin replay for a transaction (append-only log reads are by transaction). |
| `ix_digital_twin_events_event_type` | INDEX | Queries like "latest ROOT_CAUSE_IDENTIFIED" (root-cause idempotency). |
| `ix_digital_twin_events_timestamp` | INDEX | Chronological replay ordering. |

### `transactions`
| Constraint / index | Kind | Rationale |
| --- | --- | --- |
| `ix_transactions_transaction_id` | UNIQUE | Domain identity — one row per transaction_id; the anchor every other table's FK points at. |
| `ix_transactions_user_id`, `ix_transactions_merchant_id` | INDEX | User/merchant lookups and per-merchant analytics. |
| `ix_transactions_current_state` | INDEX | State-machine dashboards / recovery queues filter by state. |
| `ix_transactions_timestamp` | INDEX | Time-window queries. |

### Referential integrity
All four evidence tables carry `FOREIGN KEY (transaction_id) REFERENCES
transactions(transaction_id)` — evidence can never orphan from its
transaction. No unique constraint is redundant: each guards a distinct
idempotency or identity invariant (provider_event_id, idempotency_key,
recovery_id, assessment_id, event_id, transaction_id).
