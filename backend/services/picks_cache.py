"""
Short-TTL in-memory cache for Odds API-backed fetches.

Set PICKS_CACHE_TTL_SECONDS (default 90). Use 0 to disable.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from collections.abc import Awaitable, Callable
from copy import deepcopy
from typing import Any, TypeVar

T = TypeVar("T")
logger = logging.getLogger("bettor.cache")

# fetch_best_picks / fetch_picks_for_event return a tuple whose last element is odds_api_warning.
def _odds_warning_on_result(result: Any) -> str | None:
    if not isinstance(result, tuple) or len(result) < 1:
        return None
    w = result[-1]
    return w if isinstance(w, str) and w else None

_meta_lock = asyncio.Lock()
_key_locks: dict[str, asyncio.Lock] = {}
_cache: dict[str, tuple[float, Any]] = {}


def picks_cache_ttl_seconds() -> float:
    raw = os.environ.get("PICKS_CACHE_TTL_SECONDS", "90").strip()
    try:
        return max(0.0, float(raw))
    except ValueError:
        return 90.0


async def _lock_for(key: str) -> asyncio.Lock:
    async with _meta_lock:
        if key not in _key_locks:
            _key_locks[key] = asyncio.Lock()
        return _key_locks[key]


def _prune_expired() -> None:
    now = time.monotonic()
    dead = [k for k, (exp, _) in _cache.items() if exp <= now]
    for k in dead:
        del _cache[k]


async def cached_fetch(
    cache_key: str,
    factory: Callable[[], Awaitable[T]],
) -> tuple[T, bool]:
    """Return (result, cache_hit). Result is a deep copy when served from cache.

    Responses that include an Odds API warning (last tuple element) are not stored, so a later
    reload can get a clean slate without replaying a stale banner from one bad fan-out.
    """
    ttl = picks_cache_ttl_seconds()
    if ttl <= 0:
        logger.info("picks_cache_bypass", extra={"cache_key": cache_key, "ttl_seconds": ttl})
        return await factory(), False

    lock = await _lock_for(cache_key)
    async with lock:
        _prune_expired()
        now = time.monotonic()
        if cache_key in _cache:
            exp, val = _cache[cache_key]
            if exp > now:
                logger.info("picks_cache_hit", extra={"cache_key": cache_key})
                return deepcopy(val), True

        fresh = await factory()
        if _odds_warning_on_result(fresh) is None:
            _cache[cache_key] = (now + ttl, fresh)
            logger.info("picks_cache_miss", extra={"cache_key": cache_key, "stored": True})
        else:
            logger.info(
                "picks_cache_miss",
                extra={"cache_key": cache_key, "stored": False, "reason": "odds_api_warning"},
            )
        return deepcopy(fresh), False
