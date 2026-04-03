"""
Odds API integration: slate fetch, per-event refresh, pick ranking.

Implementation lives in `service.py`; this module exposes the stable public API.
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
