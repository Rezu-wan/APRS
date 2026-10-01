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


def _key_map(settings: Settings) -> dict[str, AuthContext]:
    return {
        settings.api_key_system: AuthContext("SYSTEM", "api_key_system"),
        settings.api_key_admin: AuthContext("ADMIN", "api_key_admin"),
        settings.api_key_support: AuthContext("SUPPORT", "api_key_support"),
        settings.api_key_customer: AuthContext("CUSTOMER", "api_key_customer"),
    }


def get_auth_context(
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
    settings: Settings = Depends(get_settings),
) -> AuthContext:
    if not x_api_key:
        raise AuthenticationError("missing X-API-Key header")
    for key, ctx in _key_map(settings).items():
        # constant-time comparison: dict lookup on attacker-controlled
        # strings is not timing-safe
        if hmac.compare_digest(key, x_api_key):
            return ctx
    raise AuthenticationError("invalid API key")


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
