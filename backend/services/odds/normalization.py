"""Normalization and time-window utilities for the odds service.

Responsibilities:
- Event ID normalisation (int / float / string forms collapse to a join-safe string).
- Implied probability from decimal odds.
- UTC ↔ local-day conversion and ISO formatting.
- Schedule shell construction and merging.
- Day/kickoff filtering (started-game exclusion).
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from services.odds.models import BetPick


# ---------------------------------------------------------------------------
# Event ID normalisation
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Implied probability
# ---------------------------------------------------------------------------

def implied_probability(decimal_odds: float) -> float:
    """Naive implied prob from the best decimal price: 1/odds (no de-vig across books)."""
    if decimal_odds <= 1.0:
        return 0.0
    return min(1.0, 1.0 / decimal_odds)


# ---------------------------------------------------------------------------
# Time helpers
# ---------------------------------------------------------------------------

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


def _parse_commence(raw: Any) -> datetime | None:
    """Upstream commence_time is normally an ISO string, but treat any other shape as unknown."""
    try:
        s = raw[:-1] + "+00:00" if raw.endswith("Z") else raw
        return datetime.fromisoformat(s)
    except (AttributeError, TypeError, ValueError):
        return None


def _commence_time_utc(raw: Any) -> datetime | None:
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


# ---------------------------------------------------------------------------
# Schedule shell construction
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Schedule/picks merge and day filtering
# ---------------------------------------------------------------------------

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
    """Keep only picks whose kickoff falls on `for_day` in `tz_name`. If today, drop started games."""
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


# ---------------------------------------------------------------------------
# Event sorting helpers (used by client and normalization)
# ---------------------------------------------------------------------------

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
    """Not-started events only, sorted by kickoff; cap limits Odds API /events/{id}/odds volume."""
    now = datetime.now(timezone.utc)
    out: list[dict[str, Any]] = []
    for ev in _events_sorted_by_kickoff(events):
        ct = _event_commence_utc(ev)
        if ct is None or ct > now:
            out.append(ev)
    return out[:cap] if cap > 0 else out
