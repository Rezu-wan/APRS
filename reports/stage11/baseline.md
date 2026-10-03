# Stage 11 — Baseline Report

**Date:** 2026-10-03 · **Branch:** `frontend` · **Head at baseline:** `77e387d` (Stage 10 complete, pushed)

All values measured on this machine immediately before any Stage 11 modification.

## Verification gates (baseline)

| Gate | Result |
|---|---|
| Backend pytest | **289 passed, 1 skipped** (191 s) |
| Frontend vitest | **75/75** (14 files) |
| TypeScript `tsc --noEmit` | **clean** |
| Production build (`npm run build`) | **pass** (3.7 s) |
| Stage 8 E2E | **7/7** |
| Stage 9 E2E | **13/13** (after baseline harness fix — see below) |
| Stage 10 E2E | **18/18** |
| Demo seed | **6/6** |
| Demo check | **READY FOR DEMO** (exit 0) |
| Live server | healthy on `127.0.0.1:8000` (Postgres `payment_recovery`, ML loaded, env=development) |

## Baseline harness fix (no production code changed)

The Stage 9 E2E (`scripts/stage9_e2e.py`) failed T11 on the baseline run:
`"T10-audit-admin body contains an 'sk-' string"`. Root cause: the sweep
treated any `sk-` **substring** as a secret leak, but the Stage 10 security
probes write FORBIDDEN audit rows whose `resource_id` contains
`/transactions/.../ri`**`sk-`**`assessment`; once those rows entered the
50-row audit window, the sweep false-positived. This is the same
latent harness bug fixed in `stage10_e2e.py` during Stage 10 integration.
Fix: `sk-` is now checked as a quoted-token prefix (`"sk-..."`) in both
scripts. Re-run: **13/13 PASS**. No backend/frontend code changed.

## System state at baseline (fact sheet)

- **Tables** (api/db/models.py): transactions, digital_twin_events (has
  `timestamp`, no `created_at`), recovery_decisions (UNIQUE transaction_id),
  ai_explanations, payment_events (UNIQUE provider_event_id, authoritative
  `event_timestamp`, source/status/reference_id/latency_ms/metadata,
  created_at), risk_assessments (UNIQUE-indexed evidence_fingerprint),
  recovery_actions (UNIQUE idempotency_key, attempt_count, versions),
  security_audit, sandbox_ledger_entries.
- **Alembic head:** `b4038c3dee26` (security_audit + sandbox_ledger).
- **Digital Twin:** api/services/digital_twin.py — `append_event`,
  `get_timeline` (single unified twin; Stage 11 must not fork it).
- **Reconstruction engine (pure):** `reconstruct_from_events(transaction_id,
  events, now)` in api/services/event_reconstruction.py — the temporal
  query will REUSE it on time-filtered event sets (no second engine).
- **Risk engine:** run_assessment/persist_assessment/record_anomaly_classified
  in api/services/risk_engine.py; fingerprint = sha256 of event set +
  root cause + flags + versions.
- **Recovery chain:** decide() → check_safety() (fresh evidence) →
  execute_recovery() (idempotency_key, attempt_count ≤ 3) → verify_release()
  (6 checks); orchestration in api/services/autonomous_recovery.py.
- **Sandbox provider:** MockPaymentProvider (set_failure one-shot injection,
  ledger_snapshot, available_limit); write-through persistence.
- **Security:** X-API-Key roles; can_access_transaction ownership rule
  (staff-full / CUSTOMER user_id match); audit constants AUTH_FAILURE …
  RECOVERY_PROCESS + Stage 11 additions (POLICY_SIMULATION, CHAOS_TEST,
  TEMPORAL_QUERY, GRAPH_ANALYSIS, MODEL_SIGNAL); rate buckets incl. demo
  60/min; request-id contextvar + log filter (the only correlation
  infrastructure today).
- **Observability gap (Stage 11 11G target):** no metrics endpoint, no
  counters module; only DB-derived /stats/summary.
- **Clean slate confirmed:** no existing event bus, pub/sub, temporal
  query, relationship/graph analysis, policy simulator, or chaos tooling
  anywhere in api/ or scripts/ (grep-verified).

## Stage 11 plan (phases, per spec §3/§32)

11A event bus → 11B temporal twin → 11C online intelligence → 11D
relationship graph → 11E policy simulator → 11F chaos/safety → 11G
observability → 11H research evaluation; tests + phase commits between
phases; full regression at the end. Standing invariants (§15) and the
Stage 9 security model are non-negotiable throughout.
