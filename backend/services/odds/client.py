"""Provider/client layer: HTTP requests to The Odds API, authentication, quota/error handling.

Responsibilities:
- Define upstream API base URL and request parameters.
- Execute bulk h2h, events-schedule, and per-event props requests.
- Handle upstream errors (429, 402, transport failures) with graceful degradation.
- Retry prop requests once on 429 or timeout.
- Track quota events and produce user-facing warning messages.

All HTTP functions receive an `httpx.AsyncClient` from the orchestration layer so they
remain independently testable without creating their own client context.
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Any

import httpx

from services.odds.models import BetPick
from services.odds.normalization import (
    _events_sorted_by_kickoff,
    _events_upcoming_sorted_for_props,
    _normalize_event_id,
    shells_from_scheduled_events,
)
from services.odds.parser import _h2h_events_to_picks, _prop_event_to_picks

logger = logging.getLogger("bettor.odds.client")

ODDS_BASE = "https://api.the-odds-api.com/v4"

_BOOKMAKERS_PARAM = "draftkings,fanduel,fanatics"
_ODDS_REGIONS = "us"

# Prop coverage cap (each event = extra API calls). Override via env to save quota.
MAX_PROP_EVENTS_PER_SPORT = int(os.environ.get("MAX_PROP_EVENTS_PER_SPORT", "20"))
# Concurrent /events/{id}/odds calls — keep low to avoid 429 rate limits on The Odds API.
_PROP_FETCH_CONCURRENCY = int(os.environ.get("ODDS_PROP_CONCURRENCY", "4"))


# ---------------------------------------------------------------------------
# Quota tracking
# ---------------------------------------------------------------------------

def _note_odds_api_quota_issue(quota_events: list[str] | None, status_code: int) -> None:
    if quota_events is None:
        return
    if status_code == 429:
        quota_events.append("rate_limit")
    elif status_code == 402:
        quota_events.append("payment_required")


def _odds_api_warning_message(
    quota_events: list[str],
    *,
    has_usable_response: bool,
) -> str | None:
    """If `has_usable_response`, soften copy: one 429 among many parallel calls is common."""
    if not quota_events:
        return None
    if "rate_limit" in quota_events:
        if has_usable_response:
            return (
                "Some Odds API requests hit HTTP 429 (rate limit), so part of the slate may be "
                "missing. Wait before reloading; you can lower MAX_PROP_EVENTS_PER_SPORT or "
                "ODDS_PROP_CONCURRENCY. Check usage at the-odds-api.com."
            )
        return (
            "The Odds API returned rate limits (HTTP 429). Wait before reloading. "
            "You can lower usage with env MAX_PROP_EVENTS_PER_SPORT and ODDS_PROP_CONCURRENCY. "
            "Check remaining quota at the-odds-api.com."
        )
    if "payment_required" in quota_events:
        if has_usable_response:
            return (
                "Some Odds API requests returned HTTP 402 (quota or billing). "
                "You still have data below, but the slate may be incomplete. "
                "Check your plan at the-odds-api.com."
            )
        return (
            "The Odds API returned HTTP 402 (quota or billing). "
            "Check your plan and usage at the-odds-api.com."
        )
    return None


# ---------------------------------------------------------------------------
# HTTP request functions
# ---------------------------------------------------------------------------

async def _fetch_bulk_h2h(
    client: httpx.AsyncClient,
    api_key: str,
    sport_key: str,
    sport_title: str,
    commence_time_from: str | None = None,
    commence_time_to: str | None = None,
    quota_events: list[str] | None = None,
) -> tuple[list[BetPick], list[dict[str, Any]]]:
    url = f"{ODDS_BASE}/sports/{sport_key}/odds"
    params: dict[str, Any] = {
        "apiKey": api_key,
        "regions": _ODDS_REGIONS,
        "bookmakers": _BOOKMAKERS_PARAM,
        "markets": "h2h",
        "oddsFormat": "decimal",
    }
    if commence_time_from:
        params["commenceTimeFrom"] = commence_time_from
    if commence_time_to:
        params["commenceTimeTo"] = commence_time_to
    try:
        r = await client.get(url, params=params, timeout=45.0)
        r.raise_for_status()
        events = r.json()
        if not isinstance(events, list):
            return [], []
        ordered = _events_sorted_by_kickoff(events)
        picks = _h2h_events_to_picks(ordered, sport_key, sport_title)
        shells = shells_from_scheduled_events(events, sport_key, sport_title)
        return picks, shells
    except httpx.HTTPStatusError as e:
        status = e.response.status_code if e.response is not None else 0
        if e.response is not None:
            _note_odds_api_quota_issue(quota_events, status)
        logger.warning(
            "odds_upstream_http_error",
            extra={"endpoint": "bulk_h2h", "sport_key": sport_key, "status_code": status},
        )
        return [], []
    except httpx.HTTPError as e:
        logger.warning(
            "odds_upstream_transport_error",
            extra={"endpoint": "bulk_h2h", "sport_key": sport_key, "error_type": type(e).__name__},
        )
        return [], []
    except ValueError:
        # Body was not JSON (proxy error page, truncated response, ...).
        logger.warning(
            "odds_upstream_invalid_json",
            extra={"endpoint": "bulk_h2h", "sport_key": sport_key},
        )
        return [], []


async def _fetch_events(
    client: httpx.AsyncClient,
    api_key: str,
    sport_key: str,
    commence_time_from: str | None = None,
    commence_time_to: str | None = None,
    quota_events: list[str] | None = None,
) -> list[dict[str, Any]]:
    url = f"{ODDS_BASE}/sports/{sport_key}/events"
    params: dict[str, Any] = {"apiKey": api_key}
    if commence_time_from:
        params["commenceTimeFrom"] = commence_time_from
    if commence_time_to:
        params["commenceTimeTo"] = commence_time_to
    try:
        r = await client.get(url, params=params, timeout=30.0)
        r.raise_for_status()
        data = r.json()
        return data if isinstance(data, list) else []
    except httpx.HTTPStatusError as e:
        status = e.response.status_code if e.response is not None else 0
        if e.response is not None:
            _note_odds_api_quota_issue(quota_events, status)
        logger.warning(
            "odds_upstream_http_error",
            extra={"endpoint": "events", "sport_key": sport_key, "status_code": status},
        )
        return []
    except httpx.HTTPError as e:
        logger.warning(
            "odds_upstream_transport_error",
            extra={"endpoint": "events", "sport_key": sport_key, "error_type": type(e).__name__},
        )
        return []
    except ValueError:
        logger.warning(
            "odds_upstream_invalid_json",
            extra={"endpoint": "events", "sport_key": sport_key},
        )
        return []


async def _fetch_event_props(
    client: httpx.AsyncClient,
    api_key: str,
    sport_key: str,
    sport_title: str,
    event_id: str,
    markets_csv: str,
    allowed: set[str],
    quota_events: list[str] | None = None,
) -> list[BetPick]:
    url = f"{ODDS_BASE}/sports/{sport_key}/events/{event_id}/odds"
    params = {
        "apiKey": api_key,
        "regions": _ODDS_REGIONS,
        "bookmakers": _BOOKMAKERS_PARAM,
        "markets": markets_csv,
        "oddsFormat": "decimal",
    }
    for attempt in range(2):
        try:
            r = await client.get(url, params=params, timeout=45.0)
            r.raise_for_status()
            ev = r.json()
            if not isinstance(ev, dict):
                return []
            return _prop_event_to_picks(ev, sport_key, sport_title, allowed)
        except httpx.HTTPStatusError as e:
            code = e.response.status_code if e.response is not None else 0
            if e.response is not None:
                _note_odds_api_quota_issue(quota_events, code)
            if attempt == 0 and code == 429:
                await asyncio.sleep(1.2)
                continue
            logger.warning(
                "odds_upstream_http_error",
                extra={
                    "endpoint": "event_props",
                    "sport_key": sport_key,
                    "status_code": code,
                    "attempt": attempt + 1,
                },
            )
            return []
        except httpx.TimeoutException:
            if attempt == 0:
                await asyncio.sleep(0.6)
                continue
            logger.warning(
                "odds_upstream_timeout",
                extra={"endpoint": "event_props", "sport_key": sport_key, "attempt": attempt + 1},
            )
            return []
        except httpx.HTTPError as e:
            logger.warning(
                "odds_upstream_transport_error",
                extra={
                    "endpoint": "event_props",
                    "sport_key": sport_key,
                    "error_type": type(e).__name__,
                },
            )
            return []
        except ValueError:
            logger.warning(
                "odds_upstream_invalid_json",
                extra={"endpoint": "event_props", "sport_key": sport_key},
            )
            return []
    return []


async def _fetch_prop_sport(
    client: httpx.AsyncClient,
    api_key: str,
    sport_key: str,
    sport_title: str,
    markets_csv: str,
    allowed: set[str],
    commence_time_from: str | None = None,
    commence_time_to: str | None = None,
    quota_events: list[str] | None = None,
) -> tuple[list[BetPick], list[dict[str, Any]]]:
    events = await _fetch_events(
        client, api_key, sport_key, commence_time_from, commence_time_to, quota_events
    )
    # List every game in the API window in the UI; only fetch props for the first N not-started.
    shells = shells_from_scheduled_events(events, sport_key, sport_title)
    chosen = _events_upcoming_sorted_for_props(events, MAX_PROP_EVENTS_PER_SPORT)
    if not chosen:
        return [], shells
    sem = asyncio.Semaphore(_PROP_FETCH_CONCURRENCY)

    async def one(ev: dict[str, Any]) -> list[BetPick]:
        async with sem:
            return await _fetch_event_props(
                client,
                api_key,
                sport_key,
                sport_title,
                _normalize_event_id(ev.get("id")),
                markets_csv,
                allowed,
                quota_events,
            )

    results = await asyncio.gather(
        *[one(ev) for ev in chosen],
        return_exceptions=True,
    )
    out: list[BetPick] = []
    for res in results:
        if isinstance(res, list):
            out.extend(res)
    return out, shells
