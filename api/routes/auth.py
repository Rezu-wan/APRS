"""
api/routes/auth.py — frontend session bootstrap.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from api.core.security import AuthContext, get_auth_context

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])


class MeResponse(BaseModel):
    role: str
    key_name: str


@router.get("/me", response_model=MeResponse)
def who_am_i(auth: AuthContext = Depends(get_auth_context)) -> MeResponse:
    """Identity probe for the frontend session bootstrap: validates the
    X-API-Key and returns the role it maps to. All four roles allowed —
    this is how a client learns its own role (the backend stays the
    authorization authority; the frontend uses this ONLY for UI visibility)."""
    return MeResponse(role=auth.role, key_name=auth.key_name)
