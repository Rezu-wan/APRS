# Stage 10 — Demo Health Check & E2E Results

**Date:** 2026-10-03 · **Branch:** `frontend` · **Environment:** local dev — PostgreSQL 18 (`payment_recovery`), uvicorn on `127.0.0.1:8000`, vite dev server on `:5173`, ML artifacts from `models/`, GenAI provider `mock` (keyless).

Everything below is real captured output from the two Stage 10 scripts. Nothing is projected or estimated.

---

## 1. `py -m scripts.stage10_demo_check`

Pre-flight check run immediately before the demo E2E (spec §21). Exit code 0.

```text
STAGE 10 DEMO CHECK
API              PASS   status=healthy
DATABASE         PASS   connected
ML               PASS   models loaded
SANDBOX          PASS   provider=mock, available_limit=10000.0 BDT
GENAI            PASS   provider=mock
DEMO DATA        PASS   all 6 seeded
FRONTEND         PASS   http://localhost:5173

READY FOR DEMO
```

Notes:

- `GENAI provider=mock` means the deterministic mock provider is answering — the demo **continues** if a real provider is unavailable (`is_fallback` explanations, `available` → `fallback` semantics). The check treats `fallback` as a PASS with an honest degraded note.
- `SANDBOX` fails hard if the demo endpoints are not deployed (404) or `available_limit < 0`.
- `FRONTEND` is a WARN-unless-`--require-frontend` check; the vite dev server was running here, so it is a full PASS.
- Honest negative-path verification: with the backend slice not yet deployed, the same script printed `NOT READY -- 2 check(s) failed` (SANDBOX + DEMO DATA) and exited 1 — it does not claim readiness when something is missing.

## 2. `py -m scripts.stage10_e2e`

The complete judge demonstration, end to end against the live server (spec §31). Rate limiting was ENABLED (the script pauses 1.2 s between calls; HTTP 429 would be a FAIL). **Result: 18/18 checks passed.**

```text
PASS [R1]  demo reset is deterministic — reset=true, 6 scenarios
PASS [S1a] S1 prepared, no recovery row yet — prepared, not processed; GET recovery -> 404
PASS [S1b] transaction read-back — current_state=RECOVERY_PENDING
PASS [S1c] reconstruction derives a root cause — root_cause=MERCHANT_CONFIRMATION_TIMEOUT
PASS [S1d] risk assessment exists (expect GENUINE_FAILURE) — anomaly_type=GENUINE_FAILURE
PASS [S1e] recovery process AUTO_RECOVERED/VERIFIED — AUTO_RECOVERED, recovery b05f9c9d-559…
PASS [S1f] recovery read-back agrees — row VERIFIED, same recovery_id
PASS [S1g] sandbox ledger reconciles — released 1200; available_limit reconciles (10000)
PASS [S1h] bn/customer explanation generated — bn explanation ok (provider generated)
PASS [S1i] audit trail records demo actions — 11 DEMO audit row(s)
PASS [S2]  double deduction blocked (provider not called) — blocked: DOUBLE_DEDUCTION, no provider call
PASS [S2-state] DEMO-S2 never released (S2) — current_state=RECOVERY_PENDING
PASS [S5]  late settlement blocks recovery (layered) — blocked: INSUFFICIENT_EVIDENCE
PASS [S5-state] DEMO-S5 never released (S5) — current_state=RECOVERY_PENDING
PASS [S6]  duplicate process is idempotent (released once) — released exactly once (1200.0 BDT)
PASS [SEC1] CUSTOMER denied on all demo/recovery/audit routes — 7 CUSTOMER probes -> 403
PASS [SEC2] SUPPORT may read, never mutate demo state — mutate -> 403 x2; read scenarios/status/ledger -> 200
PASS [SEC3] secret-leakage sweep over all collected bodies — 37 response bodies swept, zero leaks
```

Scenario verdicts (all deterministic, all sandbox-simulated):

