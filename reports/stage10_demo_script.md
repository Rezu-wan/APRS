# Stage 10 — Demo Script (3–5 minutes)

**Project:** AI Transaction Digital Twin & Autonomous Recovery Engine
**Theme:** *Automate recovery without automating trust.*

Everything shown in this script runs against the real system: the deterministic
reconstruction engine, the hybrid risk engine (rules R0–R10 + the synthetic-v1
anomaly model), the `autonomous-v1` recovery policy, the independent safety
gate, the idempotent executor, the MockPaymentProvider sandbox, and the
independent verifier (6 checks). No numbers below are invented — ML metrics
quoted are the honest ones from the Stage 2 report.

---

## Pre-demo checklist (do this BEFORE the judges sit down)

1. Start the backend:
   ```
   uvicorn api.main:app
   ```
2. Run the readiness check:
   ```
   py -m scripts.stage10_demo_check
   ```
   It must print **`READY FOR DEMO`**. If it does not, fix the reported issue
   before the demo — do not improvise.
3. Log in with the staff key (`dev-admin-key`, role `ADMIN`).
4. Open the demo control panel: **`/demo`**.
5. Click **Reset demo** to reseed the deterministic DEMO-S1..S6 scenarios.
6. Confirm **S1** shows the transaction as *prepared / not yet processed*
   (no recovery row yet).

> Recovery-from-failure notes are at the bottom of this file. Read them once
> before presenting.

---

## 0:00–0:30 — The problem

**Say:**

> "A customer is debited, but the payment fails somewhere between the gateway
> and the merchant — before settlement. The only thing the bank's system shows
> is a status flag. A flag can't explain what actually happened, and it
> definitely can't be trusted to release money automatically — blindly
> releasing funds on a bad flag is exactly how banks lose money. This project
> automates the recovery, without automating the trust."

**[UI]** Stay on the `/demo` control panel; point at the S1 card (customer
debited, gateway timeout, no merchant confirmation).

---

## 0:30–1:00 — The Digital Twin

**[UI]** From the Demo control panel: **/demo → S1 → Open transaction.**

Show the reconstruction timeline (Stage 6): **Debit → Gateway → Timeout →
missing merchant confirmation.** Point out that this chain is deterministic —
no LLM is involved in reconstruction.

**Say:**

> "Instead of trusting a single status flag, we reconstruct the transaction
> from its event history. This Digital Twin is a deterministic engine — it
> derives the payment's true state from observed events, and it produces the
> evidence trail every later decision must cite."

---

## 1:00–1:45 — Risk intelligence (Stage 7)

**[UI]** Scroll to the risk panel on the same transaction page. Show the
deterministic rules (R0–R10) and their evidence lines, plus the ML anomaly
signal.

**Say:**

> "Risk is classified by ten deterministic rules that own the anomaly type,
> blended with an ML anomaly signal. Important honesty point: the ML model can
> only upgrade INCOMPLETE or UNKNOWN outcomes to SUSPICIOUS, in three narrow
> predicted scenarios — and a score alone is never enough to authorize
> anything. The model does not authorize recovery. It informs rules that do."

Note: for S1 the rules conclude **GENUINE_FAILURE** with a cited evidence
trail.

---

## 1:45–2:30 — Autonomous recovery (Stages 6–8)

**[UI]** Click **"Run autonomous recovery"** in the Autonomous recovery panel
— click it **once**. The backend runs the full pipeline:
**Policy → Safety Gate → Executor → Sandbox Provider → Verification.**

Watch the recovery pipeline strip transition to **VERIFIED**.

**Say:**

> "One click. The recovery policy — version autonomous-v1 — decides. Here the
> evidence qualifies: genuine failure, customer debit confirmed, settlement
> never confirmed, risk LOW. The only autonomous action it can ever take is
> releasing the held limit back to the customer. And before anything executes,
> an independent safety gate re-derives the facts from fresh events inside the
> executor."

---

## 2:30–3:00 — Verification

**[UI]** Show the verification checklist card — all checks ✓, status
**VERIFIED**. Then show the sandbox ledger entry: **Held → Released**.

**Say:**

