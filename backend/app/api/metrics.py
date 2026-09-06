"""Operational metrics export (Prometheus text + JSON snapshot)."""

from __future__ import annotations

import json

from fastapi import APIRouter, Query, Response

from services.odds_metrics import render_prometheus, snapshot_dict

router = APIRouter(prefix="/api", tags=["metrics"])


@router.get("/metrics")
def metrics(
    format: str = Query(default="prometheus", pattern="^(prometheus|json)$"),
) -> Response:
    """Inspect Odds API and HTTP metrics collected in this process.

    - ``format=prometheus`` (default): Prometheus text exposition
    - ``format=json``: structured snapshot for ad-hoc inspection
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
