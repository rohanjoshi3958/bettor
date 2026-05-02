"""
Fetches odds from The Odds API (https://the-odds-api.com/).

- Soccer (EU leagues): bulk /odds, moneyline (h2h) line-shopping edge.
- NBA / NFL props: per-event /events/{id}/odds (player markets).

Slate rules are the same for every league: games on the requested calendar day are listed in
kickoff order, but any game whose scheduled kickoff is already past (started or finished) is
omitted. Prop odds are only fetched for up to MAX_PROP_EVENTS_PER_SPORT not-started games per
prop sport to limit API usage.
"""

from __future__ import annotations

import asyncio
import os
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

import httpx

ODDS_BASE = "https://api.the-odds-api.com/v4"

# Only these books (The Odds API keys).
ALLOWED_BOOKMAKER_KEYS: frozenset[str] = frozenset(
    {"draftkings", "fanduel", "fanatics"}
)
_BOOKMAKERS_PARAM = "draftkings,fanduel,fanatics"
_ODDS_REGIONS = "us"

# Prop coverage cap (each event = extra API calls). Override via env to save quota.
MAX_PROP_EVENTS_PER_SPORT = int(os.environ.get("MAX_PROP_EVENTS_PER_SPORT", "20"))
# Concurrent /events/{id}/odds calls — keep low to avoid 429 rate limits on The Odds API.
_PROP_FETCH_CONCURRENCY = int(os.environ.get("ODDS_PROP_CONCURRENCY", "4"))

# Market-implied probability = 1 / decimal_odds (naive; not de-vigged).
# 55% was often too strict with 4 books + 2-book minimum → empty slates; 0.52 primary, 0.50 fallback.
MIN_IMPLIED_PROBABILITY = 0.52
RELAXED_IMPLIED_PROBABILITY = 0.50

# Rank = blend of high implied prob + line-shopping edge (best vs avg among your books).
_RANK_IMPLIED_WEIGHT = 0.55
_RANK_EDGE_WEIGHT = 0.45

# Top picks to keep per game (grouped by event).
PICKS_PER_GAME = 3


def _normalize_event_id(raw: Any) -> str:
    """Odds API event ids may be int, float, or str — must match for schedule ↔ picks joins."""
    if raw is None:
        return ""
    if isinstance(raw, bool):
        return str(raw).lower()
    if isinstance(raw, int):
        return str(raw)
    if isinstance(raw, float):
        if raw.is_integer():
            return str(int(raw))
        return str(raw)
    s = str(raw).strip()
    if not s:
        return ""
    if s.isdigit():
        return s
    if len(s) > 2 and s[-2:] == ".0" and s[:-2].isdigit():
        return s[:-2]
    try:
        f = float(s)
        if f.is_integer():
            return str(int(f))
    except ValueError:
        pass
    return s


def implied_probability(decimal_odds: float) -> float:
    """Naive implied prob from the best decimal price: 1/odds (no de-vig across books)."""
    if decimal_odds <= 1.0:
        return 0.0
    return min(1.0, 1.0 / decimal_odds)


def rank_score(p: "BetPick") -> float:
    """Blend: implied on 0–100 scale (from best price) + line edge % (best vs mean of your books)."""
    return _RANK_IMPLIED_WEIGHT * (p.implied_probability * 100.0) + _RANK_EDGE_WEIGHT * p.edge_pct


def _zone(tz_name: str) -> ZoneInfo:
    try:
        return ZoneInfo(tz_name)
    except Exception:
        return ZoneInfo("UTC")


def local_day_bounds_utc(day: date, tz_name: str) -> tuple[datetime, datetime]:
    """Return [start_utc, end_utc) for that calendar day in the given IANA timezone."""
    z = _zone(tz_name)
    start_local = datetime.combine(day, time.min, tzinfo=z)
    end_local = start_local + timedelta(days=1)
    return start_local.astimezone(timezone.utc), end_local.astimezone(timezone.utc)


def _parse_commence(raw: str) -> datetime | None:
    try:
        s = raw[:-1] + "+00:00" if raw.endswith("Z") else raw
        return datetime.fromisoformat(s)
    except (ValueError, TypeError):
        return None


def _commence_time_utc(raw: str) -> datetime | None:
    ct = _parse_commence(raw)
    if ct is None:
        return None
    if ct.tzinfo is None:
        return ct.replace(tzinfo=timezone.utc)
    return ct.astimezone(timezone.utc)


