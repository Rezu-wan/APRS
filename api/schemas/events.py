"""
api/schemas/events.py — health/system schemas.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class HealthResponse(BaseModel):
    status: str = Field(examples=["healthy"])
    database: str = Field(examples=["connected"])
    ml_models: str = Field(examples=["loaded"])
    environment: str = Field(examples=["development"])


class ErrorResponse(BaseModel):
    error: dict
