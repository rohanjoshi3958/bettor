"""Domain/ranking layer: implied-probability floor, edge scoring, pick selection, and JSON shaping.

Responsibilities:
- `rank_score`: blend implied probability and line-shopping edge into a sortable score.
- `group_picks_into_games`: apply floor, select top-N per game, sort by kickoff.
- `picks_to_json`: serialize BetPick rows into the frontend response contract.
- `_group_with_implied_fallback`: try the primary floor then a relaxed fallback.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime
from typing import Any

from services.odds.models import MIN_IMPLIED_PROBABILITY, PICKS_PER_GAME, RELAXED_IMPLIED_PROBABILITY, BetPick
from services.odds.normalization import _commence_time_utc, _merge_game_key, _normalize_event_id

# Rank = blend of high implied prob + line-shopping edge (best vs avg among your books).
_RANK_IMPLIED_WEIGHT = 0.55
_RANK_EDGE_WEIGHT = 0.45


def rank_score(p: BetPick) -> float:
    """Blend: implied on 0–100 scale (from best price) + line edge % (best vs mean of your books)."""
    return _RANK_IMPLIED_WEIGHT * (p.implied_probability * 100.0) + _RANK_EDGE_WEIGHT * p.edge_pct


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
            return (datetime.max.replace(tzinfo=UTC), eid)
        return (ct, eid)

    blocks.sort(key=sort_key)
    if max_games is not None:
        blocks = blocks[:max_games]
    return blocks


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