> "The system does not call recovery complete until post-execution
> verification succeeds. Six independent checks: the ledger entry is released,
> the released amount matches the requested amount, the provider reference
> matches this recovery, the transaction did not succeed meanwhile, no
> settlement confirmed after the release, and this is the only active recovery
> for the transaction. Only then is the recovery marked VERIFIED."

---

## 3:00–3:30 — Customer explanation (Stage 4, prompts v4)

**[UI]** Show the Bangla customer explanation card (AI explanation, customer
view chip).

**Say:**

> "GenAI only communicates the deterministic result — in Bangla, for the
> customer. The 'resolved and limit released' wording appears only when the
> recovery is actually VERIFIED, and the prompts forbid claiming real money
> movement. The AI narrates; it never decides."

---

## 3:30–4:15 — Safety demonstration (DEMO-S2)

**[UI]** Back to **/demo → S2 → Open transaction.** Show: **DOUBLE_DEDUCTION**,
risk **CRITICAL**. Click "Run autonomous recovery" once.

Result: **RECOVERY BLOCKED** — provider NOT CALLED (blocked reason
`BLOCK_DOUBLE_DEDUCTION`).

**Say:**

> "The same automation that can recover a genuine failure can also refuse
> recovery when evidence is unsafe. Two debit confirmations means possible
> double deduction: the policy says no autonomous action, and the provider is
> never called. High and critical genuine failures also never move
> autonomously — they go to manual review."

---

## 4:15–5:00 — Race safety / idempotency (pick ONE, based on remaining time)

**Option A — DEMO-S5 (late settlement, the fresh-evidence gate):**

**[UI]** /demo → S5 → Open transaction. Click **Prepare** (evaluation runs,
decision is eligible — settlement had not yet arrived). Then **inject the late
settlement event**. Then click "Run autonomous recovery": **BLOCKED**.

**Say:**

> "Here the evaluation said recovery was safe — but a settlement confirmation
> arrived after the decision. The safety gate does not trust the earlier
> conclusion. It re-derives from fresh events inside the executor and blocks:
> no release on top of a settled payment."

**Option B — DEMO-S6 (double-press idempotency):**

**[UI]** /demo → S6 → Open transaction. Click "Run autonomous recovery"
**twice**. The first run VERIFIES; the second returns
**ALREADY_RECOVERED** with the **same recovery_id**, and the ledger shows
exactly one release.

**Say:**

> "Two presses, one recovery. The idempotency key is a hash of transaction,
> action, policy version and evidence fingerprint; a database UNIQUE
> constraint backs it up, and the provider replays idempotently. Exactly-once,
> even under concurrency — we tested this with a threaded exactly-once test."

**Closing line (either option):**

> "Autonomous does not mean uncontrolled. It means automated execution under
> independent evidence, safety, idempotency and verification controls."

---

## Recovery-from-failure notes

- **GenAI down / no API key:** the demo continues. Explanations fall back to
  the deterministic fallback templates (`is_fallback: true`). If it comes up
  during Q&A, say so honestly: the OpenAI-compatible client is wired but the
  demo runs keyless on the deterministic fallback.
- **A scenario was already processed:** use **Reset demo** on `/demo` and
  re-run. The seed script (DEMO-S1..S6) is deterministic.
- **Any error surfaces a request-id:** every error body carries a request ID —
  read it out for support; the audit trail links it to the server-side record.
- **Rate limit (429):** recovery actions are limited to 20 requests/min per
  key bucket. If you hit it during rehearsal, wait — never raise limits live.
- **Do not re-run S1 after it is VERIFIED:** that is exactly what S6
  demonstrates (ALREADY_RECOVERED). Use it as a feature, not a bug.

## Do NOT say

- Never say **"real money"**, **"refund"**, or **"the bank transfers funds"**.
  Always: **"simulated sandbox"**, **"the MockPaymentProvider ledger"**,
  **"the held limit is released in the sandbox"**.
- Never claim the ML failure classifier is production-grade — its accuracy is
  0.78 / macro-F1 0.23, reported honestly as an informational ceiling. Do not
  quote it unless asked; if asked, quote it exactly.
- Never claim Docker hardening is deployed — it is written but was never
  built on the dev machine.
- Never claim production readiness. The honest phrase: *"sandbox-only
  demonstration of the control architecture; production readiness is not
  claimed."*
