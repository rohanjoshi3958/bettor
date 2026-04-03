"""Pickable game-day window in the user’s timezone (must match frontend date bounds)."""

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from fastapi import HTTPException

# Today through today + offset (inclusive), in the request TZ. Matches JS `PICKS_FUTURE_END_OFFSET`.
PICKS_FUTURE_END_OFFSET = 5


def pickable_game_day_bounds(z: ZoneInfo) -> tuple[date, date]:
    today = datetime.now(z).date()
    last = today + timedelta(days=PICKS_FUTURE_END_OFFSET)
    return today, last


def default_pickable_game_day(z: ZoneInfo) -> date:
    first, _ = pickable_game_day_bounds(z)
    return first


def enforce_pickable_game_day(for_day: date, z: ZoneInfo) -> None:
    first, last = pickable_game_day_bounds(z)
    if for_day < first or for_day > last:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Game day must be from {first.isoformat()} through {last.isoformat()} "
                f"(today through {PICKS_FUTURE_END_OFFSET} days ahead in your timezone)."
            ),
        ) from None
