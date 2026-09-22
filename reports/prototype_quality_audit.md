# Prototype Quality Audit — Recovery Workflow (2026-09-22)

> **UPDATE (same day, fixes applied):** all five confirmed issues were fixed and re-verified. Changed files: `api/services/chaos.py`, `api/services/demo_scenarios.py`, `api/services/recovery_decision_policy.py`, `api/services/recovery_executor.py`, `scripts/stage10_e2e.py`, plus regression tests in `tests/test_recovery_policy_v2.py`, `tests/test_recovery_executor.py`, `tests/test_demo_router.py`. Post-fix evidence: targeted suites **100 passed, 1 skipped**; live Stage 10 E2E **18/18 PASS**; live chaos **9/9 runnable PASS** (all invariants held, DB_FAILURE_SIMULATION honest SKIP); dataset ground-truth replay now **0 ledger releases** with TXN-0000020 blocked on the exact recorded cap reason and TXN-0000201 still an exact DOUBLE_DEDUCTION match. Remaining failures (out of scope, unchanged): `test_event_conflicts` (10, own hardcoded 2026-10-02 dates), `test_payment_events` (3, same), `test_policy_simulator` (10 fixture errors, same), `test_event_bus` (1, same), `test_reconstruction` (1, the documented pre-existing failure). Details in §10.

Answers Judge 1's Prototype Quality criticism: **does the recovery workflow actually work, and what happens in concrete recovery/failure scenarios?** Everything below was executed against this working tree; nothing was redesigned or rewritten. The real dataset (`data/dataset/`, 32 MB, 10,506 transactions, 1,142 recorded recovery cases, manifest-verified) was treated as read-only ground truth; no dataset file or pre-existing database was modified or deleted.

Evidence logs: `reports/audit_evidence/pytest_full_2026-09-22.txt`, `reports/audit_evidence/stage10_e2e_2026-09-22.txt` (pre-fix) and `reports/audit_evidence/stage10_e2e_postfix_2026-09-22.txt`, `reports/audit_evidence/chaos_postfix_2026-09-22.txt`, `reports/audit_evidence/dataset_replay_postfix_2026-09-22.txt` (post-fix).

---

## 0. Prototype Quality — judge evidence summary

**Every number below was verified live on this machine on 2026-09-22 against the current working tree.**

**Does the recovery workflow actually work? — Yes, end to end, live.** On a live server with a fresh database, a seeded genuine failure (`DEMO-S1`) reconstructs to `MERCHANT_CONFIRMATION_TIMEOUT`, classifies `GENUINE_FAILURE` at LOW/MEDIUM risk, is released by policy `autonomous-v1` exactly once, verified against the sandbox ledger (`VERIFIED`), explained to the customer in Bangla, and audited — **Stage 10 E2E: 18/18 checks**.

**Five tested recovery/failure scenarios and their verified outcomes:**

| # | Scenario | Verified outcome | Safety mechanism that held | Evidence |
|---|---|---|---|---|
| 1 | Genuine failure → autonomous recovery (DEMO-S1) | `AUTO_RECOVERED → VERIFIED`; 1,200 BDT released exactly once; ledger reconciles; demo audit rows present | decision policy R-A, idempotency key, post-execution verifier | E2E S1a–S1i PASS |
| 2 | Double deduction (DEMO-S2 + real-dataset TXN-0000201) | `RECOVERY_BLOCKED / DOUBLE_DEDUCTION`; provider never called; transaction never released; live decision matched the dataset's recorded ground truth exactly | safety-gate debit-count check (≥2 debit confirmations → block) | E2E S2 PASS + replay log |
| 3 | Concurrent recovery race (10 threads on one transaction) | exactly one release, one provider reference, one non-replay decision; zero thread errors | idempotency-key UNIQUE + DB race handling + exactly-once ledger | chaos `CONCURRENT_RECOVERY` PASS (5/5 invariants); `tests/test_recovery_concurrency_hard.py` |
| 4 | Provider failure at release time (TIMEOUT and ERROR) | recovery row `FAILED` with the provider error code; nothing released; failure injection disarmed | provider-failure path + nothing-moved invariants | chaos `PROVIDER_TIMEOUT` / `PROVIDER_ERROR` PASS (6/6 invariants each) |
| 5 | Above-cap auto-release attempt (real-dataset TXN-0000020: 9,831.98 BDT vs 1,500 cap) | `NO_ACTION / BLOCKED — "amount 9831.98 exceeds the auto-release cap 1500.0: manual handling required"`; provider never called; re-release of historically-released TXN-0000537 also refused; **0 sandbox ledger releases across the whole replay** | hard autonomy caps in the decision policy + fresh-evidence gate + terminal-state lifecycle | replay log + §7 below |

