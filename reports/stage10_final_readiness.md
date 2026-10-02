# Stage 10 — Final Readiness Report

**Date:** 2026-10-03 · **Branch:** `frontend` · Status: **HACKATHON READY** (sandbox/simulation only — not "production banking ready").

Every value below was actually verified on this machine on 2026-10-03. Nothing is projected.

```text
STAGE 10 STATUS
Demo Check:            READY FOR DEMO (py -m scripts.stage10_demo_check, exit 0 — 7/7 checks)
Primary Scenario:      DEMO-S1 genuine failure → AUTO_RECOVERED → VERIFIED, 1,200 BDT released once,
                       ledger reconciles (Stage 10 E2E S1a–S1i, 9/9 PASS)
Safety Scenario:       DEMO-S2 double deduction → RECOVERY_BLOCKED / DOUBLE_DEDUCTION, provider NOT called,
                       transaction never released (PASS)
Race/Idempotency:      DEMO-S5 late settlement → RECOVERY_BLOCKED (fresh-evidence re-derivation) PASS;
                       DEMO-S6 duplicate process → ALREADY_RECOVERED, same recovery_id, released exactly once PASS

Backend Tests:         289 passed, 1 skipped (honest SQLite skip; baseline 278 + 11 new demo-router tests)
Frontend Tests:        75 passed (baseline 55 + 11 demo panel/console + 9 pipeline/gate/verification)
Stage 8 E2E:           7/7
Stage 9 E2E:           13/13
Stage 10 E2E:          18/18 (includes §29 security regression sweep: CUSTOMER 403 ×7,
                       SUPPORT read-never-write, 37 bodies secret-swept)

Build:                 vite production build PASS (code-split; DemoMode/SystemStatus lazy chunks)
TypeScript:            tsc --noEmit clean
Migration:             no new migrations in Stage 10 (demo control uses existing tables); DB is at the
                       Stage 9 head and the live server starts against it cleanly
Demo Seed:             6/6 (py -m scripts.seed_demo; S5 now seeds the settlement-race evidence set)

Docker:                hardened Dockerfile/compose written in Stage 9 (non-root, healthchecks, CSP) but
                       NOT built — Docker is not installed on this machine. Local demo runs on
                       uvicorn + vite directly.
Known Limitations:     see below — also reports/stage10_judge_qa.md Q17

Demo Duration:         3–5 minutes (reports/stage10_demo_script.md run sheet)
Required Services:     PostgreSQL (payment_recovery), uvicorn (api.main:app), local ML artifacts (models/),
                       vite dev server (or built dist) — GenAI provider OPTIONAL (deterministic mock/fallback;
                       demo continues without it)
Final Git Commit:      see "Git commits" below (recorded at push time on branch frontend)
```

## Performance (measured, not estimated)

| Server operation | Latency |
|---|---|
| Recovery process — first execution | ≈ 51 ms |
| Recovery process — idempotent replay | 28.5–32.2 ms |
| `/health` (DB ping + ML status) | 3.8–5.2 ms |

(E2E per-step wall-clock including the script's deliberate 1.2 s rate-limit pacing is tabulated in `stage10_demo_check.md`.)

## Demo reliability properties (verified)

- **Deterministic primary demo:** fixed ids `DEMO-S1..S6`, fixed timestamps, no RNG in the event builders; same seed + scenario = same logical outcome (18/18 E2E rerun-safe, reset-first).
- **No external dependencies in the critical path:** Postgres + FastAPI + local ML + sandbox provider; GenAI failure degrades to `is_fallback` explanations and the demo continues.
- **No demo endpoint bypasses the engine:** demo `prepare` creates data through the same services as the public API and NEVER processes recovery; the demo panel drives the real `/recovery/process`. Stage 10 E2E asserts `GET recovery → 404` after prepare.
- **Failure-safe presentation:** `stage10_demo_check.py` prints `NOT READY` and exits 1 when anything is missing (negative path verified); every error carries a request id; the Demo Mode panel and Reset action recover any half-run state.

## Responsiveness (§24 — verified to the extent possible on this machine)

The presentation layout targets desktop first: the recovery pipeline strip is
horizontal at `sm:` and up, collapsing to a vertical flow on narrow screens;
the dashboard metrics and story grids stack at `sm:`/`lg:` breakpoints; the
demo panel cards are single-column below `md:`. Verification method:
Tailwind responsive classes reviewed at the code level and the production
build compiles them; **no screenshot-based browser verification was
performed** (no browser automation is installed on this machine) — a 2-minute
manual pass at 1366/1280/1024/380 px in a real browser is recommended before
presenting. The judge-facing content (banner, pipeline, gate, verification)
is plain text/flow layout and has no fixed widths that could clip.

## Known limitations (honest)

1. **Sandbox-only.** `MockPaymentProvider` is the only provider implementation; no real money movement anywhere, by construction.
2. **ML trained on synthetic data.** All models (3 Stage-2 XGBoost + Stage-7 anomaly classifier) are trained on the generative dataset; Task-1 failure classifier honestly caps at acc 0.78 / macro-F1 0.23 (informational ceiling).
3. **In-process rate limiting.** Per-process sliding window — not distributed; documented Stage 9 seam.
4. **Docker not built.** Hardening written but unverifiable on this machine (no Docker).
5. **Single-node deployment assumed.** Everything (DB aside) is designed to run on one machine for the demo.
6. **No production hardening beyond Stage 9.** No TLS termination story, no distributed lock manager, no real provider integration — the `PaymentProvider` ABC is the seam, not a claim.

## Git commits (branch `frontend`, in order)

1. `stage10: fix sandbox ledger restore accounting — releases survive restarts`
2. `stage10: demo scenario service + staff-only demo control API (/demo/*, /sandbox/ledger)`
3. `stage10(frontend): demo contract + control panel, system status console, sandbox banner, judge mode, dashboard landing`
4. `stage10(frontend): judge-facing recovery story on the transaction page (pipeline, safety gate, verification, ledger)`
5. `stage10: demo health check + full demo E2E (18 checks, measured timings)`
6. `stage10: demo run sheet, judge Q&A, pitch, demo-check + readiness reports; README section 20`

## Documentation index

- `reports/stage10_demo_script.md` — timed 3–5 min presenter run sheet + pre-demo checklist
- `reports/stage10_demo_check.md` — demo check + Stage 10 E2E outputs, measured timings, regression gates
- `reports/stage10_judge_qa.md` — 17 judge questions answered from the real architecture
- `reports/stage10_pitch.md` — problem/solution/safety/AI-role/differentiator pitch
- `README.md` §20 — Stage 10 overview (demo control, console, judge mode, scripts)
