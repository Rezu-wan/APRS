"""
api/core/exceptions.py — application error taxonomy mapped to HTTP status codes.

Handlers in api/main.py translate these into safe JSON responses; stack traces
and driver/DB details are logged server-side, never returned to clients.
"""

from __future__ import annotations


class AppError(Exception):
    status_code = 500
    code = "INTERNAL_ERROR"

    def __init__(self, message: str, *, code: str | None = None):
        super().__init__(message)
        self.message = message
        if code is not None:
            self.code = code


class InvalidStateTransitionError(AppError):
    status_code = 400
    code = "INVALID_STATE_TRANSITION"


class NotFoundError(AppError):
    status_code = 404
    code = "NOT_FOUND"


class AuthenticationError(AppError):
    status_code = 401
    code = "AUTHENTICATION_REQUIRED"


class ForbiddenError(AppError):
    status_code = 403
    code = "INSUFFICIENT_PERMISSIONS"


class ConflictError(AppError):
    status_code = 409
    code = "CONFLICT"


class MLServiceError(AppError):
    status_code = 503
    code = "ML_MODELS_UNAVAILABLE"
