"""Deterministic, dependency-free pick-ranking policy.

This module deliberately knows only the two metrics used to rank a pick.  It is
safe to use from a worker, HTTP handler, cache layer, or a unit test without
bringing along any odds-provider or framework dependencies.

The default policy preserves the product's established score:
``0.55 * implied_probability_percent + 0.45 * line_shopping_edge_percent``.
Missing, non-numeric, or non-finite metrics contribute zero.  That makes
incomplete odds rows safe to rank while retaining the score for valid rows.
Python's stable sort is intentional: equal scores retain their input order.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Callable, Iterable, TypeVar


@dataclass(frozen=True)
class RankingConfig:
    """Centralized knobs for pick eligibility and scoring.

    The weights operate on implied probability expressed as a percentage and
    on line-shopping edge expressed as a percentage.  The two implied-probability
    floors are used by the service's normal and fallback passes.
    """

    implied_probability_weight: float = 0.55
    edge_weight: float = 0.45
    min_implied_probability: float = 0.52
    relaxed_implied_probability: float = 0.50


DEFAULT_RANKING_CONFIG = RankingConfig()


@dataclass(frozen=True)
class PickRankingMetrics:
    """The complete input contract required by the pick-ranking engine."""

    implied_probability: float | None
    edge_pct: float | None


def _finite_number_or_zero(value: object) -> float:
    """Treat absent, malformed, and non-finite metrics as an explicit zero."""
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0.0
    return number if math.isfinite(number) else 0.0


def implied_probability(decimal_odds: float | None) -> float:
    """Return naive market implied probability (``1 / decimal_odds``), or zero.

    Zero, missing, malformed, and non-positive-profit decimal odds cannot yield
    a meaningful probability and therefore explicitly produce ``0.0``.
    """
    odds = _finite_number_or_zero(decimal_odds)
    if odds <= 1.0:
        return 0.0
    return min(1.0, 1.0 / odds)


def score(
    metrics: PickRankingMetrics,
    config: RankingConfig = DEFAULT_RANKING_CONFIG,
) -> float:
    """Calculate the configured score for one pick's ranking metrics."""
    implied_probability = _finite_number_or_zero(metrics.implied_probability)
    edge_pct = _finite_number_or_zero(metrics.edge_pct)
    return (
        config.implied_probability_weight * (implied_probability * 100.0)
        + config.edge_weight * edge_pct
    )


def meets_implied_probability_floor(
    metrics: PickRankingMetrics,
    floor: float,
) -> bool:
    """Whether a pick qualifies for a floor; missing metrics deliberately fail."""
    return _finite_number_or_zero(metrics.implied_probability) >= floor


Pick = TypeVar("Pick")


def sort_picks(
    picks: Iterable[Pick],
    metrics_for: Callable[[Pick], PickRankingMetrics],
    config: RankingConfig = DEFAULT_RANKING_CONFIG,
) -> list[Pick]:
    """Return picks by descending score, preserving input order for tied scores."""
    return sorted(picks, key=lambda pick: score(metrics_for(pick), config), reverse=True)
