"""JSON API: health, full slate, single-game picks."""

from datetime import date

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import JSONResponse
from zoneinfo import ZoneInfo

from app.core.calendar import default_pickable_game_day, enforce_pickable_game_day
from services.odds import (
    MIN_IMPLIED_PROBABILITY,
    PICKS_PER_GAME,
    RELAXED_IMPLIED_PROBABILITY,
    fetch_best_picks,
    fetch_picks_for_event,
    get_api_key,
    supported_sport_key,
)
from services.picks_cache import cached_fetch, picks_cache_ttl_seconds

router = APIRouter(prefix="/api", tags=["picks"])


def _cap_picks(game: dict | None, ppg: int) -> None:
    """Ensure responses never exceed the requested picks_per_game (guards cache / merge edge cases)."""
    if not game:
        return
    picks = game.get("picks") or []
    if len(picks) > ppg:
        game["picks"] = picks[:ppg]


def _cap_slate_games(games: list[dict], ppg: int) -> None:
    for g in games:
        picks = g.get("picks") or []
        if len(picks) > ppg:
            g["picks"] = picks[:ppg]


@router.get("/health")
def health():
    return {"ok": True, "live_odds": bool(get_api_key())}


@router.get("/picks")
async def picks(
    picks_per_game: int = Query(default=PICKS_PER_GAME, ge=1, le=25),
    max_games: int | None = Query(default=None),
    game_date: str | None = Query(default=None, alias="date"),
    tz_name: str = Query(default="America/New_York", alias="timezone"),
):
    try:
        z = ZoneInfo(tz_name)
    except Exception:
        z = ZoneInfo("UTC")
    if max_games is not None and not (1 <= max_games <= 200):
        raise HTTPException(status_code=400, detail="max_games must be 1–200 or omitted") from None
    if game_date and game_date.strip():
        try:
            for_day = date.fromisoformat(game_date.strip())
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid date=YYYY-MM-DD") from None
    else:
        for_day = default_pickable_game_day(z)

    enforce_pickable_game_day(for_day, z)

    key = get_api_key()
    mg_part = "" if max_games is None else str(max_games)
    cache_key = (
        f"s|{for_day.isoformat()}|{tz_name}|{picks_per_game}|{mg_part}|"
        f"{1 if (key and key.strip()) else 0}"
    )

    async def _load_slate():
        return await fetch_best_picks(
            key,
            picks_per_game=picks_per_game,
            max_games=max_games,
            for_day=for_day,
            timezone_name=tz_name,
        )

    (
        rows,
        source,
        min_implied_used,
        used_relaxed_fallback,
        odds_api_warning,
    ), cache_hit = await cached_fetch(cache_key, _load_slate)

    _cap_slate_games(rows, picks_per_game)
    pick_count = sum(len(g.get("picks") or []) for g in rows)
    cache_hdr = (
        "hit"
        if cache_hit
        else ("bypass" if picks_cache_ttl_seconds() <= 0 else "miss")
    )
    return JSONResponse(
        {
            "source": source,
            "game_count": len(rows),
            "pick_count": pick_count,
            "picks_per_game": picks_per_game,
            "game_date": for_day.isoformat(),
            "timezone": tz_name,
            "min_implied_probability": min_implied_used,
            "target_implied_probability": MIN_IMPLIED_PROBABILITY,
            "relaxed_implied_probability": RELAXED_IMPLIED_PROBABILITY,
            "used_relaxed_implied_fallback": used_relaxed_fallback,
            "odds_api_warning": odds_api_warning,
            "games": rows,
        },
        headers={
            "Cache-Control": "no-store, must-revalidate",
            "X-Picks-Cache": cache_hdr,
        },
    )


@router.get("/picks/game")
async def picks_one_game(
    sport_key: str = Query(..., min_length=1),
    event_id: str = Query(..., min_length=1),
    picks_per_game: int = Query(default=PICKS_PER_GAME, ge=1, le=25),
    game_date: str | None = Query(default=None, alias="date"),
    tz_name: str = Query(default="America/New_York", alias="timezone"),
):
    if not supported_sport_key(sport_key):
        raise HTTPException(
            status_code=400,
            detail="Unsupported sport_key for this endpoint",
        ) from None
    try:
        z = ZoneInfo(tz_name)
    except Exception:
        z = ZoneInfo("UTC")
    if game_date and game_date.strip():
        try:
            for_day = date.fromisoformat(game_date.strip())
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid date=YYYY-MM-DD") from None
    else:
        for_day = default_pickable_game_day(z)

    enforce_pickable_game_day(for_day, z)

    key = get_api_key()
    sk = sport_key.strip()
    eid = event_id.strip()
    cache_key = (
        f"g|{sk}|{eid}|{for_day.isoformat()}|{tz_name}|{picks_per_game}|"
        f"{1 if (key and key.strip()) else 0}"
    )

    async def _load_game():
        return await fetch_picks_for_event(
            key,
            sport_key=sk,
            event_id=eid,
            for_day=for_day,
            timezone_name=tz_name,
            picks_per_game=picks_per_game,
        )

    (
        game,
        source,
        min_implied_used,
        used_relaxed_fallback,
        odds_api_warning,
    ), cache_hit = await cached_fetch(cache_key, _load_game)

    _cap_picks(game, picks_per_game)
    pick_count = len((game or {}).get("picks") or [])
    cache_hdr = (
        "hit"
        if cache_hit
        else ("bypass" if picks_cache_ttl_seconds() <= 0 else "miss")
    )
    return JSONResponse(
        {
            "source": source,
            "game": game,
            "pick_count": pick_count,
            "picks_per_game": picks_per_game,
            "game_date": for_day.isoformat(),
            "timezone": tz_name,
            "min_implied_probability": min_implied_used,
            "target_implied_probability": MIN_IMPLIED_PROBABILITY,
            "relaxed_implied_probability": RELAXED_IMPLIED_PROBABILITY,
            "used_relaxed_implied_fallback": used_relaxed_fallback,
            "odds_api_warning": odds_api_warning,
        },
        headers={
            "Cache-Control": "no-store, must-revalidate",
            "X-Picks-Cache": cache_hdr,
        },
    )
