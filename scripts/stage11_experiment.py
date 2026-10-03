"""
Stage 11 Phase 11H — Research Evaluation Experiment
Project: AI-Powered Payment Failure Recovery & Digital Twin System
====================================================================

Answers the spec §19 research question HONESTLY and ONLY on synthetic
data:

    "Can safety-gated autonomous recovery reduce recovery latency while
     preventing unsafe financial state changes compared with less
     constrained recovery strategies?"

Drives the LIVE API (sandbox demo corpus only — no real money ever
moves):

  E1  policy comparison (effectiveness + safety): POST
      /api/v1/policy-simulator/run with autonomous-v1,
      manual-only-baseline and autonomous-v2-experimental over the
      deterministic DEMO-S1..S6 corpus (ground truth available).
  E2  safety-gate contribution (honestly scoped — see the summary text):
      the gate is ALWAYS on in this system and cannot be switched off
      through the API. The measured contribution is (a) v1's gate_vetoes
      on the corpus plus (b) the LATE_SETTLEMENT / CONCURRENT_RECOVERY
      chaos invariants. NO gate-off counterfactual is fabricated.
  E3  reliability under faults: all 10 chaos scenarios, verdicts and
      per-invariant held/total counts.
  E4  operational latency: real snapshots of recovery_latency_ms /
      risk_latency_ms / reconstruction_latency_ms from GET /metrics.

Outputs under reports/stage11/ (config JSON, raw results JSON, flat CSV,
human markdown summary; one E1 bar-chart figure only if matplotlib is
importable). Every output records the metadata block (dataset
fingerprint, policy versions, code_versions, seed, git commit, UTC
timestamp, api_url, configuration).

Reproducibility contract: same seed + same corpus -> identical
dataset_fingerprint and identical E1 decision metrics. Latencies are
wall-clock and NOT part of that contract.

Style follows scripts/stage8_e2e.py / scripts/stage10_e2e.py: stdlib
only, _request/_call/ApiError, --api-url/--seed/--verbose, honest
PASS/FAIL, no secret is ever printed.

Usage:
  py -m scripts.stage11_experiment
  py -m scripts.stage11_experiment --api-url http://127.0.0.1:8000 --seed 42 --verbose
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

# Ensure the project root is importable when run as a plain script
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# --------------------------------------------------------------------------
# Configuration
# --------------------------------------------------------------------------
SEED = 42
DEFAULT_API_URL = "http://127.0.0.1:8000"
ADMIN_KEY = "dev-admin-key"   # documented public dev placeholder (README)

# Rate-limit politeness for the live dev server (rate limiting ENABLED).
INTER_CALL_SLEEP_S = 1.2

REPORT_DIR = os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "reports", "stage11")

POLICIES = ("autonomous-v1", "manual-only-baseline",
            "autonomous-v2-experimental")

CHAOS_SCENARIOS = (
    "GATEWAY_TIMEOUT",
    "GATEWAY_ERROR",
    "MERCHANT_TIMEOUT",
    "LATE_SETTLEMENT",
    "DUPLICATE_EVENT",
    "OUT_OF_ORDER_EVENT",
    "PROVIDER_TIMEOUT",
    "PROVIDER_ERROR",
    "CONCURRENT_RECOVERY",
    "DB_FAILURE_SIMULATION",
)

GATE_CONTRIBUTION_SCENARIOS = ("LATE_SETTLEMENT", "CONCURRENT_RECOVERY")

LATENCY_SERIES = ("recovery_latency_ms", "risk_latency_ms",
                  "reconstruction_latency_ms")

E2_HONEST_SCOPE = (
    "HONEST SCOPE (E2): the safety gate is ALWAYS on in this system and "
    "cannot be switched off through the API, so no gate-off run exists. "
    "The reported comparison is (policy decisions) vs (policy decisions + "
    "measured gate vetoes): v1's gate_vetoes on the demo corpus plus the "
    "LATE_SETTLEMENT and CONCURRENT_RECOVERY chaos invariants. No gate-off "
    "counterfactual is fabricated anywhere in this experiment."
)

LIMITATIONS = """\
LIMITATIONS (all stated plainly):
- Synthetic demo corpus only: 6 deterministic transactions (DEMO-S1..S6).
  Small n; results are illustrative, not statistical.
