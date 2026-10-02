"""
api/routes/explanations.py — GenAI explanation endpoints.

Read-only against authoritative data: the handler explains an already-made
decision and writes only to the ai_explanations cache. CUSTOMER role is
intentionally excluded for the same IDOR rationale as the transaction read
endpoints — there is no user-bound identity yet, so any CUSTOMER key could
read any transaction's explanation; that surface reopens once real identity
exists.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from api.core.config import get_settings
from api.core.security import AuthContext, require_roles
from api.db.database import get_db
from api.services.ai import AIProvider, get_ai_provider
from api.services.ai.schemas import (
    Audience,
    ExplanationResponse,
    Language,
)
from api.services.explanation_service import get_or_create_explanation

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
    auth: AuthContext = Depends(require_roles("SYSTEM", "ADMIN", "SUPPORT")),
    provider: AIProvider = Depends(get_ai_provider),
):
    """Explain (or replay the cached explanation for) a transaction's recovery
    decision. The provider is resolved at request time via the AI provider
    factory — nothing AI-related is loaded at startup. GenAI explains
    decisions; it never makes them."""
    return get_or_create_explanation(
        db,
        provider,
        get_settings(),
        transaction_id=payload.transaction_id,
        language=payload.language.value,
        audience=payload.audience.value,
    )
