"""api/routes/customers.py — the signed-in customer's own profile.

A CUSTOMER key can read exactly ONE customer row: the one its key binding
names (auth.customer_id). There is deliberately no list/search endpoint and
no {customer_id} path parameter — profile data cannot be enumerated.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from api.core.exceptions import ForbiddenError, NotFoundError
from api.core.security import AuthContext, require_roles
from api.db.database import get_db
from api.db.models import Customer
from api.schemas.customer import CustomerProfileResponse

router = APIRouter(prefix="/api/v1/customers", tags=["customers"])


@router.get("/me", response_model=CustomerProfileResponse)
def get_my_profile(
    db: Session = Depends(get_db),
    auth: AuthContext = Depends(require_roles("CUSTOMER")),
):
    """Profile of the customer this key speaks for (name, segment, archetype).

    404 (not 403) when the bound identity has no profile row: customer ids
    without a dataset profile (e.g. test identities) legitimately have no
    profile to show, and a 404 says nothing about other customers."""
    if not auth.customer_id:
        raise ForbiddenError("key carries no customer identity")
    row = db.query(Customer).filter(Customer.customer_id == auth.customer_id).first()
    if row is None:
        raise NotFoundError(f"no customer profile for {auth.customer_id}")
    return CustomerProfileResponse(
        customer_id=row.customer_id,
        full_name=row.full_name,
        email=row.email,
        phone=row.phone,
        country=row.country,
        status=row.status,
        segment=row.segment,
        risk_profile=row.risk_profile,
        archetype=row.archetype,
        member_since=row.created_at,
    )
