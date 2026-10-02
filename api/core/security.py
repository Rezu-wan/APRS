"""
api/core/security.py — authentication & authorization foundation.

This stage ships a pragmatic API-key foundation, not full IAM:

  * callers authenticate with the ``X-API-Key`` header
  * each key maps to exactly one role: SYSTEM | ADMIN | SUPPORT | CUSTOMER
  * keys come exclusively from the environment (see .env.example); the
    application refuses none but WARNS when the insecure dev defaults are in
    use
  * endpoints declare the roles they accept via require_roles(...)

This is deliberately easy to replace later: every protected endpoint depends
on AuthContext, so swapping in JWT/OAuth2 in a later stage means changing only
get_auth_context — no route changes. No passwords exist in this system yet;
when they do, they must be stored hashed (e.g. bcrypt) — never plaintext.
"""

from __future__ import annotations

import hmac
from dataclasses import dataclass

from fastapi import Depends, Header

from api.core.config import DEV_KEY_WARNING, Settings, get_settings
from api.core.exceptions import AuthenticationError, ForbiddenError

ROLES = ("SYSTEM", "ADMIN", "SUPPORT", "CUSTOMER")


@dataclass(frozen=True)
class AuthContext:
    role: str
    key_name: str
    # Stage 9: set for CUSTOMER-role keys — the customer identity this key
    # speaks for (ownership scoping, see can_access_transaction). None for
    # staff roles and for keys that carry no identity.
    customer_id: str | None = None


def _key_map(settings: Settings) -> dict[str, AuthContext]:
    key_map = {
        settings.api_key_system: AuthContext("SYSTEM", "api_key_system"),
        settings.api_key_admin: AuthContext("ADMIN", "api_key_admin"),
        settings.api_key_support: AuthContext("SUPPORT", "api_key_support"),
        # legacy single-customer key: identity "dev-customer" (owns nothing
        # in tests — pins the unowned-access-is-403 contract)
        settings.api_key_customer: AuthContext(
            "CUSTOMER", "api_key_customer", customer_id="dev-customer"
        ),
    }
    # Stage 9: additional customer identities from CUSTOMER_API_KEYS.
    # key_name is a NON-SECRET label ("customer:<id>") — the raw key must
    # never become a rate-limit bucket label or appear in logs.
    for key, customer_id in settings.customer_key_map.items():
        if key in key_map:
            continue  # legacy entry above already covers it
        key_map[key] = AuthContext(
            "CUSTOMER", f"customer:{customer_id}", customer_id=customer_id
        )
    return key_map


def get_auth_context(
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
    settings: Settings = Depends(get_settings),
) -> AuthContext:
    if not x_api_key:
        _audit_auth_failure("missing X-API-Key header")  # Stage 9: audit trail
        raise AuthenticationError("missing X-API-Key header")
    for key, ctx in _key_map(settings).items():
        # constant-time comparison: dict lookup on attacker-controlled
        # strings is not timing-safe
        if hmac.compare_digest(key, x_api_key):
            return ctx
    _audit_auth_failure("invalid API key")  # Stage 9: audit trail
    raise AuthenticationError("invalid API key")


def _audit_auth_failure(reason: str) -> None:
    """Stage 9: best-effort security-audit row for failed authentication.
    Lazy import keeps this module dependency-light; the audit service never
    raises and never logs the key value — only the fact of the failure."""
    try:
        from api.services.audit import record_auth_failure

        record_auth_failure(reason=reason)
    except Exception:  # noqa: BLE001 — audit must never block authentication
        pass


def can_access_transaction(auth: AuthContext, tx) -> bool:
    """Ownership rule for transaction-scoped reads (Stage 9).

    Staff roles (SYSTEM/ADMIN/SUPPORT) keep full read access — unchanged.
    CUSTOMER may read a transaction only when ``tx.user_id`` equals the
    identity bound to their key (``auth.customer_id``).

    Callers must raise ForbiddenError (403) when this returns False —
    including when the transaction itself was not found. The 403 (not 404)
    is deliberate: it does not enumerate which transaction ids exist.
    Existing tests pin the 403 contract for unowned access.
    """
    if auth.role in ("SYSTEM", "ADMIN", "SUPPORT"):
        return True
    if auth.role == "CUSTOMER":
        return tx is not None and auth.customer_id is not None and tx.user_id == auth.customer_id
    return False


def key_name_from_request(request) -> str | None:
    """Stage 9 audit helper: resolve the X-API-Key header to its KEY NAME
    label (e.g. "api_key_admin" / "customer:alice") WITHOUT ever exposing the
    raw key. Returns None for unknown/missing keys."""
    x_api_key = request.headers.get("X-API-Key")
    if not x_api_key:
        return None
    for key, ctx in _key_map(get_settings()).items():
        if hmac.compare_digest(key, x_api_key):
            return ctx.key_name
    return None


def require_roles(*allowed: str):
    """Dependency factory: restrict an endpoint to the given roles."""

    def dependency(
        auth: AuthContext = Depends(get_auth_context),
    ) -> AuthContext:
        if auth.role not in allowed:
            raise ForbiddenError(
                f"role {auth.role} may not perform this action "
                f"(allowed: {', '.join(allowed)})"
            )
        return auth

    return dependency
