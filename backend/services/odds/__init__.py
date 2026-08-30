"""
Odds API integration: slate fetch, per-event refresh, pick ranking.

Module structure
----------------
models        — BetPick dataclass and shared constants
sports        — sport keys, markets, display titles, API-key resolution
normalization — event-ID normalisation, time utilities, shell/schedule helpers
parser        — convert raw Odds API payloads into BetPick rows
ranking       — rank_score, group_picks_into_games, picks_to_json, implied-prob fallback
demo          — static fallback picks for demo/preview mode
client        — HTTP requests to The Odds API, quota tracking, warning messages
service       — orchestration (fetch_best_picks, fetch_picks_for_event) + public re-exports
"""

from services.odds.service import (
    MIN_IMPLIED_PROBABILITY,
    PICKS_PER_GAME,
    RELAXED_IMPLIED_PROBABILITY,
    BetPick,
    fetch_best_picks,
    fetch_picks_for_event,
    get_api_key,
    group_picks_into_games,
    implied_probability,
    picks_to_json,
    rank_score,
    supported_sport_key,
)

__all__ = [
    "MIN_IMPLIED_PROBABILITY",
    "PICKS_PER_GAME",
    "RELAXED_IMPLIED_PROBABILITY",
    "BetPick",
    "fetch_best_picks",
    "fetch_picks_for_event",
    "get_api_key",
    "group_picks_into_games",
    "implied_probability",
    "picks_to_json",
    "rank_score",
    "supported_sport_key",
]
