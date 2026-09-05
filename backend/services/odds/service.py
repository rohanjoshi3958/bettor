"""Orchestration layer: coordinate fetching, caching-friendly entry points, and response assembly.

`fetch_best_picks` and `fetch_picks_for_event` are the two public entry points consumed by the
API layer.  They handle demo vs live branching, call the client layer, apply day/kickoff
filtering, run ranking with the implied-probability fallback, and assemble the final response.

All symbols that were previously defined here are re-exported below so existing imports of
`services.odds.service` continue to work unchanged.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import date, datetime, timedelta
from typing import Any

import httpx

from services.odds.client import (  # noqa: F401
    _BOOKMAKERS_PARAM,
    _ODDS_REGIONS,
    _PROP_FETCH_CONCURRENCY,
    MAX_PROP_EVENTS_PER_SPORT,
    ODDS_BASE,
    _fetch_bulk_h2h,
    _fetch_event_props,
    _fetch_events,
    _fetch_prop_sport,
    _note_odds_api_quota_issue,
    _odds_api_warning_message,
)
from services.odds.demo import demo_picks  # noqa: F401

# ---------------------------------------------------------------------------
# Re-exports — maintain backward compatibility for any code importing directly
# from services.odds.service
# ---------------------------------------------------------------------------
from services.odds.models import (  # noqa: F401
    ALLOWED_BOOKMAKER_KEYS,
    MIN_IMPLIED_PROBABILITY,
    PICKS_PER_GAME,
    RELAXED_IMPLIED_PROBABILITY,
    BetPick,
)
from services.odds.normalization import (  # noqa: F401
    _commence_time_utc,
    _event_commence_utc,
    _events_sorted_by_kickoff,
    _events_upcoming_sorted_for_props,
    _exclude_past_kickoff_games,
    _filter_shells_by_game_day,
    _kickoff_still_upcoming,
    _merge_game_key,
    _merge_scheduled_with_picks,
    _normalize_event_id,
    _parse_commence,
    _shells_from_bet_picks,
    _utc_iso_z,
    _zone,
    filter_picks_by_game_day,
    implied_probability,
    local_day_bounds_utc,
    shells_from_scheduled_events,
)
from services.odds.parser import (  # noqa: F401
    _book_allowed,
    _collect_h2h_prices,
    _collect_prop_prices,
    _float_point,
    _format_prop_pick,
    _h2h_events_to_picks,
    _prop_event_to_picks,
    _prop_outcome_key,
)
from services.odds.ranking import (  # noqa: F401
    _group_with_implied_fallback,
    group_picks_into_games,
    picks_to_json,
    rank_score,
)
from services.odds.sports import (  # noqa: F401
    _MARKET_LABELS,
    NBA_PROP_MARKETS,
    NFL_PROP_MARKETS,
    SOCCER_SPORT_KEYS,
    _nba_market_set,
    _nfl_market_set,
    _sport_titles,
    get_api_key,
    supported_sport_key,
)

logger = logging.getLogger("bettor.odds.service")

# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

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
                logger.info(
                    "fetch_picks_for_event_completed",
                    extra={
                        "source": "demo",
                        "sport_key": sport_key,
                        "event_id": eid_norm,
                        "game_date": for_day.isoformat(),
                        "pick_count": len(g0.get("picks") or []),
                        "used_relaxed_fallback": relaxed,
                    },
                )
                return g0, "demo", mi_used, relaxed, None
        logger.info(
            "fetch_picks_for_event_completed",
            extra={
                "source": "demo",
                "sport_key": sport_key,
                "event_id": eid_norm,
                "game_date": for_day.isoformat(),
                "pick_count": 0,
                "used_relaxed_fallback": relaxed,
            },
        )
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

    def _complete(game: dict[str, Any] | None) -> tuple[dict[str, Any] | None, str, float, bool, str | None]:
        logger.info(
            "fetch_picks_for_event_completed",
            extra={
                "source": "live",
                "sport_key": sport_key,
                "event_id": eid_norm,
                "game_date": for_day.isoformat(),
                "pick_count": len((game or {}).get("picks") or []),
                "used_relaxed_fallback": relaxed,
                "has_odds_api_warning": bool(warn),
                "quota_events": list(dict.fromkeys(quota_events)),
            },
        )
        return game, "live", mi_used, relaxed, warn

    for g in games:
        if _normalize_event_id(g.get("event_id")) == eid_norm:
            if _kickoff_still_upcoming(g):
                return _complete(g)
            return _complete(None)
    if games:
        g0 = games[0]
        if _kickoff_still_upcoming(g0):
            return _complete(g0)
    return _complete(None)


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
        logger.info(
            "fetch_best_picks_completed",
            extra={
                "source": "demo",
                "game_date": for_day.isoformat(),
                "timezone": timezone_name,
                "game_count": len(merged),
                "pick_count": sum(len(g.get("picks") or []) for g in merged),
                "used_relaxed_fallback": relaxed,
            },
        )
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
        gather_errors = 0
        for res in gathered:
            if isinstance(res, BaseException):
                gather_errors += 1
                logger.warning(
                    "odds_sport_fanout_error",
                    extra={"error_type": type(res).__name__, "error": str(res)[:200]},
                )
                continue
            picks_part, shells_part = res
            batch.extend(picks_part)
            shell_batch.extend(shells_part)

        all_picks_pre = batch
        all_shells = shell_batch

    seen_keys: dict[tuple[str, str], dict[str, Any]] = {}
    for s in all_shells:
        game_key = _merge_game_key(s.get("sport_key"), s.get("event_id"))
        if game_key not in seen_keys:
            seen_keys[game_key] = s
    shells_unique = list(seen_keys.values())
    shells_filtered = _filter_shells_by_game_key(shells_unique, for_day, timezone_name)

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
    logger.info(
        "fetch_best_picks_completed",
        extra={
            "source": "live",
            "game_date": for_day.isoformat(),
            "timezone": timezone_name,
            "game_count": len(merged),
            "pick_count": sum(len(g.get("picks") or []) for g in merged),
            "used_relaxed_fallback": relaxed,
            "has_odds_api_warning": bool(warn),
            "quota_events": list(dict.fromkeys(quota_events)),
            "fanout_errors": gather_errors,
        },
    )
    return merged, "live", mi_used, relaxed, warn


# This private alias avoids polluting the normalization module's public surface while
# keeping the call site in fetch_best_picks readable.
_filter_shells_by_game_key = _filter_shells_by_game_day
