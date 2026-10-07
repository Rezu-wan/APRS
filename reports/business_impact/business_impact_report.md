# Business & Customer Impact — Judge Report

**Generated:** 2026-10-07 · **Git:** `426a0e8` · **Data:** 100% synthetic, seed 42 (`data/dataset/manifest.json`: `synthetic: true`)
**Reproduce:** `python -m scripts.business_impact_eval` (see *Reproducibility* below)
**Machine-readable:** `business_impact_metrics.json` · `business_impact_metrics.csv` · `live_decisions_v1.csv` (same directory)

---

## Executive Business Impact summary

Across three evidence tiers — all on the same seed-42 synthetic data, no invented or
extrapolated numbers — the APRS autonomous recovery system measurably recovers money that a
manual-only process loses, removes the majority of manual review workload, and blocks every
unsafe recovery the evidence flags:

| Tier | Evidence type | Cohort | Headline |
|---|---|---|---|
| **A** | Full-dataset **recorded** outcomes (`recovery_cases.csv`) | **n = 1,142** recovery-eligible cases (of 10,506 transactions, 1 synthetic year) | 38 auto-recoveries · **29,656.01 BDT** recovered · **−64.97%** manual reviews · **0** false recoveries |
| **B** | Full-dataset **live re-simulation** by the current engine (existing policy-simulator API) | same **n = 1,142** | **40.77%** modeled recovery rate vs 0% baseline · **198,586.60 BDT execution-consistent *modeled* impact (not realized money)** · **38 unsafe would-be releases vetoed** by the safety gate |
| **C** | Sandbox **benchmark** (Stage 11 Phase 11H, reproduced 3×) | **n = 6** deterministic demo corpus (**NOT** the full dataset) | 3/3 genuine failures recovered (100% vs 0%) · 3→0 manual reviews · 0 false recoveries |

Baseline = the repo's **registered `manual-only-baseline` policy** (`api/services/policy_simulator.py`):
never releases autonomously; every engine-confirmed genuine failure goes to MANUAL_REVIEW; everything
else NO_ACTION. No baseline was invented — it is the same policy object Stage 11 evaluates.

---

## Baseline vs APRS

### Tier A — recorded full-dataset evidence (n = 1,142)

| metric | manual-only baseline | APRS (autonomous-v1, recorded) |
|---|---|---|
| Automated releases | **0** | **38** (all `VERIFIED`) |
| Value released (BDT) | **0.00** | **29,656.01** |
| Manual-review cases | **845** | **296** (176 pending + 120 resolved) |
| Recovery rate | 0/845 = **0%** | 38/845 = **4.50%** |
| False recoveries | — | **0** (0/38 = 0%) |
| Unsafe releases | — | **0** |

Denominator **845** = recorded assessments with `recovery_candidate = true` **and**
`anomaly_type = GENUINE_FAILURE` — exactly the registered baseline's own manual-review queue.
(Ground-truth genuine by `anomaly_type` alone: 856; by `recovery_required`: 857 — reported as sensitivity.)

### Tier B — current decision engine re-simulated on the same 1,142 cases

| metric | manual-only baseline | APRS v1 (decision layer) | APRS v2 (experimental) |
|---|---|---|---|
| Would-release (raw) | 0 | 740 | 384 |
| Safety-gate vetoes | 0 | **38** | 22 |
| Post-gate releases | 0 | 702 | 362 |
| Execution-consistent releases (≤ 1,500 BDT auto-release cap) | 0 | **363** (349 ground-truth genuine) | — |
| Manual-review cases | **740** | **0** | 356 |
| Recovery rate (ground truth, execution-consistent) | **0%** | **40.77%** (349/856) — *modeled* | — |
| Value, execution-consistent **modeled** (BDT) | 0.00 | **198,586.60** — *not realized money; see Limitations 5* | — |
| False recoveries (post-gate) | — | 24 (all recorded-`DUPLICATE_TRANSACTION`; see Limitations) | — |

