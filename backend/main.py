"""
Daily sports betting picks API. Set THE_ODDS_API_KEY for live odds (the-odds-api.com).
"""

from datetime import date, datetime
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query
from zoneinfo import ZoneInfo
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from services.odds_service import (
    MIN_IMPLIED_PROBABILITY,
    PICKS_PER_GAME,
    RELAXED_IMPLIED_PROBABILITY,
    fetch_best_picks,
    fetch_picks_for_event,
    get_api_key,
    supported_sport_key,
)

ROOT = Path(__file__).resolve().parent
STATIC = ROOT / "static"
# Repo root first, then backend — backend/.env wins for the same key (local overrides).
load_dotenv(ROOT.parent / ".env")
load_dotenv(ROOT / ".env", override=True)

app = FastAPI(title="Bettor", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/health")
def health():
    return {"ok": True, "live_odds": bool(get_api_key())}


@app.get("/api/picks")
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
        for_day = datetime.now(z).date()

    key = get_api_key()
    (
        rows,
        source,
        min_implied_used,
        used_relaxed_fallback,
        odds_api_warning,
    ) = await fetch_best_picks(
        key,
        picks_per_game=picks_per_game,
        max_games=max_games,
        for_day=for_day,
        timezone_name=tz_name,
    )
    pick_count = sum(len(g.get("picks") or []) for g in rows)
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
        headers={"Cache-Control": "no-store, must-revalidate"},
    )


@app.get("/api/picks/game")
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
        for_day = datetime.now(z).date()

    key = get_api_key()
    (
        game,
        source,
        min_implied_used,
        used_relaxed_fallback,
        odds_api_warning,
    ) = await fetch_picks_for_event(
        key,
        sport_key=sport_key.strip(),
        event_id=event_id.strip(),
        for_day=for_day,
        timezone_name=tz_name,
        picks_per_game=picks_per_game,
    )
    pick_count = len((game or {}).get("picks") or [])
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
        headers={"Cache-Control": "no-store, must-revalidate"},
    )


app.mount("/assets", StaticFiles(directory=str(STATIC)), name="assets")


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


@app.get("/how-it-works")
def how_it_works():
    return FileResponse(STATIC / "how-it-works.html")


@app.get("/favicon.ico")
def favicon():
    return FileResponse(STATIC / "favicon.svg")


if __name__ == "__main__":
    import uvicorn

    # Open http://localhost:8000 in your browser
    uvicorn.run("main:app", host="localhost", port=8000, reload=True)

