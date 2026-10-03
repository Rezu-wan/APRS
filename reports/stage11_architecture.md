# Stage 11 — Architecture Report

**Date:** 2026-10-03 · **Branch:** `frontend` · Baseline: see `reports/stage11/baseline.md`

**Theme (unchanged):** Automate recovery without automating trust. Everything in Stage 11 is SANDBOX/simulation only — no real banking, no real funds, ever.

Stage 11 adds an event-driven extension and four advisory intelligence surfaces **around** the recovery pipeline. It deliberately does **not** change the pipeline itself.

## 1. What changed at the architecture level

### 1.1 The event bus (Phase 11A)

New: `api/services/eventbus/base.py` (contracts) and `api/services/eventbus/in_memory.py` (the only shipped implementation).

- `EventBus` ABC with four operations: `publish`, `subscribe`, `replay`, `close`.
- `EventEnvelope` is a **frozen** dataclass — immutability is contractual. It carries domain time (`event_timestamp`) separately from processing time (`created_at`), plus `correlation_id` (defaults to `transaction_id`), `causation_id`, `provider_event_id`, and `schema_version` (pinned `ENVELOPE_SCHEMA_VERSION = "1"`). `payload` is copied on construction and must be treated as immutable by all parties.
- `InMemoryEventBus` guarantees:
  - **Idempotent per-subscriber delivery** — a bounded per-subscriber seen-set (last 50,000 `event_id`s) silently drops duplicate delivery, so a replayed provider event cannot cause duplicate business effects. The seen-mark happens atomically under the lock *before* dispatch, so two concurrent publishes of the same `event_id` cannot both invoke the handler.
  - **Subscriber-error isolation** — a failing subscriber is logged (`payment_recovery.eventbus`, `event_id` + subscriber name only) and never breaks the publisher or other subscribers. `publish` must never raise because of a handler failure.
  - **Bounded in-memory replay store** — a deque capped at 10,000 envelopes (oldest dropped), optional `transaction_id` filter.
  - **Close semantics** — after `close()`, subscribers are cleared and `publish` is a documented no-op.
- Selection is by settings name (`event_bus = "in_memory"`; unknown names are a hard configuration error), mirroring the payment-provider pattern.

**The honesty note (load-bearing):** the bus is an in-process notification seam, **not a message broker**, and its replay store is **NOT durable**. The durable event record remains the database tables `payment_events` and `digital_twin_events`. Kafka/Redis/Postgres adapters can slot in behind the same `EventBus` ABC without touching any subscriber or publisher.

### 1.2 Correlation on payment events (migration `c7e1f2a93b84`)

Migration `20261003_0430-c7e1f2a93b84_payment_event_correlation.py` adds three nullable columns to `payment_events`:

| Column | Semantics |
|---|---|
| `correlation_id` (String 64) | Groups related events. Backfilled in one `UPDATE` from `transaction_id`, and new events carry `correlation_id = transaction_id`. |
| `causation_id` (String 64) | The `event_id` of the event that caused this one. **Reserved**: provider-observed events have no internal cause, so it stays NULL for all events today. It exists so future engine-caused events can express causality without another migration. |
| `schema_version` (String 8) | Event schema version for forward-compatible consumers. |

### 1.3 The extended pipeline

The core chain is untouched. Stage 11 hangs four read-only intelligence surfaces and the bus off the same spine:

```
                 ┌──────────────────────────────────────────────────┐
                 │            payment_events / digital_twin_events  │   ← DURABLE record (unchanged)
                 └───────────────┬──────────────────────────────────┘
                                 │ (bus publication; InMemoryEventBus is a notification
                                 ▼  seam, never a source of truth)
                        ┌──────────────────┐
   ADVICE (read-only)   │   Event Bus ABC  │
  ┌──────────────────┐  └──────────────────┘
  │ 11C Temporal twin        │ 11C Behavioral signals   │ 11D Relationship graph  │ 11G Metrics registry │
  │ state_at(T)              │ behavioral-v1            │ relationship-v1         │ counters/gauges/lat  │
  └──────────┬───────────────┴───────────┬──────────────┴───────────┬─────────────┴──────────┬─────────┘
             │          all ADVISORY — none of these feed policy, gate, or actions │
             ▼                                                                      ▼
  transaction → reconstruction (SAME pure engine) → risk assessment → decision policy
             → safety gate (fresh evidence) → executor (idempotency) → verifier → sandbox provider
                                                                     (UNCHANGED from Stage 8/9)
  + 11E policy simulator (pure, read-only re-evaluation of versioned policies)
  + 11F chaos runner (drives the REAL pipeline, asserts invariants)
```

Every Stage 11 surface is **ADVISORY**: signal → deterministic rules → policy → safety gate, unchanged. The behavioral layer states it explicitly ("does NOT feed the recovery policy or the safety gate"); the relationship layer states it explicitly ("evidence for staff eyeballs, nothing more"); the temporal layer is read-only with zero writes; the metrics registry only observes.

### 1.4 The DO-NOT list that held

| Standing prohibition | Status after Stage 11 |
|---|---|
| No second Digital Twin | Held — `state_at` reuses `get_timeline` on the single twin; nothing forks it. |
| No second reconstruction engine | Held — temporal queries call the SAME `reconstruct_from_events`; the simulator calls it pure. |
| No second decision policy at runtime | Held — the active policy is wrapped, never replaced; `autonomous-v2-experimental` exists only inside the simulator. |
| ML/GenAI never authorize a recovery | Held — ML informs risk levels only; the policy and gate remain deterministic rules; ML unavailability degrades to rules-only. |
| No endpoint fakes a recovery | Held — chaos scenarios go through the real `process_transaction`; demo `prepare` never processes. |
| Bus is never a source of truth | Pinned in the ABC docstring. |

## 2. Phase table (11A–11H)

| Phase | Scope | Where |
|---|---|---|
| 11A | Event bus: `EventBus` ABC, frozen `EventEnvelope`, `InMemoryEventBus` (idempotent delivery, error isolation, bounded replay); correlation/causation/schema_version migration | `api/services/eventbus/`, migration `c7e1f2a93b84` |
| 11B | Temporal digital twin: `state_at(db, tx, T)` — historical reconstruction from pre-T evidence only | `api/services/temporal.py` |
| 11C | Online intelligence: explainable behavioral signals (behavioral-v1), read-only, UNKNOWN when history is insufficient | `api/services/behavioral.py` |
| 11D | Relationship graph: five entity types over existing tables, six explainable signals (relationship-v1) | `api/services/relationship.py` |
| 11E | Policy simulator: versioned policy registry, flat comparison, simulation purity | `api/services/policy_simulator.py` |
| 11F | Chaos & safety: 10-scenario deterministic catalog over the REAL pipeline, invariant-based verdicts | `api/services/chaos.py` |
| 11G | Observability: in-process metrics registry (counters/gauges/latencies), `/metrics` endpoint, bounded path classes | `api/services/metrics.py` |
| 11H | Research evaluation: experiment framework over the demo corpus, honest mapping to the spec's research configurations | `reports/stage11_research_evaluation.md` |

Grounding, plan, and baseline measurements for all phases: see `reports/stage11/baseline.md`.