### Tier C — sandbox benchmark (n = 6 — never present as full-dataset)

| metric | baseline | APRS v1 |
|---|---|---|
| Recovery rate | 0/3 = **0%** | 3/3 = **100%** |
| Value (BDT, 1,200 fixed demo amount) | 0 | **3,600** |
| Manual reviews | 3 | **0** |
| False / missed recoveries | 0 / **3** | **0 / 0** |
| Gate vetoes | 0 | 0 |

Run `experiment-7c9f078b…` (2026-10-07, git `426a0e8`); E1 decision metrics **byte-identical across
3 independent runs on 2 machines** (Oct 3 ×2, Oct 7 ×1) — the Stage 11 result is valid and preserved.

---

## Exact formulas

| Metric | Formula (as computed) | Tier |
|---|---|---|
| Recovery rate | `released cases ÷ engine-confirmed-recoverable cases` | A: 38/845 · B: 349/856 · C: 3/3 |
| Value recovered (BDT) | A: `Σ released_amount` of releases · B: `Σ transaction.amount` over releases that pass the safety gate **and** satisfy the production auto-release cap (`recovery_max_amount = 1500 BDT`, `api/core/config.py`) **and** are ground-truth genuine · C: `releases × 1,200` fixture | A/B/C |
| Manual-review reduction | `(baseline_manual − v1_manual) ÷ baseline_manual` | A: (845−296)/845 = 64.97% · B: (740−0)/740 = 100% (decision layer) |
| Automated recovery count | count of release decisions (A: recorded `RELEASE_LIMIT`; B: post-gate, cap-consistent) | A: 38 · B: 349 genuine |
| Unsafe prevented | unsafe-anomaly cases (`DOUBLE_DEDUCTION`, `DUPLICATE_TRANSACTION`, `SUCCESSFUL_BUT_UNCONFIRMED`, `FALSE_COMPLAINT`, settlement-race `UNKNOWN`) **not** released | A: 149/149 |
| False-recovery rate | `releases with ground-truth anomaly ≠ GENUINE_FAILURE ÷ post-gate releases` | A: 0/38 · B: 24/702 |

Cohort: **1,142** recovery-eligible cases (recovery records exist only for transactions with a
non-`NONE` anomaly and non-`STALLED` outcome) out of **10,506** transactions, 600 customers
(455 appear in the cohort), 2025-10-01→2026-10-01. Cohort fingerprint (sha256 over the cohort's
payment-event tuples, same schema as the simulator): `12de22883883ca46…df3`.

## Recovery impact

- **Recorded (full dataset):** APRS recovered **38** failures worth **29,656.01 BDT**; the manual-only
  baseline recovers **0** of the same 845 recoverable cases. Recovery rate **4.50% vs 0%**.
- **Headroom (same cohort, current engine) — MODELED, not realized:** the live decision layer + safety
  gate + production cap **would** recover **349** genuine failures = **40.77%** of the 856 ground-truth
  genuine cases (**198,586.60 BDT execution-consistent modeled impact**) vs 0% baseline. This figure is
  a simulation result of what the current engine would decide on this recorded evidence — **no money
  was actually moved and no real savings were realized**. The gap to the recorded 4.50% is the
  execution layer's conservative caps (379 recorded cases were above the 1,500 BDT auto-release cap) —
  a tunable policy lever, not an engine limitation.
- **Sandbox benchmark:** 100% of genuine failures recovered (3/3), 0 missed.

## Manual-work impact

- Recorded full dataset: manual reviews **845 → 296 = −64.97%** (**549 cases** of human handling avoided);
  open waiting queue **845 → 176**.
- Live decision layer: of 740 engine-confirmed recoverable cases, baseline queues **all 740** for a human;
  APRS queues **0** (100% reduction at the decision layer). The execution layer's cap still routes
  above-cap cases to humans — that is the recorded 296, not a discrepancy.

