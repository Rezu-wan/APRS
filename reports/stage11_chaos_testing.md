# Stage 11 — Chaos & Safety Testing (11F)

**Module:** `api/services/chaos.py` · **Routes:** `POST /api/v1/chaos/run` (SYSTEM/ADMIN), `GET /api/v1/chaos/scenarios` · **Spec basis:** §15

**Theme context:** trust in a safety-gated recovery engine must be *tested*, not asserted. Every chaos scenario is SANDBOX/simulation only, drives the REAL pipeline, and fabricates nothing.

## 1. The non-negotiable invariants (spec §15) and where each is enforced

| Invariant | Enforced by |
|---|---|
| A recovery releases funds **at most once** (exactly-once) | `tests/test_recovery_concurrency_hard.py` (threaded exactly-once); chaos `released_exactly_once` / `one_recovery_row` invariants |
| Fresh evidence **blocks** stale releases | `LATE_SETTLEMENT` chaos scenario; `blocked_by_fresh_evidence_gate` invariant; Stage 8 E2E |
| Idempotency: duplicate events / duplicate process calls produce **one** effect | `DUPLICATE_EVENT` chaos scenario + `tests/test_event_bus.py` bus-level idempotency (per-subscriber seen-set) |
| Transaction state moves only through the state machine, monotonically recorded | `tests/test_state_integrity.py`; chaos `tx_limit_released` / `tx_never_limit_released` invariants |
| Provider is never called when the gate blocks | `LATE_SETTLEMENT`, `PROVIDER_TIMEOUT/ERROR` invariants (`provider_reference_null_on_row`, `ledger_not_released`) |
| Sandbox ledger survives restarts and stays reconciled | `tests/test_sandbox_persistence.py` (write-through `sandbox_ledger_entries`; restore-on-startup lifespan hook) |
| The past is never rewritten by future evidence | temporal isolation tests (`tests/test_temporal.py` — future-isolation invariant) |
| Reconstruction is order-independent | `OUT_OF_ORDER_EVENT` chaos scenario (`reconstruction_order_independent`) |
| A failing component never corrupts the pipeline | bus subscriber-error isolation (`tests/test_event_bus.py`); chaos invariants never raise — they record `held=False` |
| ML/GenAI unavailability degrades to rules-only, never to unsafe | `tests/test_ml_genai_failure_modes.py` |

## 2. The ten scenarios

All scenario ids are pinned (`CHAOS-<SCENARIO>-1`), timestamps derive from a fixed `CHAOS_BASE` (no RNG, no wall clock in the event chains), and every scenario **purges its own fixture first** — DB child rows in FK-safe child-first order, the persisted `sandbox_ledger_entries` row, and the in-memory provider entry (`MockPaymentProvider.purge_transactions`, which also restores the held amount to the simulated limit and drops provider-level idempotency replays), so reruns execute fresh.

| # | Scenario | Injects | MUST happen | Key invariants asserted |
|---|---|---|---|---|
| 1 | `GATEWAY_TIMEOUT` | Genuine failure with terminal gateway-TIMEOUT evidence | Auto-recover exactly once → `AUTO_RECOVERED` / `VERIFIED` | `one_recovery_row`, `provider_reference_present`, `ledger_released_once`, `tx_limit_released` |
| 2 | `GATEWAY_ERROR` | Same, terminal gateway-ERROR evidence | Same as above | Same release-invariant set |
| 3 | `MERCHANT_TIMEOUT` | The canonical merchant-confirmation-timeout chain | Auto-recover exactly once | Same release-invariant set |
| 4 | `LATE_SETTLEMENT` | `SETTLEMENT_CONFIRMED` ingested AFTER the risk assessment (via the real ingestion service) | The fresh-evidence gate BLOCKS: `RECOVERY_BLOCKED` / `BLOCKED` with a layered block code (`NEW_SUCCESSFUL_SETTLEMENT` family) | `late_settlement_ingested`, `blocked_by_fresh_evidence_gate`, `provider_reference_null_on_row`, `ledger_not_released`, `tx_never_limit_released` |
| 5 | `DUPLICATE_EVENT` | The same event batch ingested twice | Duplicates counted, not re-inserted (`created=0, duplicates=N` on the second ingest); recovery releases exactly once | `second_ingest_all_duplicates` + release invariants |
| 6 | `OUT_OF_ORDER_EVENT` | The merchant-timeout chain ingested in REVERSED order | Reconstruction is order-independent and equals the in-order reconstruction; auto-recover exactly once | `reconstruction_order_independent` (root cause + all four stage statuses) + release invariants |
| 7 | `PROVIDER_TIMEOUT` | One-shot simulated provider TIMEOUT at release time (server-side `set_failure` hook) | Executor records a `FAILED` row with `failure_reason=PROVIDER_TIMEOUT`; nothing released; `RECOVERY_BLOCKED` / `FAILED` | `row_failed_with_provider_code`, `failure_mode_cleared` (injection disarmed in `finally`) + nothing-moved invariants |
| 8 | `PROVIDER_ERROR` | One-shot simulated provider ERROR at release time | Same as #7 with `PROVIDER_ERROR` | Same as #7 |
| 9 | `CONCURRENT_RECOVERY` | 10 threads race `process_transaction` on one transaction through the shared provider — **through the real service with per-thread `SessionLocal` sessions**, barrier-synchronized, SQLite lock-retry | Exactly one release, one provider reference, exactly one non-replay `AUTO_RECOVERED` decision | `no_thread_errors`, `exactly_one_provider_reference`, `released_exactly_once` (ledger AND row-sum reconcile to the tx amount), `exactly_one_non_replay_decision`, `tx_limit_released` |
| 10 | `DB_FAILURE_SIMULATION` | — | **Honest SKIP** | none — verdict `SKIP` with an explanatory note |

### Special notes

- **`DB_FAILURE_SIMULATION` is an honest SKIP.** A database failure cannot be injected through the live service without monkeypatching a running session (the failure lives below the service boundary). The path IS covered by unit tests: `tests/test_recovery_concurrency_hard.py` (IntegrityError race paths) and executor failure-injection units. The runner returns verdict `SKIP` with that note rather than pretending coverage it does not have.
- **Provider failure injection** is the server-side one-shot hook (`MockPaymentProvider.set_failure`), always disarmed in a `finally` block; the `failure_mode_cleared` invariant asserts the disarm.
- **Invariants never raise.** A violated invariant records `held=False` and flips the scenario verdict to `FAIL`; the endpoint still returns 200 with the honest `FAIL` result. A scenario that crashes outright is rolled back and reported as `FAIL` with the exception note — chaos reports, it never 500s.

## 3. Rerun safety and the no-bypass rule

- **Purge-first, always:** every scenario purges its own fixture before running; the bulk `purge_chaos_rows` helper (prefix-scoped, used by the available-limit restoration test) mirrors the same FK-safe order plus `provider.purge_transactions` (ledger snapshot filtered by prefix) and replay-drop, so a full cycle restores the simulated provider limit exactly.
- **No endpoint fakes a recovery.** Every scenario that touches money goes through `autonomous_recovery.process_transaction` — the real decision policy, the real safety gate, the real executor and verifier, the real sandbox provider. Chaos varies only the **evidence shape** and the **environment** (arrival order, duplication, provider failure, concurrency); it never fabricates a successful recovery outcome.
