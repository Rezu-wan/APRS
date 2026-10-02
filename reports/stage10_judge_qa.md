# Stage 10 — Judge Q&A

**Project:** AI Transaction Digital Twin & Autonomous Recovery Engine
**Theme:** *Automate recovery without automating trust.*

Every answer below is traceable to the real system: `recovery_decision_policy.py`
(POLICY_VERSION `autonomous-v1`), `recovery_safety.py`, `recovery_verifier.py`
(VERIFIER_VERSION `v1`), `risk_engine.py`, `payment_lifecycle.py`, the Stage 2
ML reports, and README sections 1–19.

---

**Q1. Why a Digital Twin? Why reconstruct at all?**

Because the source system only exposes a status flag, and a flag cannot answer
"what actually happened to this payment." The Digital Twin (Stage 6) rebuilds
the payment's true state deterministically from its event history — debit
status, settlement status, merchant confirmation, root cause — with no LLM
involved. That reconstruction produces the evidence trail every downstream
decision must cite, so recovery is decided on observed facts, not a flag.
*Demo pointer: DEMO-S1 — reconstruction timeline (Debit → Gateway → Timeout →
missing merchant confirmation).*

**Q2. Isn't a normal transaction status enough?**

No. The exact scenario this project targets is the one where the status lies by
omission: the customer is debited, the payment failed before settlement, and
the status alone cannot distinguish "genuine failure, money left the customer"
from "double deduction" or "false complaint." Our rules produce different
anomaly types for precisely these cases from the same events — and each type
gets a different treatment (recovery vs manual review vs no action). A single
status flag cannot make that distinction, and acting on it blindly is the
failure mode we are preventing. *Demo pointer: DEMO-S1 vs DEMO-S2 — same
"failed payment" surface, opposite correct actions.*

**Q3. How does the system know recovery is safe?**

Four independent layers. The policy (`autonomous-v1`) only allows recovery on
its single eligible path: recovery candidate + GENUINE_FAILURE + LOW/MEDIUM
risk + customer debit CONFIRMED + settlement not confirmed — and only releases
the held limit. The safety gate is independent of the policy: it never trusts
the earlier decision and re-derives everything from FRESH events inside the
executor (settlement confirmed → block; ≥2 debits → block; existing recovery →
block). Execution is idempotent, and a post-execution verifier must pass all
six checks before the recovery is marked VERIFIED. *Demo pointer: DEMO-S1
(eligible path) and DEMO-S5 (safety gate veto).*

**Q4. Can the AI itself release money?**

No. No AI component has write access to money movement. The ML anomaly model
produces a signal; the deterministic rules own the anomaly_type; the
versioned policy makes the decision; the safety gate holds a fresh-evidence
veto; the executor performs one bounded action (release the held limit, max 3
attempts); and the verifier is pure, deterministic code. GenAI sits entirely
outside the execution path — it explains results after the fact. *Demo pointer:
DEMO-S2 — the pipeline blocks without any model in the loop.*

**Q5. What if the ML is wrong?**

The architecture contains the blast radius. Rules own `anomaly_type`; the ML
model can only narrow uncertainty in one direction (upgrade INCOMPLETE/UNKNOWN
to SUSPICIOUS in three specific predicted scenarios), never flip evidence into
its opposite — a score of 0.7+ alone authorizes nothing. A wrong score still
has to survive the policy's requirements (recovery candidate, GENUINE_FAILURE,
LOW/MEDIUM risk) and the safety gate, which re-derives the facts from events
regardless of what any model said. *Demo pointer: DEMO-S2 — a confident-looking
surface still results in NO_ACTION.*

**Q6. What if GenAI hallucinates?**

The explanation is schema-controlled: the server pre-formats all numbers and
statuses into the prompt context, the system prompt (v4) forbids claiming real
money movement, responses are cached keyed on the decision state, and on any
provider failure the deterministic fallback templates are used
(`is_fallback: true`). Most importantly, explanations are derived read-only
data — a wrong sentence can never mutate a transaction, a recovery, or the
ledger. The customer-facing "resolved + released" wording is emitted only when
the recovery is actually VERIFIED. *Demo pointer: DEMO-S1's Bangla card appears
only after VERIFIED.*

**Q7. What happens with two simultaneous recovery requests?**

Exactly-once by construction. The idempotency key is
`sha256(transaction + action + policy_version + evidence_fingerprint)`; a DB
UNIQUE constraint on that key means the second concurrent insert hits an
IntegrityError race path instead of a second execution, and the provider
itself replays idempotently at the provider-reference level. We proved it with
a threaded exactly-once test (Stage 9). *Demo pointer: DEMO-S6 — second press
returns ALREADY_RECOVERED with the same recovery_id.*

**Q8. What if settlement arrives after the recovery was evaluated?**