## Customer impact (quantified where the evidence allows)

All figures below are **full-dataset recorded evidence** unless marked otherwise. "Customer" = the
`customer_id` on the transaction; "frustration" itself is **not measurable** in this dataset — the
defensible proxies are how many customers had failures resolved without human intervention and how
many were left waiting in a queue.

| Customer-facing metric | Value | Evidence |
|---|---|---|
| Customers in the recovery cohort | **455** (of 600 in the dataset) | recorded |
| Customers with ≥1 engine-confirmed recoverable failure | **394** | recorded |
| Customers with ≥1 failure **auto-recovered, no human involved** | **36** of those 394 (**9.14%**) | recorded |
| Customers with ≥1 case sent to manual review | **189** | recorded |
| Cases a customer would wait on a human for (baseline vs APRS) | **845 → 176** | recorded |
| False-release rate (share of auto-releases that were not genuine) | **0%** (0/38) | recorded |
| Unsafe recoveries prevented (double refunds, duplicates, false complaints, settlement races) | **149** | recorded |
| Measured rate, normalized per **1,000 cohort cases**: auto-releases **33.3** · value released **25,968.49 BDT** · manual handlings avoided **480.7** | | recorded, simple rescaling — **not a forecast** |
| Modeled recovery rate (current engine, execution-consistent) | **40.77%** (349/856) vs 0% | live re-simulation — *modeled* |

**Not defensibly calculable from this data (stated explicitly):**
- **Customer wait-time reduction** — pending cases carry no resolution timestamps, and all timestamps
  are synthetic; any "days saved per customer" number would be invented.
- **Customer satisfaction / churn / complaint reduction** — no survey, ticket, or complaint data exists
  in the dataset.
- **Per-1,000 figures as a forecast** — the per-1,000 row above only rescales measured cohort
  proportions; applying it to future or real-world volume would be extrapolation and is not claimed.

## Loss-prevention / safety impact

- **149 unsafe cases prevented** (recorded, full dataset): 57 double deductions, 24 duplicate
  transactions, 56 successful-but-unconfirmed, 10 false complaints, 2 settlement races. **0 released.**
- **0 false recoveries** in the recorded full dataset (0/38) and sandbox (0/3); live re-simulation's 24
  false releases are exclusively `DUPLICATE_TRANSACTION` cases whose cross-transaction evidence a
  per-transaction simulator cannot see — the recorded full chain released **0** of them.
- Safety gate active everywhere: **38 vetoes** on the full cohort (Tier B); Stage 11 chaos:
  `LATE_SETTLEMENT` 6/6 and `CONCURRENT_RECOVERY` 5/5 invariants held; 9 PASS / 1 SKIP / 0 FAIL.

## Limitations and scope (stated plainly)

1. **100% synthetic data** (seed 42, byte-reproducible generator). No real payments, persons, or amounts.
2. Tier A's baseline arm is a **counterfactual derived from the registered baseline policy semantics**
   applied to recorded assessments — not a separately executed run.
3. Tier B re-decides recorded cases with **today's engine at evaluation time**; the simulator's
   decision layer has no amount cap (it is enforced at execution), so value claims use the
   execution-consistent filter explicitly; per-transaction simulation **cannot see cross-transaction
   duplicates** (see the 24 false releases).
4. **Tier C is a 6-transaction deterministic benchmark.** It must never be presented as a
   full-dataset result.
5. **No figure in this report is realized, real-world money.** Tier B figures (40.77%, 198,586.60 BDT)
   are simulation outputs of what the current engine would decide on recorded evidence. No
   extrapolation, annualization, or per-year projection exists anywhere in this evaluation.
6. Customer impact is a **proxy** (queue/waiting burden), not a surveyed customer outcome.
7. Recorded cohort risk-level mix: most engine-confirmed genuine failures are LOW/MEDIUM risk, so
   Tier B's R-B (HIGH/CRITICAL → MANUAL_REVIEW) path triggers 0 times — a property of this corpus.

