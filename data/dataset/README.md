# Full synthetic dataset (data/dataset/)

100% SYNTHETIC test data. Clearly-fake identities (example.com emails,
generated name combinations, fake phone numbers). No real persons,
accounts or payments exist here.

Regenerate: `python -m scripts.generate_full_dataset` (seed 42).
Re-running produces byte-identical data files (fixed seed).

| File | Rows | Contents |
|---|---|---|
| `accounts.csv` | 887 | accounts per customer; balances reconciled against transactions |
| `behavior_signals.csv` | 6,000 | 10 signals per customer, computed from their own history |
| `customers.csv` | 600 | customers: identity, country, segment, risk profile, archetype |
| `devices.csv` | 718 | device registry; last 18 are deliberately shared (mule signal) |
| `digital_twin_events.csv` | 39,748 | append-only Digital Twin timeline (state hops + observations) |
| `merchants.csv` | 40 | merchant catalog with category + risk tier |
| `model_assessments.csv` | 11,867 | per-model records (name/version/confidence/factors) |
| `payment_events.csv` | 66,928 | 14-type payment lifecycle evidence (Stage 6 shape) |
| `recovery_cases.csv` | 1,142 | Stage 8-shape recovery records (idempotency keys included) |
| `relationship_edges.csv` | 69,576 | USER/ACCOUNT/DEVICE/MERCHANT/GATEWAY/REFERENCE edges |
| `risk_assessments.csv` | 10,506 | Stage 7-shape assessments: anomaly taxonomy, evidence, rules |
| `transactions.csv` | 10,506 | transactions with ML attributes, state, risk score/level/decision |

## S1-S6 scenarios

`transactions.csv` rows with `scenario` S1..S6 mirror
`api/services/demo_scenarios.py` semantics using the same structures as
every other transaction (S1/S6 auto-recovered VERIFIED — S6 carries 2
recorded attempts for the replay story; S2 double deduction BLOCKED
with provider never called; S3 already-success; S4 insufficient
evidence; S5 late-settlement race — never released).

## Conventions

- All timestamps UTC, `YYYY-MM-DD HH:MM:SS+00:00`.
- `risk_score` is derived from transaction attributes (latency, retries,
  network, hour, geography, shared device) — not random noise.
- Empty string = not applicable / not observed (matches Stage 6's
  "not observed is derived, never stored" honesty rule).
- P2P transfers are represented as a single row on the sender's account
  (counterparty customer in `counterparty`); mule fan-in is represented
  as credit rows on the mule's account. There is no double-entry ledger.
- Loaded into the database with `scripts/load_dataset.py`
  (`.venv/bin/python -m scripts.load_dataset`; wipe + reload with an
  automatic timestamped backup of the SQLite file, then verifies row counts
  against `manifest.json`). Five tables map 1:1 (`transactions`,
  `digital_twin_events`, `payment_events`, `risk_assessments`,
  `recovery_cases`→`recovery_actions`); `customers.csv`/`merchants.csv`
  load into reference tables added by migration
  `e8f9a0b1c2d3_dataset_reference_tables`. `accounts`, `devices`,
  `behavior_signals`, `relationship_edges`, and `model_assessments` stay
  unloaded — risk internals the API deliberately keeps out of the models.
