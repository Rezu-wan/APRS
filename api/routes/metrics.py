"""api/routes/metrics.py — Stage 11G operational metrics endpoint.

GET /api/v1/metrics — SYSTEM/ADMIN only. Returns the in-process registry
snapshot plus process uptime. By construction the payload contains NO
request/user identifiers and NO amounts: metric names are code constants or
bounded path classes (see api.services.metrics.path_class).

In-process/per-process is a documented SANDBOX limitation (module docstring
of api.services.metrics).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from api.core.security import require_roles
from api.services.metrics import get_metrics, uptime_s

router = APIRouter(prefix="/api/v1", tags=["metrics"])


@router.get("/metrics")
def metrics_endpoint(
    auth=Depends(require_roles("SYSTEM", "ADMIN")),
) -> dict:
    snapshot = get_metrics().snapshot()
    return {"uptime_s": uptime_s(), **snapshot}