- Sandbox provider only: every release is simulated; no real financial
  transaction is performed and no real-world claim is made.
- ML ablation is not API-switchable: the rules-only degradation path is
  covered by unit tests instead, not measured here as a live arm.
- The safety gate cannot be disabled via the API: E2 measures the gate's
  observed contribution (vetoes + chaos invariants), never a gate-off
  counterfactual.
- Latency columns are wall-clock observations of a live dev server and
  are explicitly NOT part of the reproducibility contract; only the
  dataset fingerprint and the E1 decision metrics are.
- GenAI provider availability may vary; this experiment measures the
  deterministic recovery/safety layer, not explanation quality.
"""


# --------------------------------------------------------------------------
# HTTP helpers (stdlib only)
# --------------------------------------------------------------------------

def _request(method: str, url: str, api_key: str, payload: dict | None,
             verbose: bool) -> tuple[int, dict]:
    """One JSON API call. Returns (status_code, parsed_body)."""
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    request = urllib.request.Request(
        url, data=data, method=method,
        headers={"X-API-Key": api_key, "Content-Type": "application/json"},
    )
    if verbose:
        print(f"[verbose] {method} {url}")
    try:
        with urllib.request.urlopen(request) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            detail = exc.read().decode("utf-8", errors="replace")
        except Exception:
            pass
        try:
            return exc.code, json.loads(detail) if detail else {}
        except json.JSONDecodeError:
            raise RuntimeError(f"HTTP {exc.code}: {detail or exc.reason}") from exc


class ApiError(Exception):
    """A step failed against the live API."""

    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


def _call(method: str, url: str, api_key: str, payload: dict | None,
          verbose: bool, expect: tuple[int, ...] = (200,)) -> dict:
    time.sleep(INTER_CALL_SLEEP_S)  # stay under the live server's rate limit
    status, body = _request(method, url, api_key, payload, verbose)
    if verbose:
        print(f"[verbose] -> {status}: {json.dumps(body)[:400]}")
    if status == 429:
        raise ApiError("HTTP 429 rate limited — raise INTER_CALL_SLEEP_S "
                       "and re-run", status=status)
    if status not in expect:
        raise ApiError(
            f"{method} {url} -> HTTP {status}: {json.dumps(body)[:300]}",
            status=status,
        )
    return body


# --------------------------------------------------------------------------
# Experiment steps
# --------------------------------------------------------------------------

def preflight(base: str, verbose: bool) -> str:
    """GET /health must be healthy with ML models loaded. Returns env."""
    health = _call("GET", f"{base}/health", ADMIN_KEY, None, verbose)
    if health.get("status") != "healthy":
        raise ApiError(f"NOT READY: /health status={health.get('status')!r}")
    if health.get("ml_models") != "loaded":
        raise ApiError(f"NOT READY: ML models not loaded "
                       f"({health.get('ml_models')!r})")
    print(f"Pre-flight OK: healthy, ml_models=loaded "
          f"(environment={health.get('environment')!r})")
    return str(health.get("environment"))


def reset_demo(base: str, verbose: bool) -> dict:
    """POST /demo/reset — deterministic DEMO-S1..S6 corpus."""
    body = _call("POST", f"{base}/api/v1/demo/reset", ADMIN_KEY, None,
                 verbose)
    if body.get("reset") is not True:
        raise ApiError(f"demo reset returned reset={body.get('reset')!r}")
    print("Demo corpus reset (DEMO-S1..S6, deterministic).")
    return body


def run_e1_policy_comparison(base: str, verbose: bool) -> dict:
    """POST /policy-simulator/run over all three policies (demo source)."""
    body = _call("POST", f"{base}/api/v1/policy-simulator/run", ADMIN_KEY,
                 {"policies": list(POLICIES),
                  "source": {"demo": True}}, verbose)
    if body.get("simulated") is not True or \
            body.get("affects_live_policy") is not False:
        raise ApiError("simulator response must be simulated=true, "
                       "affects_live_policy=false")
    if not body.get("dataset", {}).get("dataset_fingerprint"):
        raise ApiError("simulator response carries no dataset_fingerprint")
    if not body.get("run_id"):
        raise ApiError("simulator response carries no run_id")
    print(f"E1 simulator run {body['run_id']}: "
          f"fingerprint={body['dataset']['dataset_fingerprint'][:16]}… "
          f"ground_truth={body.get('ground_truth_available')}")
    return body


def run_e3_chaos(base: str, verbose: bool) -> list[dict]:
    """All 10 chaos scenarios; each returns the honest verdict record."""
    records: list[dict] = []
    for scenario in CHAOS_SCENARIOS:
        body = _call("POST", f"{base}/api/v1/chaos/run", ADMIN_KEY,
                     {"scenario": scenario}, verbose)
        verdict = str(body.get("verdict"))
        inv = body.get("invariants") or []
        held = sum(1 for i in inv if i.get("held") is True)
        records.append({
            "scenario": scenario,
            "verdict": verdict,
            "outcome": body.get("outcome"),
            "invariants_held": held,
            "invariants_total": len(inv),
            "invariants": inv,
            "note": body.get("note"),
        })
        flag = {"PASS": "ok", "SKIP": "skip", "FAIL": "FAIL"}.get(verdict,
                                                                 verdict)
        print(f"  chaos {scenario:<24} {verdict:<5} "
              f"invariants {held}/{len(inv)} [{flag}]")
    return records


def build_e2_gate_contribution(sim: dict, chaos: list[dict]) -> dict:
    """E2: the gate's MEASURED contribution — v1 gate vetoes on the corpus
    + the LATE_SETTLEMENT / CONCURRENT_RECOVERY chaos invariants. Honestly
    scoped: the gate is always on; no counterfactual."""
    v1 = next((p for p in sim.get("policies", [])
               if p.get("policy_version") == "autonomous-v1"), None)
    vetoes = int(v1.get("gate_vetoes", 0)) if v1 else None
    veto_detail = [
        {"transaction_id": d.get("transaction_id"),
         "policy_action": d.get("action"),
         "gate_blocked_reason": d.get("gate_blocked_reason")}
        for d in (v1.get("decisions") or []) if v1 and d.get("gate_vetoed")
    ] if v1 else []
    chaos_evidence = {}
    for rec in chaos:
        if rec["scenario"] in GATE_CONTRIBUTION_SCENARIOS:
            chaos_evidence[rec["scenario"]] = {
                "verdict": rec["verdict"],
                "invariants_held": rec["invariants_held"],
                "invariants_total": rec["invariants_total"],
                "invariants": [
                    {"name": i.get("name"), "held": i.get("held"),
                     "detail": i.get("detail")}
                    for i in rec["invariants"]],
            }
    return {
        "honest_scope": E2_HONEST_SCOPE,
        "v1_gate_vetoes_on_corpus": vetoes,
        "v1_vetoed_decisions": veto_detail,
        "chaos_gate_invariants": chaos_evidence,
    }


def run_e4_latency(base: str, verbose: bool) -> dict:
    """GET /metrics — real latency snapshots (count/avg/min/max).

    The metrics registry is in-memory per process, so series with no
    traffic since server start do not exist. Warm the risk and
    reconstruction series with REAL endpoint calls first (DEMO fixtures,
    read + POST assessment — no state change) so all three series are
    represented. Every recorded value is a real measurement."""
    _call("GET", f"{base}/api/v1/transactions/DEMO-S1/reconstruction",
          ADMIN_KEY, None, verbose)
    _call("POST", f"{base}/api/v1/transactions/DEMO-S2/risk-assessment",
          ADMIN_KEY, {"customer_reported_failure": False}, verbose)
    body = _call("GET", f"{base}/api/v1/metrics", ADMIN_KEY, None, verbose)
    latencies = body.get("latencies_ms") or {}
    snapshot = {}
    for series in LATENCY_SERIES:
        entry = latencies.get(series)
        if entry is None:
            print(f"  WARN: /metrics has no {series} series yet (no traffic "
                  "recorded since process start)")
            continue
        snapshot[series] = entry
    counters = body.get("counters") or {}
    return {
        "latency_snapshots": snapshot,
        "relevant_counters": {k: v for k, v in counters.items()
                              if any(t in k for t in
                                     ("recovery", "risk", "reconstruction",
                                      "gate", "chaos", "simulator"))},
        "generated_at": body.get("generated_at"),
    }


# --------------------------------------------------------------------------
# Metadata + outputs
# --------------------------------------------------------------------------

def git_commit() -> str:
    """Current HEAD commit (best effort; never raises)."""
    try:
        return subprocess.run(
            ["git", "rev-parse", "HEAD"], capture_output=True, text=True,
            check=True, timeout=10).stdout.strip()
    except Exception:
        return "unknown"


def build_metadata(args, sim: dict, environment: str, run_dir_names: dict) -> dict:
    return {
        "research_question": (
            "Can safety-gated autonomous recovery reduce recovery latency "
            "while preventing unsafe financial state changes compared with "
            "less constrained recovery strategies?"),
        "dataset_fingerprint": sim["dataset"]["dataset_fingerprint"],
        "policy_versions_evaluated": list(POLICIES),
        "code_versions": sim.get("code_versions") or {},
        "seed": args.seed,
        "seed_note": (
            "Recorded for convention only. The simulator and demo corpus "
            "are deterministic: the same seed + same corpus reproduce the "
            "same dataset_fingerprint and the same E1 decision metrics. "
            "The seed does not jitter anything in this experiment."),
        "git_commit": git_commit(),
        "experiment_timestamp_utc": datetime.now(timezone.utc)
            .strftime("%Y-%m-%dT%H:%M:%SZ"),
        "api_url": args.api_url,
        "environment": environment,
        "configuration": {
            "policies": list(POLICIES),
            "simulator_source": {"demo": True},
            "chaos_scenarios": list(CHAOS_SCENARIOS),
            "gate_contribution_scenarios": list(GATE_CONTRIBUTION_SCENARIOS),
            "latency_series": list(LATENCY_SERIES),
            "demo_reset_before_measurement": True,
            "figures_enabled": run_dir_names["figures_enabled"],
            "output_files": run_dir_names["files"],
            "sandbox_only": True,
        },
    }


def _figure(matplotlib_available: bool, sim: dict, path: str) -> str | None:
    """One E1 bar-chart figure — only when matplotlib is importable."""
    if not matplotlib_available:
        return None
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        policies = [p["policy_version"] for p in sim.get("policies", [])]
        metrics = ("would_release", "would_block", "would_manual_review",
                   "would_no_action")
        series = {m: [int(p.get(m, 0)) for p in sim.get("policies", [])]
                  for m in metrics}
        x = range(len(policies))
        width = 0.2
        fig, ax = plt.subplots(figsize=(8, 4.5))
        for i, m in enumerate(metrics):
            ax.bar([xx + i * width for xx in x], series[m], width,
                   label=m.replace("would_", ""))
        ax.set_xticks([xx + 1.5 * width for xx in x])
        ax.set_xticklabels(policies, fontsize=8)
        ax.set_ylabel("transactions (demo corpus, n=6)")
        ax.set_title("E1 policy comparison (sandbox demo corpus)")
        ax.legend(fontsize=8)
        fig.tight_layout()
        fig.savefig(path, dpi=120)
        plt.close(fig)
        return path
    except Exception as exc:  # a figure must never fail the experiment
        print(f"  WARN: figure skipped ({exc})")
        return None


def write_outputs(metadata: dict, sim: dict, e2: dict, chaos: list[dict],
                  e4: dict, run_id: str, figures_enabled: bool) -> dict:
    """Write config JSON, results JSON, summary CSV and markdown summary.
    Returns the paths written."""
    os.makedirs(os.path.join(REPORT_DIR, "experiments"), exist_ok=True)
    os.makedirs(os.path.join(REPORT_DIR, "metrics"), exist_ok=True)
    os.makedirs(os.path.join(REPORT_DIR, "configs"), exist_ok=True)

    config_path = os.path.join(REPORT_DIR, "configs",
                               f"experiment-{run_id}.json")
    results_path = os.path.join(REPORT_DIR, "experiments",
                                f"experiment-{run_id}.json")
    csv_path = os.path.join(REPORT_DIR, "metrics", f"summary-{run_id}.csv")
    md_path = os.path.join(REPORT_DIR, "experiments",
                           f"experiment-{run_id}_summary.md")

    with open(config_path, "w", encoding="utf-8") as fh:
        json.dump({"experiment": "stage11_phase_11h",
                   "run_id": run_id, **metadata}, fh, indent=2,
                  ensure_ascii=False)

    results = {
        "experiment": "stage11_phase_11h",
        "run_id": run_id,
        **metadata,
        "e1_policy_comparison": sim,
        "e2_gate_contribution": e2,
        "e3_chaos_reliability": chaos,
        "e4_latency": e4,
    }
    with open(results_path, "w", encoding="utf-8") as fh:
        json.dump(results, fh, indent=2, ensure_ascii=False)

    # ---- flat CSV: experiment,metric,policy,scenario,value -------------
    rows: list[dict] = []

    def add(experiment, metric, policy, scenario, value):
        rows.append({"experiment": experiment, "metric": metric,
                     "policy": policy or "", "scenario": scenario or "",
                     "value": value})

    for p in sim.get("policies", []):
        pol = p.get("policy_version", "")
        for metric in ("transactions_evaluated", "would_release",
                       "would_block", "would_manual_review",
                       "would_no_action", "gate_vetoes", "false_recovery",
                       "missed_recovery", "provider_calls_avoided",
                       "decision_latency_ms_avg"):
            if p.get(metric) is not None:
                add("E1", metric, pol, "", p[metric])
    add("E2", "v1_gate_vetoes_on_corpus", "autonomous-v1", "",
        e2.get("v1_gate_vetoes_on_corpus") if e2.get(
            "v1_gate_vetoes_on_corpus") is not None else "n/a")
    for scenario, ev in sorted(e2.get("chaos_gate_invariants", {}).items()):
        add("E2", "gate_invariants_held", "", scenario,
            ev["invariants_held"])
        add("E2", "gate_invariants_total", "", scenario,
            ev["invariants_total"])
    for rec in chaos:
        add("E3", "verdict", "", rec["scenario"], rec["verdict"])
        add("E3", "invariants_held", "", rec["scenario"],
            rec["invariants_held"])
        add("E3", "invariants_total", "", rec["scenario"],
            rec["invariants_total"])
    for series, entry in (e4.get("latency_snapshots") or {}).items():
        for key in ("count", "avg_ms", "min_ms", "max_ms"):
            add("E4", f"{series}.{key}", "", "", entry.get(key))

    with open(csv_path, "w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=["experiment", "metric",
                                                "policy", "scenario",
                                                "value"])
        writer.writeheader()
        writer.writerows(rows)

    figure_path = None
    if figures_enabled:
        figure_path = _figure(True, sim, os.path.join(
            REPORT_DIR, "experiments", f"experiment-{run_id}_e1.png"))

    _write_md_summary(md_path, metadata, sim, e2, chaos, e4, run_id,
                      figure_path)

    return {"config": config_path, "results": results_path,
            "csv": csv_path, "summary_md": md_path,
            "figure": figure_path}


def _write_md_summary(path: str, metadata: dict, sim: dict, e2: dict,
                      chaos: list[dict], e4: dict, run_id: str,
                      figure_path: str | None) -> None:
    policies = sim.get("policies", [])
    code_versions_json = json.dumps(metadata["code_versions"], sort_keys=True)
    lines: list[str] = []
    lines.append(f"# Stage 11 Phase 11H — Research Evaluation Summary "
                 f"(run {run_id})")
    lines.append("")
    lines.append("## Research question (spec §19)")
    lines.append("")
    lines.append(f"> {metadata['research_question']}")
    lines.append("")
    lines.append("Answered ONLY on synthetic sandbox data (deterministic "
                 "DEMO-S1..S6 corpus; no real financial transaction is "
                 "performed).")
    lines.append("")
    lines.append("## Metadata")
    lines.append("")
    lines.append(f"- run_id: `{run_id}`")
    lines.append(f"- dataset_fingerprint: "
                 f"`{metadata['dataset_fingerprint']}`")
    lines.append(f"- policy versions: "
                 f"{', '.join(metadata['policy_versions_evaluated'])}")
    lines.append(f"- code_versions: `{code_versions_json}`")
    lines.append(f"- seed: {metadata['seed']} — "
                 f"{metadata['seed_note']}")
    lines.append(f"- git commit: `{metadata['git_commit']}`")
    lines.append(f"- timestamp (UTC): {metadata['experiment_timestamp_utc']}")
    lines.append(f"- api_url: {metadata['api_url']} "
                 f"(environment: {metadata['environment']})")
    lines.append("")
    lines.append("## What was measured")
    lines.append("")
    lines.append("- **E1 policy comparison** — the policy simulator over "
                 "the demo corpus for all three policies, with ground-truth "
                 "false_recovery / missed_recovery counts.")
    lines.append("- **E2 safety-gate contribution** — see the honest-scope "
                 "paragraph below.")
    lines.append("- **E3 reliability under faults** — all 10 chaos "
                 "scenarios with per-invariant outcomes.")
    lines.append("- **E4 operational latency** — real "
                 "recovery/risk/reconstruction latency snapshots from "
                 "GET /metrics.")
    lines.append("")
    lines.append("## E1 — policy comparison (n=6 demo transactions)")
    lines.append("")
    header = ("| policy | release | block | manual_review | no_action | "
              "gate_vetoes | false_recovery | missed_recovery | "
              "provider_calls_avoided | decision_latency_ms_avg |")
    lines.append(header)
    lines.append("|---|---|---|---|---|---|---|---|---|---|")
    for p in policies:
        lines.append(
            "| {pol} | {rel} | {blk} | {man} | {noa} | {vet} | {fr} | {mr} "
            "| {pca} | {lat} |".format(
                pol=p.get("policy_version"),
                rel=p.get("would_release"), blk=p.get("would_block"),
                man=p.get("would_manual_review"),
                noa=p.get("would_no_action"), vet=p.get("gate_vetoes"),
                fr=p.get("false_recovery") if p.get("false_recovery")
                is not None else "n/a",
                mr=p.get("missed_recovery") if p.get("missed_recovery")
                is not None else "n/a",
                pca=p.get("provider_calls_avoided"),
                lat=p.get("decision_latency_ms_avg")))
    lines.append("")
    lines.append(f"dataset: source={sim.get('dataset', {}).get('source')} "
                 f"transactions={sim.get('dataset', {}).get('transaction_ids')} "
                 f"fingerprint=`{sim.get('dataset', {}).get('dataset_fingerprint')}`")
    lines.append("")
    if figure_path:
        lines.append(f"![E1 comparison](experiment-{run_id}_e1.png)")
        lines.append("")
    lines.append("## E2 — safety-gate contribution")
    lines.append("")
    lines.append(e2.get("honest_scope", E2_HONEST_SCOPE))
    lines.append("")
    lines.append(f"- v1 gate vetoes on the demo corpus: "
                 f"**{e2.get('v1_gate_vetoes_on_corpus')}**")
    for d in e2.get("v1_vetoed_decisions", []):
        lines.append(f"  - `{d['transaction_id']}`: policy wanted "
                     f"{d['policy_action']}, gate blocked with "
                     f"`{d['gate_blocked_reason']}`")
    lines.append("")
    lines.append("| chaos scenario | verdict | invariants held |")
    lines.append("|---|---|---|")
    for scenario, ev in sorted(e2.get("chaos_gate_invariants", {}).items()):
        lines.append(f"| {scenario} | {ev['verdict']} | "
                     f"{ev['invariants_held']}/{ev['invariants_total']} |")
    lines.append("")
    lines.append("## E3 — reliability under faults (chaos)")
    lines.append("")
    lines.append("| scenario | verdict | invariants held | note |")
    lines.append("|---|---|---|---|")
    for rec in chaos:
        lines.append(f"| {rec['scenario']} | {rec['verdict']} | "
                     f"{rec['invariants_held']}/{rec['invariants_total']} | "
                     f"{rec.get('note') or ''} |")
    lines.append("")
    lines.append("## E4 — operational latency (real /metrics snapshots)")
    lines.append("")
    lines.append("| series | count | avg_ms | min_ms | max_ms |")
    lines.append("|---|---|---|---|---|")
    for series, entry in (e4.get("latency_snapshots") or {}).items():
        lines.append(f"| {series} | {entry.get('count')} | "
                     f"{entry.get('avg_ms'):.1f} | {entry.get('min_ms'):.1f} "
                     f"| {entry.get('max_ms'):.1f} |")
    lines.append("")
    lines.append("Latencies are wall-clock observations of a live dev "
                 "server and are **not** part of the reproducibility "
                 "contract (only the dataset fingerprint and the E1 "
                 "decision metrics are).")
    lines.append("")
    lines.append("## Limitations")
    lines.append("")
    lines.append(LIMITATIONS)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")


# --------------------------------------------------------------------------
# Console reporting + entry point
# --------------------------------------------------------------------------

def print_console_report(sim: dict, chaos: list[dict], paths: dict,
                         figures_note: str) -> None:
    print()
    print("E1 — policy comparison (demo corpus)")
    print("-" * 104)
    print(f"{'policy':<28}{'release':>9}{'block':>7}{'manual':>8}"
          f"{'no_act':>8}{'vetoes':>8}{'false':>7}{'missed':>8}"
          f"{'prov_avoid':>12}{'lat_ms_avg':>12}")
    print("-" * 104)
    for p in sim.get("policies", []):
        fr = p.get("false_recovery")
        mr = p.get("missed_recovery")
        print(f"{p.get('policy_version', ''):<28}"
              f"{p.get('would_release', 0):>9}{p.get('would_block', 0):>7}"
              f"{p.get('would_manual_review', 0):>8}"
              f"{p.get('would_no_action', 0):>8}{p.get('gate_vetoes', 0):>8}"
              f"{str(fr if fr is not None else '-'):>7}"
              f"{str(mr if mr is not None else '-'):>8}"
              f"{p.get('provider_calls_avoided', 0):>12}"
              f"{p.get('decision_latency_ms_avg', 0):>12.1f}")
    print("-" * 104)
    print()
    print("E3 — chaos verdicts")
    print("-" * 60)
    for rec in chaos:
        print(f"  {rec['scenario']:<26}{rec['verdict']:<6}"
              f"invariants {rec['invariants_held']}/{rec['invariants_total']}")
    print("-" * 60)
    print()
    print("Output files:")
    for label, path in paths.items():
        print(f"  {label:<12} {path if path else figures_note}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Stage 11 Phase 11H research evaluation experiment "
                    "(drives the live API; stdlib only; SANDBOX ONLY).")
    parser.add_argument("--api-url", default=DEFAULT_API_URL,
                        help=f"API base URL (default {DEFAULT_API_URL})")
    parser.add_argument("--seed", type=int, default=SEED,
                        help=f"recorded in metadata; the pipeline is "
                             f"deterministic (default {SEED})")
    parser.add_argument("--verbose", action="store_true",
                        help="log details (raw requests; keys never printed)")
    args = parser.parse_args(argv)

    base = args.api_url.rstrip("/")

    # Figure policy: only when matplotlib already exists in the environment.
    try:
        import matplotlib  # noqa: F401
        figures_enabled = True
        figures_note = "(figure enabled)"
    except ImportError:
        figures_enabled = False
        figures_note = "(skipped: matplotlib not available)"

    print(f"Stage 11 Phase 11H — research evaluation — API: {base}  "
          f"seed: {args.seed}")
    print("SANDBOX ONLY — synthetic demo corpus; no real money moves.")
    print(f"Figures: {figures_note}")
    print()

    try:
        environment = preflight(base, args.verbose)
        reset_demo(base, args.verbose)

        print()
        print("E1 — policy comparison (simulator, demo source) ...")
        sim = run_e1_policy_comparison(base, args.verbose)
        run_id = str(sim["run_id"]).replace("/", "-").replace(":", "-")

        print()
        print("E3 — reliability under faults (chaos, 10 scenarios) ...")
        chaos = run_e3_chaos(base, args.verbose)

        print()
        print("E2 — safety-gate contribution (measured, honestly scoped) ...")
        e2 = build_e2_gate_contribution(sim, chaos)
        print(f"  v1 gate vetoes on corpus: "
              f"{e2['v1_gate_vetoes_on_corpus']}")
        for scenario, ev in sorted(e2["chaos_gate_invariants"].items()):
            print(f"  {scenario}: {ev['verdict']} — "
                  f"{ev['invariants_held']}/{ev['invariants_total']} "
                  f"invariants held")

        print()
        print("E4 — operational latency snapshots (/metrics) ...")
        e4 = run_e4_latency(base, args.verbose)
        for series, entry in e4.get("latency_snapshots", {}).items():
            print(f"  {series}: n={entry.get('count')} "
                  f"avg={entry.get('avg_ms'):.1f}ms "
                  f"min={entry.get('min_ms'):.1f} "
                  f"max={entry.get('max_ms'):.1f}")

        file_dirs = {
            "figures_enabled": figures_enabled,
            "files": {},  # filled after write_outputs
        }
        metadata = build_metadata(args, sim, environment, file_dirs)
        paths = write_outputs(metadata, sim, e2, chaos, e4, run_id,
                              figures_enabled)
        file_dirs["files"] = {k: v for k, v in paths.items() if v}
        # rewrite the config with the final file list recorded
        with open(paths["config"], "w", encoding="utf-8") as fh:
            json.dump({"experiment": "stage11_phase_11h",
                       "run_id": run_id, **metadata}, fh, indent=2,
                      ensure_ascii=False)

        print_console_report(sim, chaos, paths, figures_note)

        chaos_ok = all(rec["verdict"] in ("PASS", "SKIP") for rec in chaos)
        success = chaos_ok  # simulator run raising means we never get here
        print(f"Verdict: {'SUCCESS' if success else 'FAILURE'} "
              f"(chaos: {sum(1 for r in chaos if r['verdict'] == 'PASS')}"
              f" PASS / {sum(1 for r in chaos if r['verdict'] == 'SKIP')}"
              f" SKIP / {sum(1 for r in chaos if r['verdict'] == 'FAIL')}"
              f" FAIL of {len(chaos)})")
        print("Autonomous recovery operates on a simulated sandbox "
              "provider. No real financial transaction is performed.")
        return 0 if success else 1

    except ApiError as exc:
        if exc.status == 429:
            print(f"    STOP: {exc}")
        else:
            print(f"    FAIL: {exc}")
        return 1
    except urllib.error.URLError as exc:
        reason = getattr(exc, "reason", exc)
        print(f"error: cannot reach API at {base}: {reason}")
        print("Start the server first:  uvicorn api.main:app --reload")
        if args.verbose:
            import traceback
            traceback.print_exc()
        return 1
    except RuntimeError as exc:
        print(f"error: {exc}")
        if args.verbose:
            import traceback
            traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
