"""
api/routes/explanations.py — GenAI explanation endpoints.

Read-only against authoritative data: the handler explains an already-made
decision and writes only to the ai_explanations cache.

Stage 9: CUSTOMER is opened with OWNERSHIP scoping — a customer key may
explain only transactions whose user_id matches its bound identity, and its
explanations are force-locked to the customer audience (language bn) so no
internal/support framing or risk internals can be requested. Unowned and
unknown ids both return a non-enumerating 403.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from api.core.config import get_settings
from api.core.exceptions import ForbiddenError
from api.core.security import AuthContext, can_access_transaction, require_roles
from api.db.database import get_db
from api.services.ai import AIProvider, get_ai_provider
from api.services.ai.schemas import (
    Audience,
    ExplanationResponse,
    Language,
)
from api.services.explanation_service import get_or_create_explanation
from api.services.transaction_service import get_transaction

router = APIRouter(prefix="/api/v1/explanations", tags=["explanations"])


class ExplanationAPIRequest(BaseModel):
    """Client-controlled input is limited to these three fields — everything
    else (decision, risk, timeline) is loaded server-side and can never be
    injected by the caller."""

    transaction_id: str = Field(min_length=1, max_length=64, examples=["TXN-123456"])
    language: Language  # bn | en (anything else is rejected with 422)
    audience: Audience  # customer | support | system


@router.post(
    "/transaction",
    response_model=ExplanationResponse,
    responses={
        404: {"description": "Transaction not found"},
        403: {"description": "Role not permitted to request explanations"},
    },
)
def explain_transaction(
    payload: ExplanationAPIRequest,
    db: Session = Depends(get_db),
    # Stage 9: CUSTOMER opened with OWNERSHIP scoping + audience lock-down.
    auth: AuthContext = Depends(require_roles("SYSTEM", "ADMIN", "SUPPORT", "CUSTOMER")),
    provider: AIProvider = Depends(get_ai_provider),
):
    """Explain (or replay the cached explanation for) a transaction's recovery
    decision. The provider is resolved at request time via the AI provider
    factory — nothing AI-related is loaded at startup. GenAI explains
    decisions; it never makes them.

    CUSTOMER keys: only their OWN transactions (user_id match), and the
    explanation is force-locked to audience=customer, language=bn — the
    service already strips internals for that audience."""
    language = payload.language.value
    audience = payload.audience.value
    if auth.role == "CUSTOMER":
        tx = get_transaction(db, payload.transaction_id)
        if not can_access_transaction(auth, tx):
            raise ForbiddenError("transaction not accessible")
        language, audience = "bn", "customer"
    return get_or_create_explanation(
        db,
        provider,
        get_settings(),
        transaction_id=payload.transaction_id,
        language=language,
        audience=audience,
    )
