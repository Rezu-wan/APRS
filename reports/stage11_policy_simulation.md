# Stage 11 — Recovery Policy Simulator (11E)

**Module:** `api/services/policy_simulator.py` · **Route:** `POST /api/v1/policy-simulation/run` (SYSTEM/ADMIN only) · **Spec basis:** §12–13

**Theme context:** trust means policy changes are *measured* before they are made. The simulator tests recovery policies against stored evidence — it never touches money, transaction state, the live policy, or the executor. SANDBOX/simulation only.

## 1. Purpose

Re-evaluate a **set of versioned recovery-decision policies** against a corpus of transactions **on identical evidence**, and report flat, measurable per-policy differences:

- **would_release / would_block / would_manual_review / would_no_action** counts (`would_block` counts only terminal payment facts — `ALREADY_SUCCESS`, `ALREADY_RECOVERED`, `NEW_SUCCESSFUL_SETTLEMENT`, `DOUBLE_DEDUCTION`; uncertainty/non-eligibility counts as `would_no_action`);
- **gate_vetoes** — how often the REAL Stage-8 safety gate (`check_safety`, pure call, same evidence) would veto each policy's releases;
- **false_recovery / missed_recovery** — ground-truth scoring, only when the corpus has KNOWN outcomes (`ground_truth_available=true`, i.e. the demo corpus); otherwise both are reported as `null` — no invented numbers for arbitrary id lists;
- **provider_calls_avoided** — releases not taken (`blocks + manuals + no_actions`), i.e. provider calls the policy would have spared;
- **per-transaction decisions** — the full auditability detail: action, blocked reason, eligibility, gate allowed/vetoed per transaction.

Evidence is loaded **once per transaction and shared across policies** — the comparison isolates the policy, not the data. Unknown policy names are a hard error (route maps to 422); missing transaction ids are skipped, not invented.

## 2. Policy versioning contract

The registry (`POLICY_REGISTRY`) holds three entries. **The ACTIVE policy is never replaced** — `autonomous-v1` is wrapped, calling `api/services/recovery_decision_policy.decide` unchanged, never reimplemented.

| Version | Status | Behavior |
|---|---|---|
| `autonomous-v1` | **ACTIVE** (not experimental) | The real Stage-8 production policy, evaluated unchanged. |
| `manual-only-baseline` | experimental | Never acts autonomously: every genuine failure / recovery candidate goes to `MANUAL_REVIEW`, everything else `NO_ACTION`. |
| `autonomous-v2-experimental` | experimental | Delta from v1, **exactly and only** this: v1 releases on LOW *or* MEDIUM risk; v2 keeps LOW unconditional but releases MEDIUM risk **only when reconstruction confidence ≥ 0.6** (`V2_MEDIUM_RELEASE_MIN_CONFIDENCE`), else `MANUAL_REVIEW`. All other rules delegate to the real v1 policy unchanged. |

The v2 rationale, from the module docstring: MEDIUM-risk releases are exactly where evidence quality should gate autonomy; the delta was chosen because it is real, small, and measurable — the demo merchant-timeout chain carries confidence 0.71 (would still release) while a gateway-timeout chain carries 0.43 (would not), so corpora exercise both sides of the threshold.

## 3. Simulation purity guarantees

A run **never** touches the live policy, the executor, the payment provider, the sandbox ledger, transaction states, or the Digital Twin. Concretely:

- the active policy is **wrapped, never modified**;
- `autonomous_recovery.process_transaction` is **never called** — the module mirrors its evaluation ORDER (assessment → reconstruction → decide → safety gate) without any of its writes;
- `risk_engine.run_assessment` is used because it is pure: it reads events, computes, and reuses stored rows on fingerprint match but never inserts (`persist_assessment` / `record_anomaly_classified` are deliberately not called);
- `reconstruct_from_events` and `check_safety` are pure (the caller supplies all data; `existing_row=None` — no in-flight recovery rows are considered).

**The only write in the entire phase** is ONE best-effort `POLICY_SIMULATION` security-audit row, written by the route (not the module) after the run. The run payload itself carries `simulated: true`, `affects_live_policy: false`, and `code_versions` with `"executor": "not-invoked"`, `"verifier": "not-invoked"`.

## 4. The no-ranking rule (spec §13)

The run reports **flat per-policy metrics** and a numeric-only comparison table (`comparison: [{metric, per_policy}]`; latency excluded — timing is noise, not evidence). There is **no ranking, no "best policy" field, no recommendation**. Policy selection is a human decision; the simulator only makes the differences measurable. That is the theme in miniature: automate the measurement, never automate the trust.

## 5. Reproducibility: `dataset_fingerprint`

Every run records a sha256 over the sorted `(transaction_id, provider_event_id, event_type, status, event_timestamp.isoformat())` tuples of the whole corpus. Same corpus → same fingerprint → same deterministic metric values (latencies excluded), so runs are comparable across time and machines. The run also records `rule_version` / `model_version` observed on the corpus and the requested policy list.

## 6. Experiment framework

`scripts/stage11_experiment.py` is the driver for the 11H research experiments. It executes four experiments against the seeded demo corpus and writes raw results for later analysis:

| Exp | Question | Method |
|---|---|---|
| **E1** | What does the policy dimension look like? | Run the policy simulator over the full policy registry (`autonomous-v1`, `manual-only-baseline`, `autonomous-v2-experimental`) on identical evidence. |
| **E2** | What does the safety gate contribute? | **Honest scoping:** the gate is always on and not API-switchable, so this experiment measures the gate's observable contribution — per-policy `gate_vetoes` on identical evidence plus the chaos invariants that exercise gate behavior (e.g. `LATE_SETTLEMENT`). It is NOT a gate-off counterfactual; the report says so plainly. |
| **E3** | How does the pipeline behave under adverse evidence shapes? | Run the deterministic chaos catalog (see `reports/stage11_chaos_testing.md`) and collect invariants + verdicts. |
| **E4** | What are the observed latencies? | Per-experiment decision latencies (simulator `decision_latency_ms_avg`) and pipeline timings. Latencies are reported separately and excluded from comparisons. |

Outputs land under `reports/stage11/experiments/` (per-experiment config/summary files and metric JSON — see `experiments/*.json` for the actual values) plus a summary markdown. **Reproducibility statement:** the demo corpus is deterministic (fixed ids, fixed timestamps, no RNG), so re-running the framework on the same corpus yields the same `dataset_fingerprint` and identical non-latency metric values; only latencies and run ids differ.

No quantitative results are quoted in this document — see `reports/stage11/experiments/*.json` for the measured values.