| Scenario | Story | Outcome |
|---|---|---|
| DEMO-S1 | Genuine failure (merchant confirmation timeout) | `AUTO_RECOVERED` → `VERIFIED`, 1,200 BDT released once, ledger reconciles |
| DEMO-S2 | Double deduction | `RECOVERY_BLOCKED`, `DOUBLE_DEDUCTION`, provider **not called**, tx never released |
| DEMO-S5 | Race: settlement confirmed after assessment | `RECOVERY_BLOCKED` (`INSUFFICIENT_EVIDENCE` after fresh-evidence re-derivation — layered-defense block code), tx never released |
| DEMO-S6 | Duplicate recovery request | 1st `AUTO_RECOVERED`, 2nd `ALREADY_RECOVERED`, **same recovery_id**, released exactly once |

Security sweep (§29): CUSTOMER denied on reset / prepare / scenarios / ledger / recovery process / risk read / audit (7 × 403); SUPPORT can read demo state but can never mutate it (reset & prepare → 403); no dev key value and no `sk-`-prefixed string appears in any of the 37 collected response bodies.

## 3. Measured performance (§23)

Per-step wall-clock from the E2E timing table (each window includes the script's deliberate 1.2 s inter-call pacing for the live rate limiter):

| Step | Measured (ms) |
|---|---|
| Reset + 6 scenario re-seed | 2438.9 |
| Scenario listing + no-recovery probe (S1a) | 2432.4 |
| Transaction read | 1205.6 |
| Reconstruction | 1212.3 |
| Risk assessment | 1224.7 |
| Recovery process (first execution) | 1251.3 |
| Recovery read-back | 1206.1 |
| Ledger consistency check | 1204.0 |
| bn/customer explanation | 1220.0 |
| Audit trail read | 1207.6 |
| S2 double deduction (prepare+process+record) | 3670.5 |
| S5 race (prepare+inject+process+record) | 6092.1 |
| S6 idempotency (prepare+2×process+ledger) | 4919.7 |
| Security sweep (14 probes) | 8466.0 + 6065.2 |
| Total script wall-clock | 45630.6 |

Subtracting the deliberate 1.2 s pacing per call, the **actual server-side pipeline cost** (also confirmed with direct `curl` timing):

| Server operation | Measured latency |
|---|---|
| `POST …/recovery/process` (first execution, S1) | ≈ 51 ms (1251.3 ms wall − 1200 ms pacing) |
| `POST …/recovery/process` (idempotent replay) | 28.5–32.2 ms across 3 runs |
| `GET /health` (DB ping + ML status) | 3.8–5.2 ms across 3 runs |

No optimization was attempted (spec: do not optimize prematurely); the numbers are recorded as measured.

## 4. Regression gates (§32)

| Gate | Stage 9 baseline | Stage 10 result |
|---|---|---|
| Backend pytest | 278 passed, 1 skipped | **289 passed, 1 skipped** (+11 demo-router tests; 1 persistence test updated for the restore-accounting fix) |
| Frontend vitest | 55 passed | **75 passed** (+11 demo panel/console, +9 pipeline/gate/verification) |
| TypeScript (`tsc --noEmit`) | clean | **clean** |
| Production build | pass | **pass** |
| Stage 8 E2E | 7/7 | **7/7** |
| Stage 9 E2E | 13/13 | **13/13** |
| Seed demo | 6/6 | **6/6** (S5 label now reflects the race scenario) |

## 5. Demo-path fix found during verification

The sandbox ledger restore (Stage 9) subtracted the full held amount from `available_limit` even for entries already released, so a restart silently drifted the simulated balance negative (observed: −800 BDT after the E2E history). Stage 10 fixes the accounting to `INITIAL − Σ(held − released)`, mirroring live operation; the pinned Stage 9 persistence test was updated to the corrected invariant and the full suite stays green. This matters for the demo: the status console and ledger card now show `10,000 BDT available` after a restart with a fully-released history, and the demo check's non-negative balance assertion holds without a manual reset.
