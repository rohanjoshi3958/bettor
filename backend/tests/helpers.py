"""Builders, fakes and utilities shared across the backend test suite.

The Odds API is mocked at the HTTP boundary (`httpx.MockTransport`) so service internals stay
untouched: tests exercise the same parsing, ranking and fallback code paths production runs.
"""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

from app.core import calendar as calendar_module
from services import picks_cache
from services.odds import service as odds_service
from services.odds import client as odds_client_module
from services.odds import normalization as normalization_module
from services.odds import demo as demo_module
from services.odds.service import BetPick

_SPORT_PATH = re.compile(r"^/v4/sports/(?P<sport>[^/]+)/(?P<tail>.*)$")
_EVENT_ODDS_TAIL = re.compile(r"^events/(?P<event_id>[^/]+)/odds$")


# --------------------------------------------------------------------------------------
# time helpers
# --------------------------------------------------------------------------------------


def utc_iso(dt: datetime) -> str:
    """Odds API style timestamp: UTC with a trailing `Z`."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def iso_offset(
    hours: float = 0.0,
    *,
    minutes: float = 0.0,
    base: datetime | None = None,
) -> str:
    origin = base or datetime.now(timezone.utc)
    return utc_iso(origin + timedelta(hours=hours, minutes=minutes))


def freeze_time(monkeypatch, now_utc: datetime, *extra_modules) -> datetime:
    """Pin `datetime.now()` inside the modules that make time-window decisions.

    After the service split, datetime.now() is called from normalization, demo, and the
    orchestration layer (service). All three are patched so that any code path that
    branches on the current time uses the frozen instant.
    """
    if now_utc.tzinfo is None:
        now_utc = now_utc.replace(tzinfo=timezone.utc)

    class FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz=None):  # type: ignore[override]
            if tz is None:
                return now_utc.astimezone(timezone.utc).replace(tzinfo=None)
            return now_utc.astimezone(tz)

    for module in (odds_service, normalization_module, demo_module, calendar_module, *extra_modules):
        monkeypatch.setattr(module, "datetime", FrozenDatetime)
    return now_utc


# --------------------------------------------------------------------------------------
# domain object builders
# --------------------------------------------------------------------------------------


def make_pick(**overrides: Any) -> BetPick:
    fields: dict[str, Any] = {
        "sport_key": "basketball_nba",
        "sport_title": "NBA",
        "event_id": "evt-1",
        "home_team": "Boston Celtics",
        "away_team": "Milwaukee Bucks",
        "commence_time": iso_offset(6),
        "pick": "PTS · Jayson Tatum — Over 27.5",
        "market_key": "player_points",
        "implied_probability": 0.60,
        "best_decimal_odds": 1.66,
        "best_book": "DraftKings",
        "avg_decimal_odds": 1.60,
        "edge_pct": 3.75,
    }
    fields.update(overrides)
    return BetPick(**fields)


def quote(
    book: str,
    market: str,
    name: str,
    price: Any,
    *,
    description: str | None = None,
    point: Any = None,
    book_title: str | None = None,
) -> dict[str, Any]:
    """One book's price for one outcome; `odds_event` folds these into the API's nested shape."""
    return {
        "book": book,
        "book_title": book_title,
        "market": market,
        "name": name,
        "price": price,
        "description": description,
        "point": point,
    }


def _bookmakers_from_quotes(quotes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    books: dict[str, dict[str, Any]] = {}
    for q in quotes:
        book = books.setdefault(
            q["book"],
            {
                "key": q["book"],
                "title": q["book_title"] or q["book"].title(),
                "markets": [],
            },
        )
        if q["book_title"] is not None:
            book["title"] = q["book_title"]
        market = next((m for m in book["markets"] if m["key"] == q["market"]), None)
        if market is None:
            market = {"key": q["market"], "outcomes": []}
            book["markets"].append(market)
        outcome: dict[str, Any] = {"name": q["name"], "price": q["price"]}
        if q["description"] is not None:
            outcome["description"] = q["description"]
        if q["point"] is not None:
            outcome["point"] = q["point"]
        market["outcomes"].append(outcome)
    return list(books.values())


def odds_event(
    event_id: Any,
    *,
    home: str = "Boston Celtics",
    away: str = "Milwaukee Bucks",
    commence_time: str | None = None,
    quotes: list[dict[str, Any]] | None = None,
    sport_key: str = "basketball_nba",
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "id": event_id,
        "sport_key": sport_key,
        "home_team": home,
        "away_team": away,
        "commence_time": commence_time if commence_time is not None else iso_offset(6),
    }
    if quotes is not None:
        payload["bookmakers"] = _bookmakers_from_quotes(quotes)
    return payload


def h2h_quotes(outcome_prices: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    """`{"draftkings": {"Arsenal": 1.85}}` -> h2h quotes."""
    return [
        quote(book, "h2h", name, price)
        for book, prices in outcome_prices.items()
        for name, price in prices.items()
    ]


# --------------------------------------------------------------------------------------
# Odds API fake
# --------------------------------------------------------------------------------------


@dataclass
class Fail:
    """Upstream answered with an HTTP error status."""

    status_code: int
    body: Any = None


@dataclass
class Raise:
    """Upstream connection blew up before a response (timeout, connect error, ...)."""

    exc: BaseException


@dataclass
class Seq:
    """Different response per call to the same URL — used for retry-behavior tests."""

    items: list[Any]
    _calls: int = field(default=0, init=False)

    def take(self) -> Any:
        if not self.items:
            return None
        index = min(self._calls, len(self.items) - 1)
        self._calls += 1
        return self.items[index]


class FakeOddsAPI:
    """In-process stand-in for api.the-odds-api.com.

    Configure per-endpoint payloads, then assert on `requests` to check fan-out and query params.
    """

    def __init__(self) -> None:
        self.bulk_odds: dict[str, Any] = {}
        self.events: dict[str, Any] = {}
        self.event_odds: dict[tuple[str, str], Any] = {}
        self.default_bulk_odds: Any = []
        self.default_events: Any = []
        self.default_event_odds: Any = None
        self.everything: Any = None
        self.requests: list[httpx.Request] = []

    # -- configuration ------------------------------------------------------------------

    def set_bulk_odds(self, sport_key: str, spec: Any) -> None:
        self.bulk_odds[sport_key] = spec

    def set_events(self, sport_key: str, spec: Any) -> None:
        self.events[sport_key] = spec

    def set_event_odds(self, sport_key: str, event_id: Any, spec: Any) -> None:
        self.event_odds[(sport_key, str(event_id))] = spec

    def fail_everything(self, spec: Any) -> None:
        self.everything = spec

    # -- assertions ---------------------------------------------------------------------

    @property
    def paths(self) -> list[str]:
        return [str(r.url.path) for r in self.requests]

    def count(self, path_fragment: str) -> int:
        return sum(1 for p in self.paths if path_fragment in p)

    def params_for(self, path_fragment: str) -> dict[str, str]:
        for request in self.requests:
            if path_fragment in str(request.url.path):
                return dict(request.url.params)
        raise AssertionError(f"no upstream request matched {path_fragment!r}; saw {self.paths}")

    def api_keys_used(self) -> set[str]:
        return {r.url.params.get("apiKey", "") for r in self.requests}

    # -- transport ----------------------------------------------------------------------

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.everything is not None:
            return self._respond(self.everything)

        match = _SPORT_PATH.match(str(request.url.path))
        if match is None:
            return httpx.Response(404, json={"message": "unknown route"})
        sport, tail = match["sport"], match["tail"]

        if tail == "odds":
            return self._respond(self.bulk_odds.get(sport, self.default_bulk_odds))
        if tail == "events":
            return self._respond(self.events.get(sport, self.default_events))

        event_match = _EVENT_ODDS_TAIL.match(tail)
        if event_match is None:
            return httpx.Response(404, json={"message": "unknown route"})
        event_id = event_match["event_id"]
        spec = self.event_odds.get((sport, event_id), self.default_event_odds)
        if spec is None:
            spec = {"id": event_id, "sport_key": sport, "bookmakers": []}
        return self._respond(spec)

    def _respond(self, spec: Any) -> httpx.Response:
        if isinstance(spec, Seq):
            spec = spec.take()
        if isinstance(spec, Raise):
            raise spec.exc
        if isinstance(spec, BaseException):
            raise spec
        if isinstance(spec, Fail):
            body = spec.body if spec.body is not None else {"message": f"HTTP {spec.status_code}"}
            return httpx.Response(spec.status_code, json=body)
        if isinstance(spec, httpx.Response):
            return spec
        # Serialize explicitly so a `None` payload becomes the JSON body `null` rather than
        # httpx's "no json supplied" empty body.
        return httpx.Response(
            200,
            content=json.dumps(spec),
            headers={
                "content-type": "application/json",
                # Default quota headers so instrumentation tests can observe gauges.
                "x-requests-remaining": "5000",
                "x-requests-used": "100",
            },
        )


class _HttpxProxy:
    """Stands in for the `httpx` module inside the odds service, swapping only `AsyncClient`."""

    def __init__(self, async_client_factory) -> None:
        self.AsyncClient = async_client_factory

    def __getattr__(self, name: str) -> Any:
        return getattr(httpx, name)


def install_fake_odds_api(monkeypatch, api: FakeOddsAPI) -> FakeOddsAPI:
    def factory(*args: Any, **kwargs: Any) -> httpx.AsyncClient:
        kwargs.pop("transport", None)
        return httpx.AsyncClient(*args, transport=httpx.MockTransport(api.handler), **kwargs)

    proxy = _HttpxProxy(factory)
    # Patch both the orchestration layer (creates AsyncClient) and the client module
    # (uses httpx exception classes in except clauses).
    monkeypatch.setattr(odds_service, "httpx", proxy)
    monkeypatch.setattr(odds_client_module, "httpx", proxy)
    return api


class _AsyncioProxy:
    """`asyncio` for the odds service with a non-blocking `sleep` so retry paths run instantly."""

    def __init__(self, sleep) -> None:
        self.sleep = sleep

    def __getattr__(self, name: str) -> Any:
        return getattr(asyncio, name)


def install_instant_sleep(monkeypatch) -> list[float]:
    recorded: list[float] = []
    real_sleep = asyncio.sleep

    async def sleep(delay: float, *args: Any, **kwargs: Any) -> Any:
        recorded.append(delay)
        return await real_sleep(0)

    # asyncio.sleep is called in the client module (prop retry backoff).
    monkeypatch.setattr(odds_client_module, "asyncio", _AsyncioProxy(sleep))
    return recorded


# --------------------------------------------------------------------------------------
# cache state
# --------------------------------------------------------------------------------------


def reset_picks_cache() -> None:
    """Drop cached entries and rebind the module locks to the running test's event loop."""
    picks_cache._cache.clear()
    picks_cache._key_locks.clear()
    picks_cache._meta_lock = asyncio.Lock()


class FakeMonotonic:
    """Manual clock for `picks_cache` TTL assertions (no real sleeping)."""

    def __init__(self, start: float = 1_000.0) -> None:
        self.value = start

    def monotonic(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds
