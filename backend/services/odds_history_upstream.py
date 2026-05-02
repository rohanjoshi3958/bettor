"""
Optional backfill for line-history charts using The Odds API historical snapshots.

Requires a paid Odds API plan with historical access. Controlled with env
BETTOR_ODDS_HISTORY_BACKFILL (default on). Each snapshot request counts toward
historical quota — cap steps with BETTOR_HISTORY_MAX_STEPS (default 120).
Soccer h2h uses per-event historical URLs (not bulk sport odds) so each snapshot is small and fast to parse.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

from services.odds.service import (
    ODDS_BASE,
    SOCCER_SPORT_KEYS,
    _BOOKMAKERS_PARAM,
    _ODDS_REGIONS,
    _collect_h2h_prices,
    _collect_prop_prices,
    _format_prop_pick,
    _commence_time_utc,
    _normalize_event_id,
)


def _history_backfill_enabled() -> bool:
    raw = os.environ.get("BETTOR_ODDS_HISTORY_BACKFILL", "1").strip().lower()
    return raw not in ("0", "false", "no", "off")


def _max_steps() -> int:
    try:
        return max(8, min(int(os.environ.get("BETTOR_HISTORY_MAX_STEPS", "120")), 500))
    except ValueError:
        return 120


def _earliest_query_utc(commence_raw: str | None) -> datetime:
    """Do not request snapshots before this time (props rarely meaningful weeks out)."""
    ct = _commence_time_utc(commence_raw or "") if commence_raw else None
    if ct is not None:
        return ct - timedelta(days=6)
    return datetime.now(timezone.utc) - timedelta(days=3)


def _best_for_h2h_pick(ev: dict[str, Any], pick_label: str) -> tuple[float, str] | None:
    prices = _collect_h2h_prices(ev)
    key = pick_label.strip()
    book_prices = prices.get(key)
    if not book_prices:
        return None
    best_book, best_dec = max(book_prices, key=lambda x: x[1])
    return float(best_dec), str(best_book)


def _best_for_prop_pick(
    ev: dict[str, Any], market_key: str, pick_label: str
) -> tuple[float, str] | None:
    grouped = _collect_prop_prices(ev, {market_key})
    for key, book_prices in grouped.items():
        if not book_prices:
            continue
        mk, desc, oname, pt = key
        if mk != market_key:
            continue
        if _format_prop_pick(mk, desc, oname, pt) != pick_label:
            continue
        best_book, best_dec = max(book_prices, key=lambda x: x[1])
        return float(best_dec), str(best_book)
    return None


def _parse_snapshot_ts(raw: str | None) -> datetime | None:
    if not raw or not isinstance(raw, str):
        return None
    try:
        s = raw[:-1] + "+00:00" if raw.endswith("Z") else raw
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except ValueError:
        return None


async def fetch_upstream_line_points(
    client: httpx.AsyncClient,
    api_key: str,
    *,
    sport_key: str,
    event_id: str,
    pick_label: str,
    market_key: str | None,
    commence_time: str | None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """
    Walk Odds API historical snapshots backward using previous_timestamp.

    Returns (points, meta) where each point is
    {t, decimal_odds, book, source: "the_odds_api"}.
    """
    meta: dict[str, Any] = {"upstream_steps": 0, "upstream_error": None}
    if not _history_backfill_enabled():
        return [], meta

    eid = _normalize_event_id(event_id)
    sk = sport_key.strip()
    pick = pick_label.strip()
    mk = (market_key or "").strip() or None
    if sk in SOCCER_SPORT_KEYS and not mk:
        mk = "h2h"
    earliest = _earliest_query_utc(commence_time)

    if mk != "h2h" and not mk:
        meta["upstream_error"] = "market_key required for historical prop backfill"
        return [], meta

    points: list[dict[str, Any]] = []
    date_q = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    prev_seen: str | None = None
    max_steps = _max_steps()

    for step in range(max_steps):
        try:
            url = f"{ODDS_BASE}/historical/sports/{sk}/events/{eid}/odds"
            if mk == "h2h":
                params: dict[str, Any] = {
                    "apiKey": api_key,
                    "regions": _ODDS_REGIONS,
                    "bookmakers": _BOOKMAKERS_PARAM,
                    "markets": "h2h",
                    "oddsFormat": "decimal",
                    "date": date_q,
                }
            else:
                params = {
                    "apiKey": api_key,
                    "regions": _ODDS_REGIONS,
                    "bookmakers": _BOOKMAKERS_PARAM,
                    "markets": mk,
                    "oddsFormat": "decimal",
                    "date": date_q,
                }
            r = await client.get(url, params=params, timeout=45.0)
            r.raise_for_status()
            body = r.json()
            if not isinstance(body, dict):
                break
            ts = body.get("timestamp")
            prev = body.get("previous_timestamp")
            ev = body.get("data")
            if not isinstance(ev, dict):
                break
            if mk == "h2h":
                row = _best_for_h2h_pick(ev, pick)
            else:
                row = _best_for_prop_pick(ev, mk, pick)
            if row and ts:
                dec, book = row
                points.append(
                    {
                        "t": ts,
                        "decimal_odds": round(dec, 4),
                        "book": book,
                        "source": "the_odds_api",
                    }
                )
            meta["upstream_steps"] = step + 1
            if not prev or not isinstance(prev, str):
                break
            if prev == prev_seen:
                break
            prev_seen = prev
            pdt = _parse_snapshot_ts(prev)
            if pdt is not None and pdt < earliest:
                break
            date_q = prev
        except httpx.HTTPStatusError as e:
            code = e.response.status_code if e.response is not None else 0
            meta["upstream_error"] = f"HTTP {code} from historical odds API"
            break
        except httpx.HTTPError as e:
            meta["upstream_error"] = str(e) or "historical request failed"
            break

    # chronological for merge
    points.sort(key=lambda p: p["t"])
    meta["truncated"] = bool(meta.get("upstream_steps", 0) >= max_steps)
    return points, meta


def merge_local_and_upstream(
    local: list[dict[str, Any]],
    upstream: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Time-ordered; same `t` keeps local row when both exist."""
    by_t: dict[str, dict[str, Any]] = {}
    for p in upstream:
        t = p.get("t")
        if isinstance(t, str):
            by_t[t] = {**p, "source": p.get("source") or "the_odds_api"}
    for p in local:
        t = p.get("t")
        if isinstance(t, str):
            by_t[t] = {**p, "source": p.get("source") or "local"}
    return sorted(by_t.values(), key=lambda x: x["t"])
