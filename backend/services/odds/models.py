"""Shared domain types and constants for the odds service.

`BetPick` is the internal representation of a ranked betting line that flows from
the parser/normalization layer through ranking and into response assembly.
"""

from __future__ import annotations

from dataclasses import dataclass

from services import ranking

# Ranking floors are owned by the dependency-free ranking engine; keep these names
# as compatibility aliases for callers and response metadata.
MIN_IMPLIED_PROBABILITY = ranking.DEFAULT_RANKING_CONFIG.min_implied_probability
RELAXED_IMPLIED_PROBABILITY = ranking.DEFAULT_RANKING_CONFIG.relaxed_implied_probability

# Top picks to keep per game (grouped by event).
PICKS_PER_GAME = 3

# Only these books (The Odds API keys) — used by both the client layer (request param)
# and the parser layer (response filter).
ALLOWED_BOOKMAKER_KEYS: frozenset[str] = frozenset(
    {"draftkings", "fanduel", "fanatics"}
)


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