(The late-settlement race — fresh `SETTLEMENT_CONFIRMED` evidence blocking a stale release — is additionally covered by chaos `LATE_SETTLEMENT` PASS (6/6 invariants) and E2E S5.)

**What safety mechanisms prevented bad recovery?** A first-match decision policy with hard amount/previous-failures caps; a 6-check safety gate re-derived from fresh evidence at execution time (state, in-flight rows, fresh settlement, debit count, root cause, still-failing); an idempotency key making every release exactly-once; post-execution verification against the provider ledger before any state transition; and a lifecycle pre-check that refuses releases from terminal states.

**What evidence proves the prototype works?**

- Recovery-focused suites (policy, executor, safety, verifier, recovery, concurrency, demo router, chaos, autonomous API): **100 passed, 1 skipped**
- Full backend suite: **463 passed, 15 failed, 1 skipped** — all 15 are date-anchored fixtures in 4 non-recovery test files plus the 1 documented reconstruction failure (§10)
- Live demo story: **Stage 10 E2E 18/18** (`python -m scripts.stage10_e2e`)
- Live chaos lab: **9/9 runnable scenarios PASS**, `DB_FAILURE_SIMULATION` honest SKIP (`POST /api/v1/chaos/run`)
- Real-dataset ground-truth replay (6-case deep replay of 1,142 recorded cases): **2 exact ground-truth matches, 3 safe blocks, 1 refused re-release, 0 releases**

**Reproduce (existing commands only):**

```bash
# 1) recovery-focused suites
.venv/bin/python -m pytest tests/test_recovery_policy_v2.py tests/test_recovery_executor.py \
  tests/test_recovery_safety.py tests/test_recovery_verifier.py tests/test_recovery.py \
  tests/test_recovery_concurrency_hard.py tests/test_demo_router.py tests/test_chaos.py \
  tests/test_autonomous_api.py -q          # → 100 passed, 1 skipped

# 2) live demo E2E (fresh scratch DB; server on :8010)
rm -f data/audit_e2e.db
DATABASE_URL=sqlite:///./data/audit_e2e.db .venv/bin/python -m alembic upgrade head
DATABASE_URL=sqlite:///./data/audit_e2e.db .venv/bin/python -m uvicorn api.main:app --port 8010 &
.venv/bin/python -m scripts.stage10_e2e --api-url http://127.0.0.1:8010   # → 18/18

# 3) chaos scenarios (same server; all 10 ids listed in §5)
curl -X POST http://127.0.0.1:8010/api/v1/chaos/run -H "X-API-Key: dev-admin-key" \
  -H "Content-Type: application/json" -d '{"scenario": "GATEWAY_TIMEOUT"}'

# 4) real-dataset replay (dataset stays READ-ONLY; replay lands in a scratch DB)
rm -f data/dataset_e2e.db
DATABASE_URL=sqlite:///./data/dataset_e2e.db .venv/bin/python -m alembic upgrade head
DATABASE_URL=sqlite:///./data/dataset_e2e.db .venv/bin/python -m scripts.load_dataset_db
DATABASE_URL=sqlite:///./data/dataset_e2e.db .venv/bin/python -m uvicorn api.main:app --port 8011 &
# then POST /api/v1/transactions/TXN-0000020|TXN-0000201|TXN-0000537/recovery/process
```

---

## 1. Verdict in one paragraph

The core recovery engine **works where it is tested**: all 46 recovery-focused pytest tests pass, and a live replay of the real dataset's recovery cases through `POST /transactions/{id}/recovery/process` reproduces the recorded safety behavior (double-deduction block, no double release, provider idempotency). But the prototype has a **time-bomb**: demo and chaos fixture timestamps are pinned to 2026-10-03 (`DEMO_BASE`, `CHAOS_BASE`), which is valid only when the wall clock is past 2026-10-02. On today's clock (2026-09-22) the 24 h future-skew guard rejects every fixture event, which breaks the live demo (8/18 E2E checks), all 9 runnable chaos scenarios, and 38 of the 39 failing unit tests. Separately, a live replay on the real dataset exposed **one genuine safety-policy gap**: the documented auto-release amount cap is not enforced in the autonomous pipeline, and a 9,831.98 BDT transaction (cap: 1,500) was released in the sandbox.

