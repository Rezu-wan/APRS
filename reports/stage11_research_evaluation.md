# Stage 11 — Research Evaluation (11H)

**Spec basis:** §12–13 (policy simulation), §15 (invariants), §20 (metrics taxonomy), §21 (reproducibility) · **Experiments:** `scripts/stage11_experiment.py` → `reports/stage11/experiments/`

**Theme context:** the research evaluation exists to *measure* the system honestly, not to market it. Every claim below is scoped to what this SANDBOX system can actually run.

## 1. The research question (spec, verbatim intent)

> *Can an autonomous, safety-gated recovery policy recover failed payment transactions as effectively as manual recovery, while making fewer errors and fewer provider calls — and what is the measurable contribution of each safety layer?*

## 2. The four system configurations, mapped honestly

The spec defines four configurations. This is how they map onto what this system can **actually run** — including where they cannot:

| Spec configuration | Realization in this system | Honesty note |
|---|---|---|
| **Baseline** (manual recovery) | Simulated by the `manual-only-baseline` policy in the policy simulator: never acts autonomously; every genuine failure goes to `MANUAL_REVIEW`. | This is a *policy* baseline, not a human-operations study. |
| **System A** (ML-supported decisions) | The real pipeline: ML features feed risk levels; the deterministic policy and safety gate remain the authority. | The engine already degrades to **rules-only when ML is unavailable** (unit-tested in `tests/test_ml_genai_failure_modes.py`). But the API **cannot force rules-only per request** — there is no per-request ML switch — so a live A-vs-C ablation over the API is not possible. The experiment framework therefore measures the **policy dimension (E1)** and the **gate dimension (E2: measured vetoes + chaos invariants)** and says so plainly; the ML ablation exists at unit-test level only. |
| **System B** (rules-only decisions) | Same as above minus ML: exercised only via the ML-unavailable degradation path in unit tests. | Not runnable as a separate live configuration. |
| **System C** (full pipeline with safety gate) | The real full pipeline — decision policy → fresh-evidence safety gate (always authoritative) → executor → verifier → sandbox provider. Exactly what chaos (E3) and the demo corpus exercise. | This is the only configuration that moves (simulated) money. |

## 3. Metrics taxonomy (spec §20) → where each is actually measured

| Spec metric family | Measured by |
|---|---|
| Recovery outcomes (recovered / blocked / failed / manual) | Simulator run (`would_release / would_block / would_manual_review / would_no_action`, `gate_vetoes`); live counters via the `/metrics` endpoint (`recovery_attempts_total`, `recovery_success_total`, `recovery_blocked_total`, `recovery_failed_total`, `manual_review_total`, `safety_gate_blocks_total`) |
| False recovery / missed recovery | Simulator run — `false_recovery` / `missed_recovery`, computed only when `ground_truth_available` (demo corpus); `null` otherwise |
| Provider call efficiency | Simulator `provider_calls_avoided`; live `provider_calls_total` / `provider_errors_total` counters |
| Reliability under adverse conditions | Chaos invariants (E3) — exactly-once, fresh-evidence blocking, order independence, duplicate handling; unit tests for DB-failure and concurrency paths |
| Latency / performance | E4 latencies (simulator `decision_latency_ms_avg`; pipeline latencies via `/metrics` latency series) — reported separately, excluded from comparisons |
| Explainability / auditability | Temporal query (`excluded_event_count`, `uncertainty_note`), behavioral signals (per-signal `basis`, `description`, honest `UNKNOWN` levels), relationship graph (evidence ids + descriptions), request ids + `security_audit` trail |

Measured values live in `reports/stage11/experiments/` — see `experiments/*.json`. No numbers are quoted in this document that are not read from those outputs.

## 4. Reproducibility contract (spec §21)

| Spec field | Where it is recorded |
|---|---|
| Dataset identity | `SimulationRun.dataset.dataset_fingerprint` (sha256 over sorted corpus event tuples) + `source` (`demo` / `transaction_ids`) + explicit `transaction_ids` |
| Code versions | `SimulationRun.code_versions`: requested `policies`, corpus-observed `rule_version` and `model_version`; `executor` / `verifier` pinned as `"not-invoked"` (simulation purity) |
| Simulation marker | `simulated: true`, `affects_live_policy: false`, `run_id`, `generated_at` |
| Chaos runs | Deterministic by construction: fixed `CHAOS_BASE` timestamps, pinned ids `CHAOS-<SCENARIO>-1`, no RNG; scenario catalog order fixes start offsets |
| Experiment outputs | Per-experiment config + metric files under `reports/stage11/experiments/` and a summary markdown |

**Reproducibility statement:** the demo corpus is deterministic (fixed ids, fixed timestamps, no RNG in the builders), so the same corpus yields the same `dataset_fingerprint` and identical non-latency metric values on re-run; latencies and run ids are the only expected variance.

## 5. LIMITATIONS (read this before quoting any number)

These are deliberate, documented boundaries of the evaluation — stated prominently, not footnoted:

1. **Synthetic data only.** All data is generated by this project's own simulators and demo seed. There is no real banking data — now, and none is claimed. Nothing here is evidence about real payment networks, real banks, or real customers.
2. **Small corpus.** The demo corpus is 6 transactions (DEMO-S1..S6). All ground-truth metrics (false/missed recovery) are computed over this tiny, corpus-specific set and must not be generalized; on arbitrary id lists they are reported as `null` rather than invented.
3. **Sandbox provider only.** The provider is `MockPaymentProvider` — an in-memory, write-through-persisted simulation. "Release" means a ledger entry in a sandbox, nothing else.
4. **In-process infrastructure.** The metrics registry and the rate limiter are per-process and in-memory; they reset on restart and do not aggregate across workers. The bus is in-process with a bounded, non-durable replay store; the durable record is the database.
5. **No clean gate-off ablation.** The safety gate is always on and not API-switchable, so E2 measures the gate's *contribution* (vetoes + chaos invariants), not a gate-on vs gate-off counterfactual.
6. **ML ablation not possible via the API.** Rules-only mode exists only as the automatic ML-unavailable degradation path (unit-tested); the experiment framework cannot produce a live ML-on vs ML-off comparison.
7. **No real-world performance claims of any kind.** Latencies are measurements of this machine, this stack, this corpus. They support no production capacity, scalability, or accuracy claim — and no claim that autonomous recovery is safe or effective outside this sandbox.

The honest summary: this system demonstrates a *measurable, reproducible, safety-gated* recovery pipeline in a sandbox, with every evaluation artifact inspectable. Whether the approach transfers to real payment operations is a question this evaluation, by design, cannot answer.
