"""Public API response models.

These are the authoritative Pydantic shapes for every JSON body the picks API sends to
clients.  Upstream / provider-specific payloads (Odds API responses, internal BetPick
dataclasses, etc.) are intentionally kept separate from these models.
"""

from __future__ import annotations

from pydantic import BaseModel


class PickModel(BaseModel):
    """A single ranked bet pick returned inside a game."""

    sport_key: str
    sport_title: str
    event_id: str
    matchup: str
    home_team: str
    away_team: str
    commence_time: str
    pick: str
    market_key: str | None = None
    implied_probability: float
    implied_pct: float
    rank_score: float
    best_decimal_odds: float
    best_book: str
    avg_decimal_odds: float
    edge_pct: float


class GameModel(BaseModel):
    """A game/event together with its ranked picks."""

    sport_key: str
    sport_title: str
    event_id: str
    home_team: str
    away_team: str
    commence_time: str
    matchup: str
    picks: list[PickModel]


class _PicksMeta(BaseModel):
    """Fields shared by both the slate and the single-game response envelopes."""

    source: str
    pick_count: int
    picks_per_game: int
    game_date: str
    timezone: str
    min_implied_probability: float
    target_implied_probability: float
    relaxed_implied_probability: float
    used_relaxed_implied_fallback: bool
    odds_api_warning: str | None = None


class PicksSlateResponse(_PicksMeta):
    """Response body for ``GET /api/picks`` (full-slate endpoint)."""

    game_count: int
    games: list[GameModel]


class PicksGameResponse(_PicksMeta):
    """Response body for ``GET /api/picks/game`` (single-event endpoint)."""

    game: GameModel | None = None


class HealthResponse(BaseModel):
    """Response body for ``GET /api/health``."""

    ok: bool
    live_odds: bool
