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
from dataclasses import dataclass, field
from typing import Any, TypeVar

from services.odds_metrics import record_cache_result

T = TypeVar("T")
logger = logging.getLogger("bettor.cache")

# fetch_best_picks / fetch_picks_for_event return a tuple whose last element is odds_api_warning.
def _odds_warning_on_result(result: Any) -> str | None:
    if not isinstance(result, tuple) or len(result) < 1:
        return None
    w = result[-1]
    return w if isinstance(w, str) and w else None


@dataclass
class _KeyLock:
    """Per-key mutex with a waiter count so idle locks can be dropped safely."""

    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    waiters: int = 0


_meta_lock = asyncio.Lock()
_key_locks: dict[str, _KeyLock] = {}
_cache: dict[str, tuple[float, Any]] = {}


def picks_cache_ttl_seconds() -> float:
    raw = os.environ.get("PICKS_CACHE_TTL_SECONDS", "90").strip()
    try:
        return max(0.0, float(raw))
    except ValueError:
        return 90.0


async def _acquire_key_lock(key: str) -> _KeyLock:
    async with _meta_lock:
        entry = _key_locks.get(key)
        if entry is None:
            entry = _KeyLock()
            _key_locks[key] = entry
        entry.waiters += 1
        return entry


async def _release_key_lock(key: str, entry: _KeyLock) -> None:
    async with _meta_lock:
        entry.waiters -= 1
        if entry.waiters == 0 and _key_locks.get(key) is entry:
            del _key_locks[key]


async def _await_release_key_lock(key: str, entry: _KeyLock) -> None:
    """Await key-lock release without letting cancellation strand waiters.

    ``cached_fetch`` may already be cancelled when it reaches ``finally``. An
    ``await`` on ``_meta_lock`` inside release can then raise ``CancelledError``
    before ``waiters`` is decremented, leaving orphaned ``_key_locks`` entries.
    Shield the cleanup task and keep waiting through repeated cancellation.
    """
    cleanup = asyncio.create_task(_release_key_lock(key, entry))
    try:
        await asyncio.shield(cleanup)
    except asyncio.CancelledError:
        while not cleanup.done():
            try:
                await asyncio.shield(cleanup)
            except asyncio.CancelledError:
                pass
        raise


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
        record_cache_result("bypass")
        return await factory(), False

    entry = await _acquire_key_lock(cache_key)
    try:
        async with entry.lock:
            _prune_expired()
            now = time.monotonic()
            if cache_key in _cache:
                exp, val = _cache[cache_key]
                if exp > now:
                    logger.info("picks_cache_hit", extra={"cache_key": cache_key})
                    record_cache_result("hit")
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
            record_cache_result("miss")
            return deepcopy(fresh), False
    finally:
        await _await_release_key_lock(cache_key, entry)
