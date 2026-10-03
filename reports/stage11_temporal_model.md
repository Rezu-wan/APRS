# Stage 11 — Temporal Digital Twin (11B)

**Module:** `api/services/temporal.py` · **Route:** `GET /api/v1/transactions/{transaction_id}/state-at?timestamp=...` (SYSTEM/ADMIN/SUPPORT)

**Theme context:** automating recovery must not automate trust — and reconstructing the past must not rewrite it. Everything here is SANDBOX/simulation only.

## 1. The question it answers

> **What did the system know at time T?**

Not "what does the system know now, projected backwards" — but the honest, evidence-limited picture as of an arbitrary instant `T`. This matters for incident review, dispute walkthroughs, and research: a decision made at 14:30 must be judgeable against only the evidence that existed at 14:30.

## 2. Reconstruction rules

`state_at(db, transaction, at)` performs exactly three reads and zero writes:

1. **Event filter:** all `payment_events` rows for the transaction are loaded, then partitioned by `event_timestamp <= T`. Only the `observed` set is passed to `reconstruct_from_events` — **the SAME deterministic reconstruction engine as the live path**. Stage statuses, root cause, confidence, and `missing_events` therefore reflect only pre-T knowledge. There is no second engine.
2. **`state_then` from the twin timeline:** `get_timeline` is filtered to events with `timestamp <= T`; the LAST such event's `new_state` is the historical state. **The mutable `Transaction.current_state` is never used** — it is the *current* state and would be an anachronism inside a historical view.
3. **Birth state:** with no twin events at or before `T`, the honest answer is `INITIATED` (the state machine's birth state), with `last_twin_event_type = None`.

Purity: `state_at` performs **no writes** — no twin appends, no risk assessments. Like the GenAI explanation path, it must stay read-only. Naive datetimes returned by SQLite are treated as UTC (same convention as the behavioral/relationship modules), and the query instant is normalized before any comparison.

## 3. The future-isolation invariant

**A future event must never influence a historical reconstruction.** Later events are not silently swallowed — they are counted and reported:

- `excluded_event_count` — how many events have `event_timestamp > T`.
- `uncertainty_note` — `"{n} later event(s) exist but are EXCLUDED from this historical view — future evidence never rewrites the past."`

Spec example: a query at **14:32:08** on a chain whose `SETTLEMENT_CONFIRMED` arrives at **14:32:11** must see settlement as not-yet-known; the 14:32:11 event appears only as `excluded_event_count += 1` plus the uncertainty note — never in `stages`, never in the root cause, never in the confidence. The converse query at 14:32:11 does include it. Same engine, different evidence windows, no special-casing.

If there is no evidence at all at `T` (zero observed, zero excluded), the note is `"no evidence existed at this time"`.

## 4. Timestamp semantics

| Notion | Field | Meaning | Authority |
|---|---|---|---|
| Event order | array position in a reply / twin timeline ordering by `(timestamp, id)` | presentation order | derived |
| Domain time | `payment_events.event_timestamp` | when the thing actually happened in the payment lifecycle | **authoritative** — the only clock used by temporal queries |
| Processing time | `payment_events.created_at` | when our system ingested/recorded it | observational only |
| Causal relationship | `correlation_id` / `causation_id` | grouping (currently `= transaction_id`) and, once populated, what-caused-what | structural |

The temporal query deliberately keys on **domain time**, because "what did the system know at T" is a claim about the payment lifecycle's own clock, not about ingest bookkeeping.

## 5. API contract

```http
GET /api/v1/transactions/DEMO-S1/state-at?timestamp=2026-10-03T14:32:08Z
Authorization: (SYSTEM | ADMIN | SUPPORT API key)
```

```json
{
  "transaction_id": "DEMO-S1",
  "as_of": "2026-10-03T14:32:08+00:00",
  "state_then": "FAILED",
  "last_twin_event_type": "STATE_TRANSITION",
  "observed_event_count": 4,
  "excluded_event_count": 1,
  "reconstruction": {
    "root_cause": "MERCHANT_CONFIRMATION_TIMEOUT",
    "reconstruction_confidence": 0.71,
    "stages": {
      "bank_debit": "CONFIRMED",
      "gateway": "CONFIRMED",
      "merchant_confirmation": "TIMEOUT",
      "settlement": "NOT_OBSERVED"
    },
    "missing_events": []
  },
  "uncertainty_note": "1 later event(s) exist but are EXCLUDED from this historical view — future evidence never rewrites the past."
}
```

(Values above are illustrative shape, not a recorded run; the `as_of` is echoed tz-aware ISO-8601. Malformed timestamps are a 422 `VALIDATION_ERROR`, never a 500; naive timestamps are assumed UTC.)

## 6. Honesty note: causation is reserved, not claimed

Provider events today carry **`correlation_id` only** (`= transaction_id`, per migration `c7e1f2a93b84`). `causation_id` exists in the schema and in `EventEnvelope` but is **NULL for every event** — provider-observed events have no internal cause, and the system does not invent causal edges it cannot observe. The column is reserved for future engine-caused events; this document deliberately makes no causal-graph claims from the current data.

Related coverage: `tests/test_temporal.py` asserts the future-isolation invariant directly (a later settlement never leaks into a pre-settlement reconstruction).