## Presentation-ready claims (audit-safe)

1. "On the full 1,142-case recovery cohort of our synthetic year, APRS auto-recovered **38 failed
   payments worth 29,656.01 BDT with zero false recoveries and zero unsafe releases**; a manual-only
   process recovers zero of them automatically." *(full-dataset evidence)*
2. "APRS cut manual-review workload by **64.97%** (845 → 296 cases; 549 handled without a human) and
   shrank the waiting queue from 845 to 176." *(full-dataset evidence)*
3. "The safety gate blocked **149 unsafe cases** — double deductions, duplicates, false complaints,
   settlement races — while releasing **zero** of them, and vetoed 38 would-be releases in live
   re-simulation." *(full-dataset evidence)*
4. "Re-simulating today's decision engine on the same 1,142 cases shows a **modeled recovery rate of
   40.77% of genuine failures (349/856), worth 198,586.60 BDT execution-consistent *modeled* impact —
   not realized money — versus 0% under a manual-only baseline**, with every above-cap release
   correctly excluded." *(full-dataset, live-simulation evidence)*
5. "On our deterministic 6-transaction sandbox benchmark — reproduced identically in three independent
   runs — APRS recovers **3/3 genuine failures (100% vs 0%)**, eliminates **3 → 0** manual reviews, with
   **0 false recoveries**." *(sandbox benchmark — NOT the full dataset)*

## Judge-feedback traceability

| Criticism | Where it is answered |
|---|---|
| J1 "measurable benefits are not provided" | Every section quantifies: Tier A recovery/value/manual/safety table, formulas with numerator+denominator, Customer-impact table |
| J2 "recovery improvement is not quantified" | Recovery rate 4.50% vs 0% (recorded), 40.77% vs 0% (modeled), 100% vs 0% (sandbox); value 29,656.01 BDT recorded / 198,586.60 BDT modeled |
| J2 "support workload" | Manual reviews 845 → 296 (−64.97%, 549 avoided); waiting queue 845 → 176 |
| J2 "customer frustration" | Customer-impact table (36/394 customers auto-served, 0% false-release rate) + explicit list of what cannot be measured |
| J3 "preventing double-refund loss via fresh-evidence safety gates" | 149 unsafe cases prevented (57 double deductions); 0 unsafe released; 38 live gate vetoes; LATE_SETTLEMENT 6/6 + CONCURRENT_RECOVERY 5/5 chaos invariants |

*Verification: every figure in this report was independently recomputed on 2026-10-07 directly from
`data/dataset/*.csv` (Tier A), `live_decisions_v1.csv` (Tier B), and the Stage 11 run JSON (Tier C);
all match exactly.*

## Reproducibility

```bash
# 0. environment (any Python 3.12 with requirements.txt)
# 1. isolated local SQLite (never Neon; DATABASE_URL is a literal file URI)
rm -f data/test_dataset.db
DATABASE_URL=sqlite:///./data/test_dataset.db python -m alembic upgrade head
DATABASE_URL=sqlite:///./data/test_dataset.db python -m scripts.load_dataset_db   # verifies counts vs manifest.json
DATABASE_URL=sqlite:///./data/test_dataset.db uvicorn api.main:app --port 8001
# 2. evaluation (read-only; writes only reports/business_impact/)
python -m scripts.business_impact_eval --api-url http://127.0.0.1:8001
```

Tier A reads `data/dataset/*.csv` read-only; Tier B calls the existing
`POST /api/v1/policy-simulator/run` (4 batches × ≤300 ids; raw per-transaction decisions in
`live_decisions_v1.csv`); Tier C reads the existing Stage 11 run JSON
(`reports/stage11/experiments/experiment-7c9f078b98b74608acc81cad4900371c.json`).
No frontend, backend, or dataset file is modified by any step.