It gets blocked. The window between decision and execution is exactly where a
settlement can land, so the safety gate re-derives from FRESH events inside the
executor: if a SETTLEMENT_CONFIRMED event exists, execution is blocked — no
release on top of a settled payment. Additionally, the verifier's check 5
fails if any settlement confirmed after the release, forcing a safe manual
state. *Demo pointer: DEMO-S5 — prepare, inject late settlement, process →
BLOCKED.*

**Q9. What if the provider call fails or times out?**

The failure-injection path shows it: the recovery row moves to FAILED, nothing
is released, the verifier is never bypassed, and attempts are bounded
(`attempt_count` ≤ 3). Because this is the sandbox provider, the failure is
deterministic and demonstrable. A failed attempt never leaves money in an
undefined state — the ledger entry simply stays Held. *Demo pointer: the
failure-injection scenario in the demo control panel.*

**Q10. Can this system move real money?**

No. The MockPaymentProvider — an in-memory ledger with write-through
persistence and a simulated 10,000 BDT limit — is the only provider
implementation. There is no real-bank integration anywhere in the codebase;
the wording, DB fields and documentation all carry the simulated nature. The
demo's value is the control architecture, not money movement. *Demo pointer:
the sandbox ledger view on any processed scenario.*

**Q11. Was the ML trained on real banking data?**

No — 100% synthetic data: 25,000 rows generated from documented generative
rules (SEED 42). We say this plainly because it is the honest position, and it
is also why we report metrics with ceilings rather than celebrating them. The
architecture (rules own decisions, ML only informs) is designed so that
swapping in better data later does not change the safety story. *Demo pointer:
n/a — covered in the Stage 2 report and README.*

**Q12. Could you connect a real payment provider?**

The seam exists: the `PaymentProvider` ABC defines `ensure_hold`,
`release_limit`, `get_ledger_entry` and `reset`; the executor and verifier are
provider-agnostic and only talk to that contract. What we honestly do not
claim is production readiness — a real provider needs settlement
reconciliation, retries under real latency, and regulatory review, none of
which a hackathon sandbox demonstrates. *Demo pointer: the sandbox ledger's
provider_reference, which is what the verifier checks against.*

**Q13. Why both rules AND ML?**

Rules give auditable, explainable evidence and own the anomaly_type — a judge
can read R0–R10 and the decision table R-A..R-F line by line. ML gives a
pattern signal on sparse features where rules have little to grip, but with
conservative precedence: risk_score = max(deterministic, 0.5·ML + 0.5·deterministic),
the level never falls, and ML can only upgrade specific uncertain outcomes.
Rules decide; ML informs. *Demo pointer: DEMO-S1 risk panel — rule evidence
lines alongside the ML signal.*

**Q14. How do you prevent double recovery?**

Four mechanisms stacked: the idempotency key (content-hash), the DB UNIQUE
constraint with the IntegrityError race path, provider-level idempotent replay,
and the safety gate's existing-recovery check plus the verifier's
single-active-recovery check. A repeat request gets ALREADY_RECOVERED with the
same recovery_id — not a second release. *Demo pointer: DEMO-S6.*

**Q15. How do you protect customer transactions?**

Customer-role API keys are bound to `user_id` ownership: a customer can only
see their own transactions, and mismatched access returns a non-enumerating
403 (the response does not reveal whether the transaction exists). On top of
that: role gates (SYSTEM/ADMIN/SUPPORT/CUSTOMER), an audit trail that records
key NAMES never secrets, per-key-bucket rate limits (explanations 30, recovery
20, risk 30, events 60, auth 60, default 120 per minute — configurable), and a
request ID on every response including errors. *Demo pointer: a customer-key
request against another user's transaction → 403.*

**Q16. What happens when evidence is incomplete?**

Nothing — deliberately. INCOMPLETE/UNKNOWN anomalies produce NO_ACTION with
`BLOCK_INSUFFICIENT_EVIDENCE`. Uncertainty never authorizes recovery; that is
a written invariant of the policy ("when uncertain: DO NOT RECOVER"), not an
accident. The manual-review path exists for humans. *Demo pointer: DEMO-S4 —
incomplete evidence → NO_ACTION.*

**Q17. What is the biggest limitation? We won't sugar-coat.**

Several, all real and documented: (1) the MockPaymentProvider is the only
provider — no real-bank integration is claimed; (2) the rate limiter is
in-process, not distributed — it does not bound a multi-node deployment; (3)
all ML is trained on synthetic data and currently pinned, so it reflects the
generative rules, not real-world drift; (4) the Task-1 failure classifier is
honestly weak (accuracy 0.78, macro-F1 0.23) — we report it as an
informational ceiling, which is why it never gates anything; (5) the Docker
hardening is written but was never built (Docker unavailable on the dev
machine); (6) there is no production deployment at all. The contribution is
the control architecture — evidence, policy, independent safety gate,
idempotency, verification — demonstrated end-to-end in a sandbox. *Demo
pointer: the honest-metrics panel and README sections 18–19.*
