"""
api/services/recovery_verifier.py — Stage 8 post-execution VERIFICATION
(VERIFIER_VERSION "v1").

PURE: the caller supplies the ledger entry (from the provider service), the
fresh events, the recovery row(s) and the release timestamp. This module
NEVER imports the provider and NEVER touches the DB — it is a pure judge of
the evidence it is handed.

Checks (fixed order; FIRST failure still records the remaining context but
the overall result is failed=False):

  1. ledger_entry exists and its status is RELEASED
  2. released amount == requested amount (float compare with tolerance
     0.001 — currency amounts arrive as binary floats from JSON/DB layers;
     an exact == would reject legitimate values like 99.999999 vs 100.0.
     0.001 is well below the smallest cent-level discrepancy that matters
     and above float noise for realistic amounts)
  3. provider_reference present and matches the recovery row's
     provider_reference (proof the release is THIS recovery's)
  4. transaction state is not SUCCESS
  5. NO SETTLEMENT_CONFIRMED event arrived AFTER the release (spec
     section 19: a post-execution success means the provider settled
     anyway — releasing on top would double-pay; verification FAILS to
     force a safe manual state)
  6. the recovery row being verified is the ONLY non-BLOCKED row for the
     transaction (no competing concurrent recovery)

All pass -> passed=True.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Sequence

from api.schemas.recovery_autonomous import VerificationResult

VERIFIER_VERSION = "v1"

_RELEASED_STATUS = "RELEASED"
_SETTLEMENT_CONFIRMED_EVENT = "SETTLEMENT_CONFIRMED"

# Float comparison tolerance for currency amounts — see module docstring.
AMOUNT_TOLERANCE = 0.001


def verify_release(
    tx: Any,
    recovery_row: Any,
    ledger_entry: dict | None,
    fresh_events: Sequence[Any],
    released_after: datetime,
    *,
    all_recovery_rows: Sequence[Any] = (),
    now: datetime | None = None,
) -> VerificationResult:
    """Judge whether a limit release actually happened, correctly, for THIS
    recovery, with no competing recovery and no post-execution success.

    ``tx``: object with ``current_state``.
    ``recovery_row``: object with ``requested_amount`` and
        ``provider_reference``.
    ``ledger_entry``: provider-side dict (or None) with ``status``,
        ``released_amount``, ``provider_reference``.
    ``fresh_events``: objects with ``event_type`` and ``event_timestamp``.
    ``released_after``: the execution timestamp — any settlement confirmed
        AFTER this moment fails verification.
    ``all_recovery_rows``: every recovery row for the transaction (the
        caller queries; this module stays pure).
    """
    verified_at = now or released_after
    checks: list[dict] = []

    def _record(name: str, passed: bool, detail: str) -> None:
        checks.append({"name": name, "passed": passed, "detail": detail})

    # (1) ledger entry exists and is released
    if ledger_entry is None:
        _record("ledger_released", False, "no ledger entry found")
    else:
        status = str(ledger_entry.get("status", ""))
        ok = status == _RELEASED_STATUS
        _record(
            "ledger_released", ok,
            f"ledger status {status!r}" if ok
            else f"ledger status is {status!r}, expected {_RELEASED_STATUS!r}",
        )

    # (2) amount equality within tolerance
    requested = float(getattr(recovery_row, "requested_amount", 0.0))
    if ledger_entry is None or "released_amount" not in ledger_entry:
        _record("amount_matches", False, "no released amount on ledger entry")
    else:
        released = float(ledger_entry["released_amount"])
        ok = abs(released - requested) <= AMOUNT_TOLERANCE
        _record(
            "amount_matches", ok,
            f"released {released} vs requested {requested} "
            f"(tolerance {AMOUNT_TOLERANCE})",
        )

    # (3) provider reference match
    row_ref = getattr(recovery_row, "provider_reference", None)
    if ledger_entry is None:
        _record("provider_reference", False, "no ledger entry to compare")
    else:
        ledger_ref = ledger_entry.get("provider_reference")
        ok = (
            bool(ledger_ref)
            and bool(row_ref)
            and str(ledger_ref) == str(row_ref)
        )
        _record(
            "provider_reference", ok,
            f"ledger {ledger_ref!r} vs recovery row {row_ref!r}",
        )

    # (4) transaction must not have succeeded meanwhile
    state = str(getattr(tx, "current_state", ""))
    ok = state != "SUCCESS"
    _record(
        "tx_not_success", ok,
        f"transaction state {state}"
        + ("" if ok else " — payment succeeded, release must be reviewed"),
    )

    # (5) no settlement confirmed after the release (spec section 19)
    late_settlements = [
        e for e in fresh_events
        if str(getattr(e, "event_type", "")) == _SETTLEMENT_CONFIRMED_EVENT
        and getattr(e, "event_timestamp") > released_after
    ]
    ok = not late_settlements
    _record(
        "no_settlement_after_release", ok,
        f"{len(late_settlements)} settlement confirmation(s) after the "
        "release" + (" — possible double payment" if not ok else ""),
    )

    # (6) this recovery row is the only non-BLOCKED row for the transaction
    active_others = [
        r for r in all_recovery_rows
        if r is not recovery_row
        and r != recovery_row  # equal row = same recovery (e.g. reloaded)
        and str(getattr(r, "status", "")) != "BLOCKED"
    ]
    ok = not active_others
    _record(
        "single_active_recovery", ok,
        f"{len(active_others)} other non-BLOCKED recovery row(s)"
        + (" — concurrent recovery detected" if not ok else ""),
    )

    return VerificationResult(
        passed=all(c["passed"] for c in checks),
        checks=checks,
        verified_at=verified_at,
    )
