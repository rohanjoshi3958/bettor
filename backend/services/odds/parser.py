"""Provider payload parsing: convert raw Odds API responses into BetPick domain objects.

Responsibilities:
- Validate and filter bookmaker/market data from upstream responses.
- Collect prices (h2h and props) across allowed books.
- Build BetPick rows with implied probability and line-shopping edge.
"""

from __future__ import annotations

from typing import Any

from services.odds.models import ALLOWED_BOOKMAKER_KEYS, BetPick
from services.odds.normalization import _normalize_event_id, implied_probability
from services.odds.sports import _MARKET_LABELS


# ---------------------------------------------------------------------------
# Price type helpers
# ---------------------------------------------------------------------------

def _float_point(p: Any) -> float | None:
    if p is None:
        return None
    try:
        return round(float(p), 4)
    except (TypeError, ValueError):
        return None


def _prop_outcome_key(market_key: str, out: dict[str, Any]) -> tuple[str, str, str, float | None]:
    return (
        market_key,
        (out.get("description") or "").strip(),
        (out.get("name") or "").strip(),
        _float_point(out.get("point")),
    )


def _format_prop_pick(mk: str, desc: str, name: str, point: float | None) -> str:
    label = _MARKET_LABELS.get(mk, mk.replace("_", " ").title())
    if point is not None:
        return f"{label} · {desc} — {name} {point}"
    return f"{label} · {desc} — {name}"


# ---------------------------------------------------------------------------
# Book filtering
# ---------------------------------------------------------------------------

def _book_allowed(book: dict[str, Any]) -> bool:
    k = book.get("key")
    return isinstance(k, str) and k in ALLOWED_BOOKMAKER_KEYS


# ---------------------------------------------------------------------------
# Price collection
# ---------------------------------------------------------------------------

def _collect_h2h_prices(event: dict[str, Any]) -> dict[str, list[tuple[str, float]]]:
    by_outcome: dict[str, list[tuple[str, float]]] = {}
    for book in event.get("bookmakers") or []:
        if not _book_allowed(book):
            continue
        title = book.get("title") or book.get("key") or "Unknown"
        for market in book.get("markets") or []:
            if market.get("key") != "h2h":
                continue
            for out in market.get("outcomes") or []:
                name = out.get("name")
                price = out.get("price")
                if not name or price is None:
                    continue
                try:
                    dec = float(price)
                except (TypeError, ValueError):
                    continue
                if dec <= 1.0:
                    continue
                by_outcome.setdefault(name, []).append((title, dec))
    return by_outcome


def _collect_prop_prices(
    event: dict[str, Any],
    allowed_markets: set[str],
) -> dict[tuple[str, str, str, float | None], list[tuple[str, float]]]:
    by_key: dict[tuple[str, str, str, float | None], list[tuple[str, float]]] = {}
    for book in event.get("bookmakers") or []:
        if not _book_allowed(book):
            continue
        title = book.get("title") or book.get("key") or "Unknown"
        for market in book.get("markets") or []:
            mk = market.get("key") or ""
            if mk not in allowed_markets:
                continue
            for out in market.get("outcomes") or []:
                price = out.get("price")
                if price is None:
                    continue
                try:
                    dec = float(price)
                except (TypeError, ValueError):
                    continue
                if dec <= 1.0:
                    continue
                key = _prop_outcome_key(mk, out)
                by_key.setdefault(key, []).append((title, dec))
    return by_key


# ---------------------------------------------------------------------------
# BetPick builders
# ---------------------------------------------------------------------------

def _h2h_events_to_picks(
    events: list[dict[str, Any]],
    sport_key: str,
    sport_title: str,
) -> list[BetPick]:
    picks: list[BetPick] = []
    for ev in events:
        raw_time = ev.get("commence_time") or ""
        prices = _collect_h2h_prices(ev)
        home = ev.get("home_team") or ""
        away = ev.get("away_team") or ""
        eid = _normalize_event_id(ev.get("id"))

        for outcome_name, book_prices in prices.items():
            if len(book_prices) < 2:
                continue
            decimals = [p[1] for p in book_prices]
            best_tup = max(book_prices, key=lambda x: x[1])
            best_book, best_dec = best_tup
            avg_dec = sum(decimals) / len(decimals)
            if avg_dec <= 0:
                continue
            # % edge vs mean of books posting this outcome (2+ books required).
            edge = (best_dec - avg_dec) / avg_dec * 100.0
            impl = implied_probability(best_dec)

            picks.append(
                BetPick(
                    sport_key=sport_key,
                    sport_title=sport_title,
                    event_id=eid,
                    home_team=home,
                    away_team=away,
                    commence_time=raw_time,
                    pick=outcome_name,
                    market_key="h2h",
                    implied_probability=round(impl, 4),
                    best_decimal_odds=round(best_dec, 3),
                    best_book=best_book,
                    avg_decimal_odds=round(avg_dec, 3),
                    edge_pct=round(edge, 2),
                )
            )
    return picks


def _prop_event_to_picks(
    ev: dict[str, Any],
    sport_key: str,
    sport_title: str,
    allowed: set[str],
) -> list[BetPick]:
    raw_time = ev.get("commence_time") or ""
    home = ev.get("home_team") or ""
    away = ev.get("away_team") or ""
    eid = _normalize_event_id(ev.get("id"))
    grouped = _collect_prop_prices(ev, allowed)
    picks: list[BetPick] = []

    for key, book_prices in grouped.items():
        if len(book_prices) < 2:
            continue
        mk, desc, oname, pt = key
        if not desc and not oname:
            continue
        decimals = [p[1] for p in book_prices]
        best_tup = max(book_prices, key=lambda x: x[1])
        best_book, best_dec = best_tup
        avg_dec = sum(decimals) / len(decimals)
        if avg_dec <= 0:
            continue
        # Same edge formula as h2h: best decimal vs mean of books on this prop outcome.
        edge = (best_dec - avg_dec) / avg_dec * 100.0
        impl = implied_probability(best_dec)
        pick_label = _format_prop_pick(mk, desc, oname, pt)
        picks.append(
            BetPick(
                sport_key=sport_key,
                sport_title=sport_title,
                event_id=eid,
                home_team=home,
                away_team=away,
                commence_time=raw_time,
                pick=pick_label,
                market_key=mk,
                implied_probability=round(impl, 4),
                best_decimal_odds=round(best_dec, 3),
                best_book=best_book,
                avg_decimal_odds=round(avg_dec, 3),
                edge_pct=round(edge, 2),
            )
        )
    return picks