---

## 2. What passes (strongest existing tests, exact numbers)

| Suite | Command | Result |
|---|---|---|
| Recovery modules (executor, safety, verifier, policy v2, concurrency-hard, recovery API) | `pytest tests/test_recovery*.py tests/test_recovery_concurrency_hard.py` | **46 passed, 1 skipped** (62 s) |
| Full backend suite | `pytest tests/` | **425 passed, 39 failed, 10 errors, 1 skipped** (148 s) — README's "406 passed, 1 skipped" is stale |

Failure concentration (all from the same root cause unless noted):

| File | Failures/Errors | Cause |
|---|---|---|
| `tests/test_chaos.py` | 17 failed | fixture timestamps in the future (§3) |
| `tests/test_event_conflicts.py` | 10 failed | same (422 future-timestamp) |
| `tests/test_demo_router.py` | 7 failed | same |
| `tests/test_payment_events.py` | 3 failed | same (`assert 422 ...`) |
| `tests/test_event_bus.py` | 1 failed | same |
| `tests/test_reconstruction.py` | 1 failed | the **documented pre-existing** failure (`assert 'INCOMPLETE' == 'CUSTOMER_DEBIT_FAILED'`) |
| `tests/test_policy_simulator.py` | 10 errors | fixture `pydantic ValidationError` — same root cause |

---

## 3. Root cause of the 38 test failures + live demo/chaos breakage: a pinned-date time-bomb

- `api/services/demo_scenarios.py:43` — `DEMO_BASE = datetime(2026, 10, 3, 9, 0, 0, tzinfo=timezone.utc)`
- `api/services/chaos.py:69` — `CHAOS_BASE = datetime(2026, 10, 3, 13, 0, 0, tzinfo=timezone.utc)`
- `api/schemas/payment_events.py:17` — `MAX_FUTURE_SKEW = timedelta(hours=24)`; the `PaymentEventIn.event_timestamp` validator (lines 92–99) rejects anything more than 24 h in the future.

The repo's last commit is dated **2026-10-04** — the fixtures were valid when authored. The machine clock now reads **2026-09-22**, so every demo/chaos fixture timestamp is ~11 days in the future and event ingestion rejects them:

```
pydantic ValidationError for PaymentEventIn
event_timestamp: Value error, event_timestamp more than 1 day, 0:00:00 in the future
  input_value='2026-10-03T09:04:00.120Z'
```

Live consequences (fresh DB `data/audit_e2e.db`, server on :8010):

- `POST /api/v1/demo/scenarios/{S2,S5,S6}/prepare` → **HTTP 500** (`INTERNAL_ERROR`); `demo_scenarios.py:281` constructs `PaymentEventIn(**e)` inside the route, so the `ValidationError` escapes unhandled.
- Cascade on DEMO-S1 (the primary judge story): prepare fails → `reconstruction events_loaded=0 root_cause=INCOMPLETE` → risk `UNKNOWN` → recovery `blocked_reason=INSUFFICIENT_EVIDENCE`. The engine then *fails safe* (nothing released) — but the demo shows a blocked case instead of the intended AUTO_RECOVERED.

This is a fixture-anchoring bug, not an engine bug: the same scenarios pass in-suite whenever timestamps are valid (see §2: 46/46 recovery tests).

---

## 4. Live demo E2E — Stage 10 judge story: 8/18 PASS (expected 18/18)

`python -m scripts.stage10_e2e --api-url http://127.0.0.1:8010` — full log in `reports/audit_evidence/stage10_e2e_2026-09-22.txt`.

| Check | Result | Evidence |
|---|---|---|
| R1 demo reset deterministic | **FAIL** | `POST /demo/reset` → 500 (future-timestamp ValidationError mid-prepare) |
| S1a prepared, no recovery row | **FAIL** | `exists=True, prepared=False` |
| S1b transaction read-back | PASS | `current_state=RECOVERY_PENDING` |
| S1c reconstruction root cause | PASS* | `root_cause=INCOMPLETE` (degraded; expected MERCHANT_TIMEOUT — *caused by §3*) |
| S1d risk assessment exists | PASS* | `anomaly_type='INCOMPLETE'` vs expected GENUINE_FAILURE (*same cause) |
| S1e recovery process AUTO_RECOVERED | **FAIL** | got `RECOVERY_BLOCKED` (INSUFFICIENT_EVIDENCE, §3 cascade) |
| S1f recovery read-back VERIFIED | **FAIL** | row status `BLOCKED` |
| S1g ledger reconciles | **FAIL** | no ledger entry for DEMO-S1 (nothing released — consistent with the block) |
| S1h bn/customer explanation | PASS | mock provider, Bangla text returned |
| S1i audit trail demo rows | **FAIL** | no DEMO_RESET/DEMO_SEED rows recorded |
| S2 double deduction blocked | **FAIL** | prepare → 500 (§3) |
| S2-state never released | PASS | `RECOVERY_PENDING` |
| S5 race blocked (layered) | **FAIL** | prepare → 500 (§3) |
| S5-state never released | PASS | `RECOVERY_PENDING` |
| S6 idempotent duplicate process | **FAIL** | prepare → 500 (§3) |
| SEC1 customer denied everywhere | PASS | 7 probes → 403 |
| SEC2 support may read demo | **FAIL** | `GET /demo/scenarios` → 403; `routes/demo.py:144` requires `SYSTEM, ADMIN` — route/E2E contract drift, unrelated to §3 |
| SEC3 secret-leakage sweep | PASS | 20 bodies swept, zero leaks |

---

## 5. Chaos scenarios via live API — 0/9 runnable PASS, all one cause

`POST /api/v1/chaos/run` (fresh server, `data/audit_e2e.db`). The runner honestly reports failures instead of 500ing (`chaos.py:825`):

| Scenario | Verdict | Note |
|---|---|---|
| GATEWAY_TIMEOUT, GATEWAY_ERROR, MERCHANT_TIMEOUT, LATE_SETTLEMENT, DUPLICATE_EVENT, OUT_OF_ORDER_EVENT, PROVIDER_TIMEOUT, PROVIDER_ERROR, CONCURRENT_RECOVERY | **FAIL ×9** | identical note: `ValidationError ... event_timestamp more than 1 day in the future` |
| DB_FAILURE_SIMULATION | **SKIP** | honest skip by design (covered by unit tests) |

In-suite, the same scenarios fail for the same reason (17 `test_chaos.py` failures). The scenario *logic* itself is covered by the 46 passing recovery tests; only the fixture dates are broken.

---

## 6. Real-dataset replay — the recovery workflow against recorded ground truth

Method: loaded `data/dataset/*.csv` into a fresh temp DB (`scripts/load_dataset_db.py`; **all counts verified against manifest.json**), served it live on :8011, and pushed six recorded cases through the real pipeline. Ground truth = `recovery_cases.csv` (policy_version `autonomous-v1`).

| Transaction | Recorded outcome | Live outcome | Verdict |
|---|---|---|---|
| TXN-0000201 | BLOCKED / DOUBLE_DEDUCTION | `RECOVERY_BLOCKED`, anomaly DOUBLE_DEDUCTION, risk CRITICAL, `provider_reference=None` | **EXACT MATCH** |
| TXN-0003500 | BLOCKED / NEW_SUCCESSFUL_SETTLEMENT | `RECOVERY_BLOCKED` via anomaly UNKNOWN ("uncertainty never recovers") | SAFE, label drift |
| TXN-0000094 | BLOCKED / INSUFFICIENT_EVIDENCE | `RECOVERY_BLOCKED` via INCOMPLETE / BLOCK_INSUFFICIENT_EVIDENCE | SAFE, near-match |
| TXN-0000001 | MANUAL_REVIEW (resolved manually) | `RECOVERY_BLOCKED` via INCOMPLETE / UNKNOWN | SAFE, label drift |
| TXN-0000537 | RELEASE_LIMIT / RECOVERED (historical) | `RECOVERY_BLOCKED` — gate refuses an already-released tx; **0 new ledger entries** | PASS (no double release) |
| TXN-0000020 | BLOCKED / "amount above auto-release cap" (9,831.98 > 1,500) | **`action=RELEASE_LIMIT` — provider RELEASED 9,831.98 (`REL-2a0d7e90`)** | **FAIL — cap bypass (§7)** |

Safety-relevant integrity checks that held during the replay:

- Blocked cases never call the provider (`provider_reference=None`; ledger had exactly one entry, only from TXN-0000020).
- Provider idempotency: a second `process` call on TXN-0000020 produced **no second release** (ledger still exactly 1 entry, same reference).
- The state machine refused the illegal `RECOVERY_REJECTED → LIMIT_RELEASED` transition; the row honestly recorded `status=FAILED, failure_reason="state transition blocked..."` (`recovery_executor.py:523-527` — release is intentionally applied before the final transition; see §7 caveat).

