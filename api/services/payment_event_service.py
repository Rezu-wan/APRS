"""
api/services/payment_event_service.py — Stage 6 payment-domain event
ingestion and Digital Twin integration.

ingest_events() implements POST /api/v1/transactions/{id}/payment-events:

  1. the transaction must already exist (payment events are evidence ABOUT a
     transaction, never a way to create one)
  2. each payload is validated by PaymentEventIn (schema), and the
     authoritative (source, stage, outcome) triple is derived from
     EVENT_TYPE_INFO — the client cannot mislabel evidence
  3. rows are inserted unless provider_event_id already exists (replayed
     provider redelivery) — duplicates are counted, never re-inserted;
     a same-id payload whose canonical digest differs from the stored row
     is a CONFLICT (EVENT_CONFLICT, HTTP 409) and is never accepted
  4. one commit per batch; a concurrent duplicate INSERT that loses the
     UNIQUE(provider_event_id) race is rolled back and re-counted as a
     duplicate (same race-handling style as recovery_service)

get_payment_events() orders by event_timestamp — the DOMAIN time carried by
the event — not insertion order: providers redeliver and replay events
out of order, so arrival order is meaningless evidence.

record_root_cause_event() appends the reconstruction engine's verdict to the
EXISTING transaction-state Digital Twin. It is an OBSERVATION, not a state
transition: previous_state == new_state == tx.current_state, and the state
machine's STATE_EVENT_TYPES is deliberately left untouched.
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import timezone

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from api.core.exceptions import ConflictError, NotFoundError
from api.core.payment_lifecycle import EVENT_TYPE_INFO
from api.db.models import DigitalTwinEvent, PaymentEvent, Transaction
from api.services.digital_twin import append_event
from api.services.transaction_service import get_transaction

logger = logging.getLogger("payment_recovery.payment_events")

ROOT_CAUSE_EVENT_TYPE = "ROOT_CAUSE_IDENTIFIED"
_RECONSTRUCTION_VERSION = "1"


def event_payload_digest(
    *,
    event_type: str,
    source: str,
    status: str,
    event_timestamp,
    reference_id: str | None,
    latency_ms: int | None,
) -> str:
    """Canonical digest of the identity-defining fields of a payment event.

    Two payloads with the same provider_event_id and the same digest are a
    REPLAY (idempotent redelivery); the same id with a different digest is a
    CONFLICT (never accepted). Only the digest — never the raw payload —
    travels into logs/audit records.
    """
    ts = event_timestamp
    if hasattr(ts, "tzinfo") and ts.tzinfo is not None:
        # normalize to naive UTC: the DB column is timezone-naive, incoming
        # payloads are tz-aware — both sides must digest identically
        ts = ts.astimezone(timezone.utc).replace(tzinfo=None)
    canonical = json.dumps(
        {
            "event_type": event_type,
            "source": source,
            "status": status,
            "event_timestamp": ts.isoformat() if hasattr(ts, "isoformat") else str(ts),
            "reference_id": reference_id,
            "latency_ms": latency_ms,
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _audit_conflict(transaction_id: str, provider_event_id: str,
                    incoming_digest: str, stored_digest: str) -> None:
    """DEFENSIVE audit hook — an audit failure must NEVER break ingestion."""
    try:
        from api.services.audit import (  # lazy import
            AUDIT_PAYMENT_EVENT_CONFLICT,
            record_security_event,
        )

        record_security_event(
            actor_type="SYSTEM",
            actor_id=None,
            action=AUDIT_PAYMENT_EVENT_CONFLICT,
            resource_type="payment_event",
            resource_id=provider_event_id,
            result="DENIED",
            reason="payment event redelivered with different content",
            # digests only — never the raw payloads
            audit_metadata={
                "transaction_id": transaction_id,
                "provider_event_id": provider_event_id,
                "incoming_digest": incoming_digest,
                "stored_digest": stored_digest,
            },
        )
    except Exception:  # pragma: no cover — audit must never break ingestion
        logger.warning(
            "audit of payment-event conflict failed (non-fatal): tx=%s peid=%s",
            transaction_id, provider_event_id,
        )


def _event_out(e: PaymentEvent) -> dict:
    return {
        "event_id": e.event_id,
        "provider_event_id": e.provider_event_id,
        "event_type": e.event_type,
        "source": e.source,
        "status": e.status,
        "event_timestamp": e.event_timestamp,
        "reference_id": e.reference_id,
        "latency_ms": e.latency_ms,
        "metadata": e.event_metadata,
    }


def ingest_events(db: Session, transaction_id: str, payloads: list) -> dict:
    """Ingest a batch of payment-domain events for an existing transaction.

    Idempotent per provider_event_id: replays count as duplicates. Commits
    once. Returns {"transaction_id", "created", "duplicates", "events"}.
    """
    tx = get_transaction(db, transaction_id)
    if tx is None:
        raise NotFoundError(f"transaction {transaction_id} not found")

    created: list[PaymentEvent] = []
    duplicates = 0
    for p in payloads:
        # authoritative vocabulary triple, not the client's claim
        try:
            info = EVENT_TYPE_INFO[p.event_type]
        except KeyError:
            # Stage 9: batch-level rejection audit (defensive — the schema
            # validates event_type first; this guards non-schema callers).
            # 409 event-content conflicts are audited separately by the
            # conflict-detection path above.
            from api.services.audit import (
                AUDIT_PAYMENT_EVENT_REJECTED,
                record_security_event,
            )

            record_security_event(
                actor_type="ANONYMOUS",
                actor_id=None,
                action=AUDIT_PAYMENT_EVENT_REJECTED,
                resource_type="payment_event_batch",
                resource_id=transaction_id,
                result="DENIED",
                reason=f"rejected {getattr(p, 'provider_event_id', '?')}: "
                       "unknown event_type",
            )
            raise
        existing = db.scalars(
            select(PaymentEvent).where(
                PaymentEvent.provider_event_id == p.provider_event_id
            )
        ).one_or_none()
        if existing is not None:
            incoming_digest = event_payload_digest(
                event_type=p.event_type,
                source=info["source"],
                status=info["outcome"],
                event_timestamp=p.event_timestamp,
                reference_id=p.reference_id,
                latency_ms=p.latency_ms,
            )
            stored_digest = event_payload_digest(
                event_type=existing.event_type,
                source=existing.source,
                status=existing.status,
                event_timestamp=existing.event_timestamp,
                reference_id=existing.reference_id,
                latency_ms=existing.latency_ms,
            )
            if incoming_digest != stored_digest:
                # same provider_event_id, different content: never accepted,
                # and the differing values are NOT named (do not leak the
                # stored evidence) — only the digests reach the audit trail
                db.rollback()
                logger.warning(
                    "payment event conflict: tx=%s provider_event_id=%s",
                    transaction_id, p.provider_event_id,
                )
                _audit_conflict(
                    transaction_id, p.provider_event_id,
                    incoming_digest, stored_digest,
                )
                raise ConflictError(
                    f"payment event {p.provider_event_id} already exists "
                    f"with different content",
                    code="EVENT_CONFLICT",
                )
            duplicates += 1
            continue
        event = PaymentEvent(
            transaction_id=transaction_id,
            provider_event_id=p.provider_event_id,
            event_type=p.event_type,
            source=info["source"],
            status=info["outcome"],
            event_timestamp=p.event_timestamp,
            reference_id=p.reference_id,
            latency_ms=p.latency_ms,
            event_metadata=p.metadata,
        )
        db.add(event)
        try:
            db.flush()
        except IntegrityError:
            # lost the UNIQUE(provider_event_id) race against a concurrent
            # batch — rollback discards our partial batch; re-run from scratch,
            # where the dedup query now sees the winner's committed rows
            db.rollback()
            logger.info(
                "concurrent duplicate payment event race: tx=%s replaying batch",
                transaction_id,
            )
            return ingest_events(db, transaction_id, payloads)
        created.append(event)

    db.commit()
    for e in created:
        db.refresh(e)
    logger.info(
        "payment events ingested: tx=%s created=%d duplicates=%d",
        transaction_id, len(created), duplicates,
    )
    return {
        "transaction_id": transaction_id,
        "created": len(created),
        "duplicates": duplicates,
        "events": [_event_out(e) for e in created],
    }


def get_payment_events(db: Session, transaction_id: str) -> list[PaymentEvent]:
    """All payment-domain events for a transaction, ordered by domain time.

    event_timestamp is the AUTHORITATIVE ordering key (with id as a stable
    tiebreaker for same-timestamp events), NOT insertion order: providers
    redeliver and replay events out of order, so arrival order carries no
    evidentiary meaning. The reconstruction engine depends on this ordering.
    """
    stmt = (
        select(PaymentEvent)
        .where(PaymentEvent.transaction_id == transaction_id)
        .order_by(PaymentEvent.event_timestamp.asc(), PaymentEvent.id.asc())
    )
    return list(db.scalars(stmt))


def record_root_cause_event(db: Session, tx: Transaction, reconstruction: dict) -> bool:
    """Append the reconstruction verdict to the transaction-state Digital Twin.

    An OBSERVATION, not a transition: previous_state == new_state ==
    tx.current_state — the state machine is untouched.

    Idempotent on root cause: if the latest ROOT_CAUSE_IDENTIFIED event for
    the transaction already carries the same root cause, nothing is appended
    (returns False); a changed verdict appends a new observation.

    The CALLER owns the commit.
    """
    latest = db.scalars(
        select(DigitalTwinEvent)
        .where(
            DigitalTwinEvent.transaction_id == tx.transaction_id,
            DigitalTwinEvent.event_type == ROOT_CAUSE_EVENT_TYPE,
        )
        .order_by(DigitalTwinEvent.timestamp.desc(), DigitalTwinEvent.id.desc())
        .limit(1)
    ).one_or_none()

    root_cause = reconstruction["root_cause"]
    if latest is not None:
        meta = latest.event_metadata or {}
        if meta.get("root_cause") == root_cause:
            return False

    stage_statuses = reconstruction.get("stage_statuses") or {}
    append_event(
        db,
        tx,
        previous_state=tx.current_state,
        new_state=tx.current_state,
        event_type=ROOT_CAUSE_EVENT_TYPE,
        reason=f"Root cause: {root_cause}",
        event_metadata={
            "root_cause": root_cause,
            "failure_stage": reconstruction.get("failure_stage"),
            "last_successful_stage": reconstruction.get("last_successful_stage"),
            "stage_statuses": {
                stage: stage_statuses.get(stage) for stage in
                ("BANK_DEBIT", "GATEWAY", "MERCHANT_CONFIRMATION", "SETTLEMENT")
            },
            "missing_events": reconstruction.get("missing_events"),
            "reconstruction_version": _RECONSTRUCTION_VERSION,
        },
    )
    logger.info(
        "root cause recorded: tx=%s root_cause=%s", tx.transaction_id, root_cause
    )
    return True