def _utc_iso_z(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _event_dict_to_shell(ev: dict[str, Any], sport_key: str, sport_title: str) -> dict[str, Any]:
    home = ev.get("home_team") or ""
    away = ev.get("away_team") or ""
    return {
        "sport_key": sport_key,
        "sport_title": sport_title,
        "event_id": _normalize_event_id(ev.get("id")),
        "home_team": home,
        "away_team": away,
        "commence_time": ev.get("commence_time") or "",
        "matchup": f"{away} @ {home}",
        "picks": [],
    }


def shells_from_scheduled_events(
    events: list[dict[str, Any]],
    sport_key: str,
    sport_title: str,
) -> list[dict[str, Any]]:
    """One shell per event in the API payload, same kickoff sort for soccer (bulk) and props (/events)."""
    return [
        _event_dict_to_shell(ev, sport_key, sport_title)
        for ev in _events_sorted_by_kickoff(events)
    ]


def _filter_shells_by_game_day(
    shells: list[dict[str, Any]], for_day: date, tz_name: str
) -> list[dict[str, Any]]:
    """Keep shells on `for_day` in `tz_name` (past kickoffs are removed after merge)."""
    start_utc, end_utc = local_day_bounds_utc(for_day, tz_name)
    out: list[dict[str, Any]] = []
    for s in shells:
        ct = _commence_time_utc(s.get("commence_time") or "")
        if ct is None:
            continue
        if not (start_utc <= ct < end_utc):
            continue
        out.append(s)
    return out


def _shells_from_bet_picks(picks: list[BetPick]) -> list[dict[str, Any]]:
    """Unique game shells derived from pick rows (demo / fallback)."""
    by_key: dict[tuple[str, str], dict[str, Any]] = {}
    for p in picks:
        key = (p.sport_key, p.event_id)
        if key not in by_key:
            by_key[key] = {
                "sport_key": p.sport_key,
                "sport_title": p.sport_title,
                "event_id": p.event_id,
                "home_team": p.home_team,
                "away_team": p.away_team,
                "commence_time": p.commence_time,
                "matchup": f"{p.away_team} @ {p.home_team}",
                "picks": [],
            }
    return list(by_key.values())


def _merge_game_key(sport_key: Any, event_id: Any) -> tuple[str, str]:
    return (str(sport_key or "").strip(), _normalize_event_id(event_id))


def _merge_scheduled_with_picks(
    shells: list[dict[str, Any]],
    ranked_games: list[dict[str, Any]],
    *,
    max_games: int | None = None,
) -> list[dict[str, Any]]:
    """Every scheduled shell is listed; picks come from ranked_games when present (keys normalized)."""
    picks_by_game: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for g in ranked_games:
        k = _merge_game_key(g.get("sport_key"), g.get("event_id"))
        picks_by_game[k] = list(g.get("picks") or [])

    shell_by_key: dict[tuple[str, str], dict[str, Any]] = {}
    for s in shells:
        k = _merge_game_key(s.get("sport_key"), s.get("event_id"))
        if k not in shell_by_key:
            shell_by_key[k] = s

    seen: set[tuple[str, str]] = set()
    out: list[dict[str, Any]] = []
    for k, s in shell_by_key.items():
        merged = {**s, "picks": picks_by_game.get(k, [])}
        out.append(merged)
        seen.add(k)

    for g in ranked_games:
        k = _merge_game_key(g.get("sport_key"), g.get("event_id"))
        if k not in seen:
            seen.add(k)
            out.append(g)

    def sk(g: dict[str, Any]) -> tuple[datetime, str]:
        ct = _commence_time_utc(g.get("commence_time") or "")
        eid = _normalize_event_id(g.get("event_id"))
        if ct is None:
            return (datetime.max.replace(tzinfo=timezone.utc), eid)
        return (ct, eid)

    out.sort(key=sk)
    if max_games is not None and max_games >= 0:
        out = out[:max_games]
    return out


def _exclude_past_kickoff_games(games: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Drop games whose scheduled commence time is at or before now (started or finished)."""
    now_utc = datetime.now(timezone.utc)
    out: list[dict[str, Any]] = []
    for g in games:
        ct = _commence_time_utc(g.get("commence_time") or "")
        if ct is None:
            out.append(g)
            continue
        if ct > now_utc:
            out.append(g)
    return out


def _kickoff_still_upcoming(game: dict[str, Any]) -> bool:
    ct = _commence_time_utc(game.get("commence_time") or "")
    if ct is None:
        return True
    return ct > datetime.now(timezone.utc)


def filter_picks_by_game_day(picks: list[BetPick], for_day: date, tz_name: str) -> list[BetPick]:
    """Keep only picks whose kickoff falls on `for_day` in `tz_name`. If that day is today, drop started games."""
    start_utc, end_utc = local_day_bounds_utc(for_day, tz_name)
    z = _zone(tz_name)
    today_user = datetime.now(z).date()
    now_utc = datetime.now(timezone.utc)
    out: list[BetPick] = []
    for p in picks:
        ct = _commence_time_utc(p.commence_time)
        if ct is None:
            continue
        if not (start_utc <= ct < end_utc):
            continue
        if for_day == today_user and ct <= now_utc:
            continue
        out.append(p)
    return out


def _book_allowed(book: dict[str, Any]) -> bool:
    k = book.get("key")
    return isinstance(k, str) and k in ALLOWED_BOOKMAKER_KEYS

SOCCER_SPORT_KEYS = (
    "soccer_epl",
    "soccer_spain_la_liga",
    "soccer_italy_serie_a",
    "soccer_germany_bundesliga",
    "soccer_france_ligue",
    "soccer_uefa_champs_league",
    "soccer_uefa_europa_league",
)

NBA_PROP_MARKETS = (
    "player_points,player_rebounds,player_assists,player_threes,"
    "player_points_rebounds_assists,player_points_rebounds,player_points_assists,player_rebounds_assists"
)

NFL_PROP_MARKETS = (
    "player_pass_yds,player_pass_tds,player_rush_yds,player_reception_yds,"
    "player_receptions,player_anytime_td"
)

def _nba_market_set() -> set[str]:
    return {m.strip() for m in NBA_PROP_MARKETS.replace("\n", "").split(",") if m.strip()}


def _nfl_market_set() -> set[str]:
    return {m.strip() for m in NFL_PROP_MARKETS.replace("\n", "").split(",") if m.strip()}


def _sport_titles() -> dict[str, str]:
    return {
        "basketball_nba": "NBA",
        "americanfootball_nfl": "NFL",
        "soccer_epl": "EPL",
        "soccer_spain_la_liga": "La Liga",
        "soccer_italy_serie_a": "Serie A",
        "soccer_germany_bundesliga": "Bundesliga",
        "soccer_france_ligue": "Ligue 1",
        "soccer_uefa_champs_league": "UCL",
        "soccer_uefa_europa_league": "Europa League",
    }


_MARKET_LABELS: dict[str, str] = {
    "player_points": "PTS",
    "player_rebounds": "REB",
    "player_assists": "AST",
    "player_threes": "3PM",
    "player_points_rebounds_assists": "PRA",
    "player_points_rebounds": "P+R",
    "player_points_assists": "P+A",
    "player_rebounds_assists": "R+A",
    "player_pass_yds": "Pass Yds",
    "player_pass_tds": "Pass TD",
    "player_rush_yds": "Rush Yds",
    "player_reception_yds": "Rec Yds",
    "player_receptions": "Rec",
    "player_anytime_td": "Anytime TD",
}


@dataclass(frozen=True)
class BetPick:
    sport_key: str
    sport_title: str
    event_id: str
    home_team: str
    away_team: str
    commence_time: str
    pick: str
    market_key: str | None
    implied_probability: float
    best_decimal_odds: float
    best_book: str
    avg_decimal_odds: float
    edge_pct: float


def _demo_commence_on_day(for_day: date, tz_name: str, hour: int, minute: int = 0) -> str:
    z = _zone(tz_name)
    dt = datetime.combine(for_day, time(hour, minute), tzinfo=z)
    utc = dt.astimezone(timezone.utc)
    now_utc = datetime.now(timezone.utc)
    if utc <= now_utc:
        _, end_utc = local_day_bounds_utc(for_day, tz_name)
        bump = now_utc + timedelta(hours=2, minutes=5)
        utc = min(bump, end_utc - timedelta(minutes=1))
        if utc <= now_utc:
            utc = end_utc - timedelta(minutes=1)
    return utc.isoformat().replace("+00:00", "Z")


def demo_picks(for_day: date, tz_name: str) -> list[BetPick]:
    titles = _sport_titles()
    nba_t = _demo_commence_on_day(for_day, tz_name, 19, 30)
    return [
        BetPick(
            sport_key="basketball_nba",
            sport_title=titles["basketball_nba"],
            event_id="demo-nba",
            home_team="Boston Celtics",
            away_team="Milwaukee Bucks",
            commence_time=nba_t,
            pick="PTS · Jayson Tatum — Over 27.5",
            market_key="player_points",
            implied_probability=round(implied_probability(1.62), 4),
            best_decimal_odds=1.62,
            best_book="DraftKings",
            avg_decimal_odds=1.58,
            edge_pct=2.5,
        ),
        BetPick(
            sport_key="basketball_nba",
            sport_title=titles["basketball_nba"],
            event_id="demo-nba",
            home_team="Boston Celtics",
            away_team="Milwaukee Bucks",
            commence_time=nba_t,
            pick="REB · Jayson Tatum — Over 8.5",
            market_key="player_rebounds",
            implied_probability=round(implied_probability(1.59), 4),
            best_decimal_odds=1.59,
            best_book="FanDuel",
            avg_decimal_odds=1.55,
            edge_pct=2.7,
        ),
        BetPick(
            sport_key="basketball_nba",
            sport_title=titles["basketball_nba"],
            event_id="demo-nba",
            home_team="Boston Celtics",
            away_team="Milwaukee Bucks",
            commence_time=nba_t,
            pick="PRA · Jayson Tatum — Over 38.5",
            market_key="player_points_rebounds_assists",
            implied_probability=round(implied_probability(1.61), 4),
            best_decimal_odds=1.61,
            best_book="Fanatics",
            avg_decimal_odds=1.56,
            edge_pct=3.0,
        ),
        BetPick(
            sport_key="basketball_nba",
            sport_title=titles["basketball_nba"],
            event_id="demo-nba",
            home_team="Boston Celtics",
            away_team="Milwaukee Bucks",
            commence_time=nba_t,
            pick="3PM · Derrick White — Over 3.5",
            market_key="player_threes",
            implied_probability=round(implied_probability(1.57), 4),
            best_decimal_odds=1.57,
            best_book="FanDuel",
            avg_decimal_odds=1.52,
            edge_pct=2.4,
        ),
        BetPick(
            sport_key="basketball_nba",
            sport_title=titles["basketball_nba"],
            event_id="demo-nba",
            home_team="Boston Celtics",
            away_team="Milwaukee Bucks",
            commence_time=nba_t,
            pick="AST · Jrue Holiday — Over 5.5",
            market_key="player_assists",
            implied_probability=round(implied_probability(1.6), 4),
            best_decimal_odds=1.6,
            best_book="DraftKings",
            avg_decimal_odds=1.57,
            edge_pct=1.9,
        ),
        BetPick(
            sport_key="americanfootball_nfl",
            sport_title=titles["americanfootball_nfl"],
            event_id="demo-nfl",
            home_team="Kansas City Chiefs",
            away_team="Buffalo Bills",
            commence_time=_demo_commence_on_day(for_day, tz_name, 16, 0),
            pick="Pass Yds · Josh Allen — Over 274.5",
            market_key="player_pass_yds",
            implied_probability=round(implied_probability(1.58), 4),
            best_decimal_odds=1.58,
            best_book="FanDuel",
            avg_decimal_odds=1.54,
            edge_pct=2.6,
        ),
        BetPick(
            sport_key="soccer_epl",
            sport_title=titles["soccer_epl"],
            event_id="demo-epl",
            home_team="Arsenal",
            away_team="Chelsea",
            commence_time=_demo_commence_on_day(for_day, tz_name, 12, 0),
            pick="Arsenal",
            market_key="h2h",
            implied_probability=round(implied_probability(1.55), 4),
            best_decimal_odds=1.55,
            best_book="Fanatics",
            avg_decimal_odds=1.51,
            edge_pct=2.6,
        ),
    ]


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


def _note_odds_api_quota_issue(quota_events: list[str] | None, status_code: int) -> None:
    if quota_events is None:
        return
    if status_code == 429:
        quota_events.append("rate_limit")
    elif status_code == 402:
        quota_events.append("payment_required")


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
        try:
            from services.odds_peaks import defer_record_h2h_slate_events

            defer_record_h2h_slate_events(ordered, sport_key)
        except Exception:
            pass
        shells = shells_from_scheduled_events(events, sport_key, sport_title)
        return picks, shells
    except httpx.HTTPStatusError as e:
        if e.response is not None:
            _note_odds_api_quota_issue(quota_events, e.response.status_code)
        return [], []
    except httpx.HTTPError:
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
        if e.response is not None:
            _note_odds_api_quota_issue(quota_events, e.response.status_code)
        return []
    except httpx.HTTPError:
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
            try:
                from services.odds_peaks import defer_record_prop_event

                defer_record_prop_event(ev, sport_key, allowed)
            except Exception:
                pass
            return _prop_event_to_picks(ev, sport_key, sport_title, allowed)
        except httpx.HTTPStatusError as e:
            code = e.response.status_code if e.response is not None else 0
            if e.response is not None:
                _note_odds_api_quota_issue(quota_events, code)
            if attempt == 0 and code == 429:
                await asyncio.sleep(1.2)
                continue
            return []
        except httpx.TimeoutException:
            if attempt == 0:
                await asyncio.sleep(0.6)
                continue
            return []
        except httpx.HTTPError:
            return []
    return []


def _event_commence_utc(ev: dict[str, Any]) -> datetime | None:
    raw = ev.get("commence_time") or ""
    return _commence_time_utc(raw)


def _events_sorted_by_kickoff(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """All /events rows, sorted by kickoff (unknown times last), stable by id."""
    keyed: list[tuple[datetime, str, dict[str, Any]]] = []
    for ev in events:
        ct = _event_commence_utc(ev)
        eid = str(ev.get("id") or "")
        sort_t = ct if ct is not None else datetime.max.replace(tzinfo=timezone.utc)
        keyed.append((sort_t, eid, ev))
    keyed.sort(key=lambda x: (x[0], x[1]))
    return [t[2] for t in keyed]


def _events_upcoming_sorted_for_props(
    events: list[dict[str, Any]],
    cap: int,
) -> list[dict[str, Any]]:
    """Not-started events only, sorted by kickoff; cap limits Odds API /events/{{id}}/odds volume."""
    now = datetime.now(timezone.utc)
    out: list[dict[str, Any]] = []
    for ev in _events_sorted_by_kickoff(events):
        ct = _event_commence_utc(ev)
        if ct is None or ct > now:
            out.append(ev)
    return out[:cap] if cap > 0 else out


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


def _group_with_implied_fallback(
    raw_picks: list[BetPick],
    picks_per_game: int,
    max_games: int | None,
) -> tuple[list[dict[str, Any]], float, bool]:
    """Try primary implied floor, then relaxed; return (games, min_implied_used, used_fallback)."""
    blocks = group_picks_into_games(
        raw_picks,
        picks_per_game=picks_per_game,
        max_games=max_games,
        min_implied=MIN_IMPLIED_PROBABILITY,
    )
    if blocks:
        return blocks, MIN_IMPLIED_PROBABILITY, False
    blocks = group_picks_into_games(
        raw_picks,
        picks_per_game=picks_per_game,
        max_games=max_games,
        min_implied=RELAXED_IMPLIED_PROBABILITY,
    )
    return blocks, RELAXED_IMPLIED_PROBABILITY, True


def supported_sport_key(sport_key: str) -> bool:
    return sport_key in SOCCER_SPORT_KEYS or sport_key in (
        "basketball_nba",
        "americanfootball_nfl",
    )


async def fetch_picks_for_event(
    api_key: str | None,
    *,
    sport_key: str,
    event_id: str,
    for_day: date,
    timezone_name: str,
    picks_per_game: int = PICKS_PER_GAME,
) -> tuple[dict[str, Any] | None, str, float, bool, str | None]:
    """Fetch and rank picks for a single event (one Odds API call for props; bulk slice for soccer)."""
    z = _zone(timezone_name)
    today_user = datetime.now(z).date()
    if for_day < today_user:
        src = "demo" if not (api_key and api_key.strip()) else "live"
        return None, src, MIN_IMPLIED_PROBABILITY, False, None

    if not supported_sport_key(sport_key):
        return None, "live", MIN_IMPLIED_PROBABILITY, False, None

    eid_norm = _normalize_event_id(event_id)

    if not api_key or not api_key.strip():
        demo = demo_picks(for_day, timezone_name)
        picks = [p for p in demo if p.sport_key == sport_key and p.event_id == eid_norm]
        picks = filter_picks_by_game_day(picks, for_day, timezone_name)
        games, mi_used, relaxed = _group_with_implied_fallback(
            picks, picks_per_game, max_games=1
        )
        if games:
            g0 = games[0]
            if _kickoff_still_upcoming(g0):
                return g0, "demo", mi_used, relaxed, None
        return None, "demo", mi_used, relaxed, None

    key = api_key.strip()
    titles = _sport_titles()
    sport_title = titles.get(sport_key, sport_key)
    start_utc, end_utc = local_day_bounds_utc(for_day, timezone_name)
    cf = _utc_iso_z(start_utc)
    ct = _utc_iso_z(end_utc - timedelta(seconds=1))

    quota_events: list[str] = []
    async with httpx.AsyncClient() as client:
        if sport_key in SOCCER_SPORT_KEYS:
            bulk_picks, _ = await _fetch_bulk_h2h(
                client, key, sport_key, sport_title, cf, ct, quota_events
            )
            picks = [p for p in bulk_picks if p.event_id == eid_norm]
        elif sport_key == "basketball_nba":
            picks = await _fetch_event_props(
                client,
                key,
                sport_key,
                titles["basketball_nba"],
                eid_norm,
                NBA_PROP_MARKETS.replace("\n", ""),
                _nba_market_set(),
                quota_events,
            )
        elif sport_key == "americanfootball_nfl":
            picks = await _fetch_event_props(
                client,
                key,
                sport_key,
                titles["americanfootball_nfl"],
                eid_norm,
                NFL_PROP_MARKETS.replace("\n", ""),
                _nfl_market_set(),
                quota_events,
            )
        else:
            picks = []

    picks = filter_picks_by_game_day(picks, for_day, timezone_name)
    games, mi_used, relaxed = _group_with_implied_fallback(
        picks, picks_per_game, max_games=1
    )
    has_picks = any(len(g.get("picks") or []) > 0 for g in games)
    warn = _odds_api_warning_message(quota_events, has_usable_response=has_picks)
    for g in games:
        if _normalize_event_id(g.get("event_id")) == eid_norm:
            if _kickoff_still_upcoming(g):
                return g, "live", mi_used, relaxed, warn
            return None, "live", mi_used, relaxed, warn
    if games:
        g0 = games[0]
        if _kickoff_still_upcoming(g0):
            return g0, "live", mi_used, relaxed, warn
    return None, "live", mi_used, relaxed, warn


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


async def fetch_best_picks(
    api_key: str | None,
    *,
    picks_per_game: int = PICKS_PER_GAME,
    max_games: int | None = None,
    for_day: date,
    timezone_name: str,
) -> tuple[list[dict[str, Any]], str, float, bool, str | None]:
    z = _zone(timezone_name)
    today_user = datetime.now(z).date()
    if for_day < today_user:
        src = "demo" if not (api_key and api_key.strip()) else "live"
        return [], src, MIN_IMPLIED_PROBABILITY, False, None

    if not api_key or not api_key.strip():
        demo = demo_picks(for_day, timezone_name)
        demo = filter_picks_by_game_day(demo, for_day, timezone_name)
        games, mi_used, relaxed = _group_with_implied_fallback(
            demo, picks_per_game, None
        )
        shells = _shells_from_bet_picks(demo)
        merged = _merge_scheduled_with_picks(shells, games, max_games=max_games)
        merged = _exclude_past_kickoff_games(merged)
        return merged, "demo", mi_used, relaxed, None

    key = api_key.strip()
    titles = _sport_titles()
    start_utc, end_utc = local_day_bounds_utc(for_day, timezone_name)
    cf = _utc_iso_z(start_utc)
    ct = _utc_iso_z(end_utc - timedelta(seconds=1))

    quota_events: list[str] = []
    all_picks_pre: list[BetPick] = []
    all_shells: list[dict[str, Any]] = []
    async with httpx.AsyncClient() as client:
        soccer_tasks = [
            _fetch_bulk_h2h(client, key, sk, titles.get(sk, sk), cf, ct, quota_events)
            for sk in SOCCER_SPORT_KEYS
        ]
        nba_t = _fetch_prop_sport(
            client,
            key,
            "basketball_nba",
            titles["basketball_nba"],
            NBA_PROP_MARKETS.replace("\n", ""),
            _nba_market_set(),
            cf,
            ct,
            quota_events,
        )
        nfl_t = _fetch_prop_sport(
            client,
            key,
            "americanfootball_nfl",
            titles["americanfootball_nfl"],
            NFL_PROP_MARKETS.replace("\n", ""),
            _nfl_market_set(),
            cf,
            ct,
            quota_events,
        )

        gathered = await asyncio.gather(
            *soccer_tasks,
            nba_t,
            nfl_t,
            return_exceptions=True,
        )

        batch: list[BetPick] = []
        shell_batch: list[dict[str, Any]] = []
        for res in gathered:
            if isinstance(res, Exception):
                continue
            picks_part, shells_part = res
            batch.extend(picks_part)
            shell_batch.extend(shells_part)

        all_picks_pre = batch
        all_shells = shell_batch

    seen_keys: dict[tuple[str, str], dict[str, Any]] = {}
    for s in all_shells:
        key = _merge_game_key(s.get("sport_key"), s.get("event_id"))
        if key not in seen_keys:
            seen_keys[key] = s
    shells_unique = list(seen_keys.values())
    shells_filtered = _filter_shells_by_game_day(shells_unique, for_day, timezone_name)

    all_picks = filter_picks_by_game_day(all_picks_pre, for_day, timezone_name)
    games, mi_used, relaxed = _group_with_implied_fallback(
        all_picks, picks_per_game, None
    )
    merged = _merge_scheduled_with_picks(
        shells_filtered, games, max_games=max_games
    )
    merged = _exclude_past_kickoff_games(merged)
    warn = _odds_api_warning_message(
        quota_events,
        has_usable_response=len(merged) > 0,
    )
    return merged, "live", mi_used, relaxed, warn


def picks_to_json(picks: list[BetPick]) -> list[dict[str, Any]]:
    return [
        {
            "sport_key": p.sport_key,
            "sport_title": p.sport_title,
            "event_id": p.event_id,
            "matchup": f"{p.away_team} @ {p.home_team}",
            "home_team": p.home_team,
            "away_team": p.away_team,
            "commence_time": p.commence_time,
            "pick": p.pick,
            "market_key": p.market_key,
            "implied_probability": p.implied_probability,
            "implied_pct": round(p.implied_probability * 100.0, 2),
            "rank_score": round(rank_score(p), 2),
            "best_decimal_odds": p.best_decimal_odds,
            "best_book": p.best_book,
            "avg_decimal_odds": p.avg_decimal_odds,
            "edge_pct": p.edge_pct,
        }
        for p in picks
    ]


def group_picks_into_games(
    picks: list[BetPick],
    picks_per_game: int = PICKS_PER_GAME,
    max_games: int | None = None,
    *,
    min_implied: float | None = None,
) -> list[dict[str, Any]]:
    """Group by (sport_key, event_id); keep top `picks_per_game` by rank_score per game."""
    floor = MIN_IMPLIED_PROBABILITY if min_implied is None else min_implied
    filtered = [p for p in picks if p.implied_probability >= floor]
    by_game: dict[tuple[str, str], list[BetPick]] = defaultdict(list)
    for p in filtered:
        k = _merge_game_key(p.sport_key, p.event_id)
        by_game[k].append(p)
    blocks: list[dict[str, Any]] = []
    for plist in by_game.values():
        plist.sort(key=rank_score, reverse=True)
        top = plist[:picks_per_game]
        if not top:
            continue
        g0 = top[0]
        eid_out = _normalize_event_id(g0.event_id)
        blocks.append(
            {
                "sport_key": g0.sport_key,
                "sport_title": g0.sport_title,
                "event_id": eid_out,
                "home_team": g0.home_team,
                "away_team": g0.away_team,
                "commence_time": g0.commence_time,
                "matchup": f"{g0.away_team} @ {g0.home_team}",
                "picks": picks_to_json(top),
            }
        )

    def sort_key(b: dict[str, Any]) -> tuple[datetime, str]:
        ct = _commence_time_utc(b["commence_time"])
        eid = _normalize_event_id(b.get("event_id"))
        if ct is None:
            return (datetime.max.replace(tzinfo=timezone.utc), eid)
        return (ct, eid)

    blocks.sort(key=sort_key)
    if max_games is not None:
        blocks = blocks[:max_games]
    return blocks


def get_api_key() -> str | None:
    raw = os.environ.get("THE_ODDS_API_KEY")
    if raw is None:
        return None
    k = raw.strip()
    if not k or k.startswith("#"):
        return None
    return k