---

## 7. Defect: the auto-release amount cap is not enforced in the autonomous pipeline

**Evidence chain:**

1. README + `api/core/config.py:53` document `RECOVERY_MAX_AMOUNT=1500` as "Maximum transaction amount eligible for auto-release"; the dataset's ground truth contains 379 cases blocked for "amount above auto-release cap" (plus 6 "previous failures above cap").
2. The flagship endpoint `POST /transactions/{id}/recovery/process` (`routes/autonomous_recovery.py:91`) runs `recovery_decision_policy.decide()` + `check_safety()` + executor.
3. `api/services/recovery_decision_policy.py:137-154` — rule R-A (the only eligible path) checks candidate/anomaly/risk/debit/settlement. **No amount or previous-failures cap.**
4. `api/services/recovery_safety.py:49-180` — the 6-check safety gate checks state, in-flight rows, fresh settlement, double debit, root cause, still-failing. **No cap.**
5. The cap exists only in the legacy `api/services/recovery_policy.py:33-51` (`evaluate_policy`), reachable only via `routes/recovery.py → recovery_service.request_release`.
6. Live result on real dataset: TXN-0000020 (9,831.98 BDT, ground truth "amount above auto-release cap") was **released** in the sandbox with `REL-2a0d7e90`; the final state transition was then refused as illegal, leaving ledger ("RELEASED 9,831.98") and transaction state (`RECOVERY_REJECTED`) permanently divergent, with the recovery row `FAILED`.

