# AI Transaction Digital Twin & Autonomous Recovery Engine — Pitch

**Theme:** *Automate recovery without automating trust.*

## Problem

A customer is debited, but the payment fails between the gateway and the
merchant — before settlement. All the back-office sees is a status flag: it
cannot explain what happened, cannot distinguish a genuine failure from a
double deduction or a false complaint, and must never be the sole basis for
releasing money. Today that reconciliation is manual, slow, and error-prone in
both directions: money held too long, or released on bad evidence.

## Solution

An end-to-end engine that reconstructs each payment's true state from its
event history (the Digital Twin), classifies it with hybrid risk intelligence,
and — only when the evidence is safe — autonomously releases the customer's
held limit, verifies the result post-execution, and explains it to the
customer in Bangla. Six deterministic demo scenarios (DEMO-S1..S6) demonstrate
the happy path, every refusal path, and the race conditions.

## Safety

Five independent controls, in order:

1. **Evidence** — deterministic event reconstruction (no LLM) with a cited
   evidence trail.
2. **Policy** — `autonomous-v1`, first-match rules: the only autonomous action
   is RELEASE_LIMIT, and only for recovery candidates with GENUINE_FAILURE,
   LOW/MEDIUM risk, debit CONFIRMED and settlement not confirmed. HIGH/CRITICAL
   genuine failures go to MANUAL_REVIEW; anything ambiguous gets NO_ACTION.
   When uncertain, do not recover.
3. **Independent safety gate** — re-derives everything from FRESH events
   inside the executor: settlement confirmed → block; ≥2 debits → block;
   existing recovery → block. It never trusts the earlier decision.
4. **Idempotent execution** — idempotency key
   `sha256(tx + action + policy_version + evidence_fingerprint)`, DB UNIQUE
   constraint, `attempt_count` ≤ 3, provider-level idempotent replay. Exactly
   once, even under concurrency (threaded exactly-once test).
5. **Post-execution verification** — 6 independent checks (ledger released,
   amount matches, provider reference matches, transaction not SUCCESS, no
   settlement after the release, single active recovery). LIMIT_RELEASED is
   reported only after all pass.

## The AI's role

- **ML** (3 XGBoost models + the synthetic-v1 anomaly model) is a *risk and
  anomaly signal*. Deterministic rules own `anomaly_type`; ML can only upgrade
  INCOMPLETE/UNKNOWN → SUSPICIOUS in three narrow predicted scenarios, and a
  score alone authorizes nothing. Metrics are reported honestly: Task 2
  ROC-AUC 0.936, Task 3 R² 0.90, anomaly macro-F1 0.891 with false-complaint
  recall 0.00 by design — and the Task-1 failure classifier's accuracy
  0.78 / macro-F1 0.23 is reported as an informational ceiling, which is why
  it never gates anything.
- **GenAI** (prompts v4) is an *explainer*. Schema-controlled context,
  server-pre-formatted numbers, cache keyed on decision state, anti-hallucination
  system prompt, deterministic fallback templates. Customer-facing
  "resolved + released" wording only when VERIFIED.

**Neither authorizes money movement. No AI component has write access to it.**

## Differentiator

This is not just fraud detection — it does not stop at scoring. It is not just
a chatbot — the LLM never decides anything. It connects the full chain:
event reconstruction → risk intelligence → autonomous recovery → independent
verification → customer explanation. The hard part of payments automation is
not predicting risk; it is executing safely when the evidence says so and
refusing when it does not. That refusal path is a first-class, demoable
feature here.

## Sandbox statement

All money movement is simulated: the MockPaymentProvider (in-memory +
write-through persisted ledger, 10,000 BDT simulated limit) is the only
provider implementation. No real funds, no bank integration, and production
readiness is not claimed.

## Architecture at a glance

```
Payment Events
  → Event Reconstruction          (deterministic, no LLM)
  → Risk & Anomaly Classification (10 rules own anomaly_type; ML upgrades only)
  → Recovery Policy               (autonomous-v1, first-match)
  → Independent Safety Gate       (fresh-evidence re-derivation)
  → Idempotent Recovery Executor  (sha256 key, UNIQUE, ≤3 attempts)
  → Sandbox Payment Provider      (MockPaymentProvider)
  → Persistent Sandbox Ledger     (write-through, survives restarts)
  → Verification                  (6 checks → VERIFIED)
  → Digital Twin Event Log        (full audit timeline)
  → GenAI Explanation             (Bangla, prompts v4, fallback-safe)
  → React Dashboard               (timeline, recovery, explanation, demo panel)
```

## Demo scenarios (S1–S6)

| ID | Scenario | Outcome |
|----|----------|---------|
| S1 | Genuine failure, debit confirmed, no settlement | Recovery → VERIFIED, limit released |
| S2 | DOUBLE_DEDUCTION, risk CRITICAL | RECOVERY BLOCKED, provider not called |
| S3 | Success (happy path) | NO_ACTION — ALREADY_SUCCESS; nothing to recover |
| S4 | Incomplete evidence | NO_ACTION — INSUFFICIENT_EVIDENCE; uncertainty never recovers |
| S5 | Settlement arrives after evaluation | Safety gate blocks on fresh events |
| S6 | Recovery requested twice | ALREADY_RECOVERED, same recovery_id, one release |

## The closing theme

**Automate recovery without automating trust.** Trust stays with the
evidence, the policy, and the independent controls — the automation only
executes what they jointly permit. Autonomous does not mean uncontrolled; it
means automated execution under independent evidence, safety, idempotency and
verification.
