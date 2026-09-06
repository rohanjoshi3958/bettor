"""Operational metrics export (Prometheus text + JSON snapshot).

Admin-only: requires ``METRICS_ADMIN_TOKEN`` via Bearer or ``X-Metrics-Token``.
"""

from __future__ import annotations

import json
import secrets
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Response

from app.config import get_metrics_admin_token
from services.odds_metrics import render_prometheus, snapshot_dict

router = APIRouter(prefix="/api", tags=["metrics"])


def _provided_token(
    authorization: str | None,
    x_metrics_token: str | None,
) -> str | None:
    if x_metrics_token and x_metrics_token.strip():
        return x_metrics_token.strip()
    if authorization:
        scheme, _, value = authorization.partition(" ")
        if scheme.lower() == "bearer" and value.strip():
            return value.strip()
    return None


def _tokens_match(provided: str, expected: str) -> bool:
    if len(provided) != len(expected):
        return False
    return secrets.compare_digest(provided, expected)


def require_metrics_admin(
    authorization: Annotated[str | None, Header()] = None,
    x_metrics_token: Annotated[str | None, Header(alias="X-Metrics-Token")] = None,
) -> None:
    """Reject unauthenticated metrics scrapes."""
    expected = get_metrics_admin_token()
    if expected is None:
        raise HTTPException(
            status_code=503,
            detail="Metrics admin token is not configured (set METRICS_ADMIN_TOKEN)",
        ) from None
    provided = _provided_token(authorization, x_metrics_token)
    if provided is None or not _tokens_match(provided, expected):
        raise HTTPException(status_code=401, detail="Unauthorized") from None


@router.get("/metrics")
def metrics(
    _admin: Annotated[None, Depends(require_metrics_admin)],
    format: str = Query(default="prometheus", pattern="^(prometheus|json)$"),
) -> Response:
    """Inspect Odds API and HTTP metrics collected in this process.

    - ``format=prometheus`` (default): Prometheus text exposition
    - ``format=json``: structured snapshot for ad-hoc inspection

    Requires ``METRICS_ADMIN_TOKEN`` as ``Authorization: Bearer …`` or
    ``X-Metrics-Token``.
    """
    if format == "json":
        return Response(
            content=json.dumps(snapshot_dict(), indent=2, sort_keys=True),
            media_type="application/json",
            headers={"Cache-Control": "no-store"},
        )
    return Response(
        content=render_prometheus(),
        media_type="text/plain; version=0.0.4; charset=utf-8",
        headers={"Cache-Control": "no-store"},
    )