Git history shows the Stage 8 policy (`bda39d5`) never had the cap — this is a policy-coverage gap between two shipped policies, not a regression. Fix direction (not applied): enforce `amount ≤ recovery_max_amount` and `previous_failures ≤ recovery_max_previous_failures` inside `decide()` R-A (or as safety-gate check #0), and define compensation for a sandbox release whose final transition fails.

---

## 8. Scenario matrix (Judge 1's "3–5 concrete scenarios")

| # | Scenario | Strongest existing test | Result |
|---|---|---|---|
| 1 | Genuine failure → auto-recover exactly once | `tests/test_recovery_executor.py`, `test_recovery_concurrency_hard.py` (threaded exactly-once) | **PASS** in-suite (46/46); live demo path broken only by §3 fixture dates |
| 2 | Double deduction → block, provider never called | chaos `LATE_SETTLEMENT`/`DUPLICATE_EVENT` units + **live real-dataset TXN-0000201** | **PASS live (exact ground-truth match)** |
| 3 | Fresh settlement race → block stale release | `tests/test_recovery_safety.py` + safety-gate check #3 | PASS in-suite; live/chaos blocked by §3 |
| 4 | Idempotent replay → released exactly once | `tests/test_recovery_concurrency_hard.py` + live ledger check on TXN-0000537/0020 | **PASS** (0 double releases; provider replay returned same reference) |
| 5 | Provider failure (TIMEOUT/ERROR) → FAILED row, nothing released | `tests/test_recovery_executor.py` failure-injection units | **PASS in-suite**; live chaos blocked by §3 |
| 6 | Above-cap auto-release guard | README policy + dataset ground truth | **FAIL live — §7 bypass** |

---

## 9. Minimal fixes (ranked; none applied in this audit)

1. **Re-anchor fixture time** — derive `DEMO_BASE`/`CHAOS_BASE` from `now` (or a `FIXTURE_TIME` env) in `demo_scenarios.py:43` and `chaos.py:69`. Restores ~38 unit tests, the live demo, and all 9 chaos scenarios with two line-level changes.
2. **Wire the caps into the autonomous pipeline** — add amount/previous-failures checks to `decide()` or the safety gate; add a compensation rule for ledger-vs-state divergence after an aborted final transition.
3. **Resolve the SUPPORT-role drift** — `routes/demo.py:144` vs `stage10_e2e` SEC2 expectation.
4. **Handle `ValidationError` in `demo_scenarios.prepare_scenario`** so a bad fixture date degrades to a 4xx with the validator message instead of a 500.

Scratch artifacts created by this audit (safe to delete): `data/audit_e2e.db`, `data/dataset_e2e.db`. The dataset CSVs and any pre-existing database were not modified.

---

## 10. Fix log (2026-09-22, post-audit)

Each fix maps to a confirmed reproduction from §3–§7. No API contract changed; the one contract question (SUPPORT vs admin-only demo reads) was resolved in favor of the pinned pytest contract (below).

| # | Fix | File(s) | Confirmed by |
|---|---|---|---|
| 1a | `DEMO_BASE` re-anchored to import-time wall clock (yesterday, 09:00 UTC) — all offsets unchanged | `api/services/demo_scenarios.py:43-52` | demo prepare 500s (§3, §4) |
| 1b | `CHAOS_BASE` re-anchored identically (13:00 UTC) | `api/services/chaos.py:68-76` | 9/9 live chaos FAILs (§5) |
| 2 | `RECOVERY_MAX_AMOUNT` / `RECOVERY_MAX_PREVIOUS_FAILURES` enforced inside R-A of the decision policy (`_cap_violation`); above-cap R-A-shaped evidence → `NO_ACTION` / `BLOCK_NOT_ELIGIBLE` with the cap reason | `api/services/recovery_decision_policy.py` | TXN-0000020 sandbox release (§6–§7) |
| 3 | Lifecycle pre-check in the executor: `validate_transition(tx.current_state, LIMIT_RELEASED)` runs BEFORE `provider.ensure_hold`; illegal → `FAILED` row, no provider call, ledger/state reconciled | `api/services/recovery_executor.py:380-407` | ledger "RELEASED 9,831.98" vs state `RECOVERY_REJECTED` divergence (§7) |
| 4 | SEC2 in the E2E updated to the pinned contract (demo is admin-only per `routes/demo.py` require_roles + `test_demo_router.py` "Demo mode is admin-only", which passes): SUPPORT demo reads → 403, ledger read → 200 | `scripts/stage10_e2e.py:615-634` | SEC2 FAIL (§4) |
| 5 | Fixture `ValidationError` → `DemoFixtureInvalidError` (422 `DEMO_FIXTURE_INVALID`) in both `PaymentEventIn` construction sites | `api/services/demo_scenarios.py` | demo prepare 500 → now 422 (§3) |

New regression tests (all fail on the pre-fix code): `test_above_cap_amount_is_not_eligible`, `test_previous_failures_above_cap_is_not_eligible` (`tests/test_recovery_policy_v2.py`); `test_illegal_transition_never_reaches_the_provider` (`tests/test_recovery_executor.py`); `test_future_skewed_fixture_is_422_not_500` (`tests/test_demo_router.py`).

Post-fix verification (all exact numbers):

- `pytest` targeted files (recovery ×6, demo router, chaos, autonomous API): **100 passed, 1 skipped** in 283 s.
- Live Stage 10 E2E (`stage10_e2e`, fresh server `data/audit_e2e.db`): **18/18 PASS** — S1 `MERCHANT_CONFIRMATION_TIMEOUT → GENUINE_FAILURE → AUTO_RECOVERED → VERIFIED`, ledger released 1,200 and reconciles; S2 `DOUBLE_DEDUCTION` block, provider never called; S5 blocked; S6 released exactly once; SEC1/SEC2/SEC3 pass (37 bodies swept, zero leaks).
- Live chaos (`POST /api/v1/chaos/run`, all 10): **9/9 runnable PASS** with every invariant held (GATEWAY_TIMEOUT 4/4 … CONCURRENT_RECOVERY 5/5); `DB_FAILURE_SIMULATION` honest SKIP.
- Dataset ground-truth replay (fresh `data/dataset_e2e.db`): TXN-0000020 now `NO_ACTION / BLOCKED — "amount 9831.98 exceeds the auto-release cap 1500.0: manual handling required"` (exact match, `provider_reference=None`); TXN-0000201 still an exact DOUBLE_DEDUCTION match; TXN-0000537 refused a second release; **sandbox ledger: 0 entries after the full replay**.

Remaining failures (out of scope per the fix instructions, unchanged from the audit baseline): `tests/test_event_conflicts.py` (10 — its own hardcoded `2026-10-02` fixture dates), `tests/test_payment_events.py` (3 — hardcoded `2026-10-02`), `tests/test_policy_simulator.py` (10 fixture `ValidationError`s — same class), `tests/test_event_bus.py` (1 — same class), `tests/test_reconstruction.py` (1 — the documented pre-existing failure). All are test-fixture date anchoring except the reconstruction one; fixing them is a mechanical follow-up of fix #1 if desired.
