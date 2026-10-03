"""Customer-facing profile schemas."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated

from pydantic import BaseModel, BeforeValidator, ConfigDict


def _coerce_utc(v):
    """SQLite (dev DB) returns naive datetimes; re-attach UTC so API
    consumers always receive offset-designated ISO-8601 timestamps."""
    if isinstance(v, datetime) and v.tzinfo is None:
        return v.replace(tzinfo=timezone.utc)
    return v


UtcDatetime = Annotated[datetime, BeforeValidator(_coerce_utc)]


class CustomerProfileResponse(BaseModel):
    """The signed-in customer's OWN identity/profile. Served to CUSTOMER-role
    keys only, and always the caller's own row (key binding decides identity)
    — there is no path to read another customer's profile."""

    customer_id: str
    full_name: str
    email: str
    phone: str
    country: str
    status: str
    segment: str
    risk_profile: str
    archetype: str | None
    member_since: UtcDatetime
