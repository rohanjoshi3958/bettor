"""Cache hit/miss, TTL, deep-copy isolation and per-key locking for `services.picks_cache`."""

from __future__ import annotations

import asyncio
import sys

import pytest

from services import picks_cache
from tests.helpers import FakeMonotonic


class Counter:
    """Factory that records how many times the expensive fetch actually ran."""

    def __init__(self, result=("payload", None)):
        self.calls = 0
        self.result = result

    async def __call__(self):
        self.calls += 1
        return self.result


@pytest.fixture
def fake_clock(monkeypatch):
    clock = FakeMonotonic()
    monkeypatch.setattr(picks_cache, "time", clock)
    return clock


class TestTtlConfiguration:
    def test_default_ttl_is_ninety_seconds(self, monkeypatch):
        monkeypatch.delenv("PICKS_CACHE_TTL_SECONDS", raising=False)
        assert picks_cache.picks_cache_ttl_seconds() == 90.0

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [("0", 0.0), ("30", 30.0), (" 12.5 ", 12.5), ("-5", 0.0), ("", 90.0), ("abc", 90.0)],
    )
    def test_env_override_is_parsed_defensively(self, monkeypatch, raw, expected):
        monkeypatch.setenv("PICKS_CACHE_TTL_SECONDS", raw)
        assert picks_cache.picks_cache_ttl_seconds() == expected


class TestCacheHitAndMiss:
    async def test_first_call_is_a_miss_and_runs_the_factory(self):
        factory = Counter()
        result, hit = await picks_cache.cached_fetch("k", factory)
        assert result == ("payload", None)
        assert hit is False
        assert factory.calls == 1

    async def test_second_call_is_a_hit_and_skips_the_factory(self):
        factory = Counter()
        await picks_cache.cached_fetch("k", factory)
        result, hit = await picks_cache.cached_fetch("k", factory)
        assert hit is True
        assert result == ("payload", None)
        assert factory.calls == 1

    async def test_different_keys_do_not_share_entries(self):
        slate = Counter(("slate", None))
        game = Counter(("game", None))
        assert (await picks_cache.cached_fetch("slate|k", slate))[0] == ("slate", None)
        assert (await picks_cache.cached_fetch("game|k", game))[0] == ("game", None)
        assert slate.calls == game.calls == 1

    async def test_cached_value_survives_repeated_hits(self):
        factory = Counter()
        for _ in range(5):
            await picks_cache.cached_fetch("k", factory)
        assert factory.calls == 1


class TestTtlExpiry:
    async def test_entry_is_served_until_the_ttl_elapses(self, monkeypatch, fake_clock):
        monkeypatch.setenv("PICKS_CACHE_TTL_SECONDS", "90")
        factory = Counter()
        await picks_cache.cached_fetch("k", factory)

        fake_clock.advance(89)
        _, hit = await picks_cache.cached_fetch("k", factory)
        assert hit is True
        assert factory.calls == 1

    async def test_entry_is_refetched_once_the_ttl_passes(self, monkeypatch, fake_clock):
        monkeypatch.setenv("PICKS_CACHE_TTL_SECONDS", "90")
        factory = Counter()
        await picks_cache.cached_fetch("k", factory)

        fake_clock.advance(91)
        _, hit = await picks_cache.cached_fetch("k", factory)
        assert hit is False
        assert factory.calls == 2

    async def test_expiry_is_exclusive_at_the_boundary(self, monkeypatch, fake_clock):
        monkeypatch.setenv("PICKS_CACHE_TTL_SECONDS", "90")
        factory = Counter()
        await picks_cache.cached_fetch("k", factory)

        fake_clock.advance(90)
        _, hit = await picks_cache.cached_fetch("k", factory)
        assert hit is False

    async def test_expired_entries_are_pruned_from_the_dict(self, monkeypatch, fake_clock):
        monkeypatch.setenv("PICKS_CACHE_TTL_SECONDS", "10")
        await picks_cache.cached_fetch("stale", Counter())
        assert "stale" in picks_cache._cache

        fake_clock.advance(11)
        await picks_cache.cached_fetch("other", Counter())
        assert "stale" not in picks_cache._cache

    async def test_ttl_is_read_per_call_so_config_changes_take_effect(self, monkeypatch, fake_clock):
        monkeypatch.setenv("PICKS_CACHE_TTL_SECONDS", "90")
        factory = Counter()
        await picks_cache.cached_fetch("k", factory)

        monkeypatch.setenv("PICKS_CACHE_TTL_SECONDS", "0")
        _, hit = await picks_cache.cached_fetch("k", factory)
        assert hit is False
        assert factory.calls == 2


class TestCacheDisabled:
    async def test_zero_ttl_bypasses_the_cache_entirely(self, monkeypatch):
        monkeypatch.setenv("PICKS_CACHE_TTL_SECONDS", "0")
        factory = Counter()
        for _ in range(3):
            result, hit = await picks_cache.cached_fetch("k", factory)
            assert hit is False
        assert factory.calls == 3
        assert picks_cache._cache == {}

    async def test_zero_ttl_returns_the_factory_result_directly(self, monkeypatch):
        monkeypatch.setenv("PICKS_CACHE_TTL_SECONDS", "0")
        payload = (["game"], None)
        result, _ = await picks_cache.cached_fetch("k", Counter(payload))
        assert result is payload


class TestCopyOnReadIsolation:
    async def test_callers_cannot_mutate_the_cached_payload(self):
        payload = ({"games": [{"picks": ["a"]}]}, None)
        first, _ = await picks_cache.cached_fetch("k", Counter(payload))
        first[0]["games"][0]["picks"].append("injected")

        second, hit = await picks_cache.cached_fetch("k", Counter(payload))
        assert hit is True
        assert second[0]["games"][0]["picks"] == ["a"]

    async def test_the_factory_result_is_also_copied_on_the_miss(self):
        payload = ({"games": []}, None)
        result, _ = await picks_cache.cached_fetch("k", Counter(payload))
        assert result is not payload
        assert result == payload

    async def test_mutating_the_miss_result_does_not_poison_later_hits(self):
        """`/picks` caps picks per game on the returned rows, so the miss result must be a copy."""
        factory = Counter(({"games": [{"picks": ["a", "b", "c"]}]}, None))
        miss, _ = await picks_cache.cached_fetch("k", factory)
        miss[0]["games"][0]["picks"] = miss[0]["games"][0]["picks"][:1]

        hit_result, hit = await picks_cache.cached_fetch("k", factory)
        assert hit is True
        assert hit_result[0]["games"][0]["picks"] == ["a", "b", "c"]


class TestWarningResultsAreNotCached:
    async def test_a_slate_with_an_odds_api_warning_is_refetched(self):
        factory = Counter((["partial"], "The Odds API returned rate limits (HTTP 429)."))
        await picks_cache.cached_fetch("k", factory)
        _, hit = await picks_cache.cached_fetch("k", factory)
        assert hit is False
        assert factory.calls == 2
        assert picks_cache._cache == {}

    async def test_a_clean_slate_after_a_warning_gets_cached(self):
        warned = Counter((["partial"], "rate limited"))
        await picks_cache.cached_fetch("k", warned)
        clean = Counter((["full"], None))
        await picks_cache.cached_fetch("k", clean)
        _, hit = await picks_cache.cached_fetch("k", clean)
        assert hit is True
        assert clean.calls == 1

    @pytest.mark.parametrize(
        ("result", "expected"),
        [
            ((["x"], None), None),
            ((["x"], ""), None),
            ((["x"], "boom"), "boom"),
            (("only-warning",), "only-warning"),
            ((), None),
            (["x", "boom"], None),
            ("not-a-tuple", None),
            ((["x"], 429), None),
        ],
    )
    def test_warning_detection_only_trusts_a_trailing_string(self, result, expected):
        assert picks_cache._odds_warning_on_result(result) == expected


class TestPerKeyLocking:
    async def test_concurrent_requests_for_one_key_share_a_single_fetch(self):
        """Two users loading the same slate must not double the upstream Odds API fan-out."""
        started = asyncio.Event()
        release = asyncio.Event()
        calls = 0

        async def slow_factory():
            nonlocal calls
            calls += 1
            started.set()
            await release.wait()
            return (["slate"], None)

        first = asyncio.create_task(picks_cache.cached_fetch("k", slow_factory))
        await started.wait()
        second = asyncio.create_task(picks_cache.cached_fetch("k", slow_factory))
        await asyncio.sleep(0)
        release.set()

        (r1, hit1), (r2, hit2) = await asyncio.gather(first, second)
        assert calls == 1
        assert r1 == r2 == (["slate"], None)
        assert (hit1, hit2) == (False, True)

    async def test_a_stampede_of_requests_collapses_to_one_fetch(self):
        release = asyncio.Event()
        calls = 0

        async def slow_factory():
            nonlocal calls
            calls += 1
            await release.wait()
            return (["slate"], None)

        tasks = [
            asyncio.create_task(picks_cache.cached_fetch("k", slow_factory)) for _ in range(20)
        ]
        await asyncio.sleep(0)
        release.set()
        results = await asyncio.gather(*tasks)

        assert calls == 1
        assert sum(1 for _, hit in results if hit) == 19

    async def test_different_keys_are_fetched_concurrently(self):
        """A slow slate for one date must not block a different date behind the same lock."""
        both_running = asyncio.Barrier(2)

        async def factory(name):
            async def run():
                await asyncio.wait_for(both_running.wait(), timeout=2.0)
                return ([name], None)

            return run

        slate_a = await factory("a")
        slate_b = await factory("b")
        results = await asyncio.gather(
            picks_cache.cached_fetch("a", slate_a),
            picks_cache.cached_fetch("b", slate_b),
        )
        assert [r[0][0] for r in results] == [["a"], ["b"]]

    async def test_a_failing_fetch_releases_the_lock_for_the_next_caller(self):
        async def boom():
            raise RuntimeError("upstream exploded")

        with pytest.raises(RuntimeError):
            await picks_cache.cached_fetch("k", boom)

        result, hit = await picks_cache.cached_fetch("k", Counter())
        assert hit is False
        assert result == ("payload", None)

    async def test_a_failing_fetch_is_not_cached(self):
        async def boom():
            raise RuntimeError("upstream exploded")

        with pytest.raises(RuntimeError):
            await picks_cache.cached_fetch("k", boom)
        assert picks_cache._cache == {}

    async def test_concurrent_callers_share_one_key_lock(self):
        """While a fetch is in flight, every waiter must sit on the same mutex."""
        started = asyncio.Event()
        release = asyncio.Event()

        async def slow_factory():
            started.set()
            await release.wait()
            return (["slate"], None)

        first = asyncio.create_task(picks_cache.cached_fetch("k", slow_factory))
        await started.wait()
        in_flight = picks_cache._key_locks["k"]
        assert in_flight.waiters >= 1

        second = asyncio.create_task(picks_cache.cached_fetch("k", Counter()))
        for _ in range(50):
            await asyncio.sleep(0)
            if in_flight.waiters == 2:
                break

        assert picks_cache._key_locks["k"] is in_flight
        assert in_flight.waiters == 2

        release.set()
        await asyncio.gather(first, second)
        assert picks_cache._key_locks == {}

    async def test_idle_key_locks_are_removed_after_the_last_waiter(self):
        await picks_cache.cached_fetch("k", Counter())
        assert picks_cache._key_locks == {}

    async def test_a_new_lock_is_created_after_the_previous_one_is_released(self):
        started = asyncio.Event()
        release = asyncio.Event()

        async def slow_factory():
            started.set()
            await release.wait()
            return ("payload", None)

        first = asyncio.create_task(picks_cache.cached_fetch("k", slow_factory))
        await started.wait()
        first_generation = picks_cache._key_locks["k"]
        release.set()
        await first
        assert picks_cache._key_locks == {}

        # Expire the cached entry so the next call must fetch again (and allocate a new lock).
        picks_cache._cache.clear()

        started2 = asyncio.Event()
        release2 = asyncio.Event()

        async def slow_factory2():
            started2.set()
            await release2.wait()
            return ("payload", None)

        second = asyncio.create_task(picks_cache.cached_fetch("k", slow_factory2))
        await started2.wait()
        second_generation = picks_cache._key_locks["k"]
        release2.set()
        await second

        assert picks_cache._key_locks == {}
        assert second_generation is not first_generation

    async def test_concurrent_first_touches_of_one_key_create_one_lock(self):
        release = asyncio.Event()
        calls = 0

        async def slow_factory():
            nonlocal calls
            calls += 1
            await release.wait()
            return ("payload", None)

        tasks = [
            asyncio.create_task(picks_cache.cached_fetch("k", slow_factory)) for _ in range(10)
        ]
        for _ in range(50):
            await asyncio.sleep(0)
            entry = picks_cache._key_locks.get("k")
            if entry is not None and entry.waiters == 10:
                break

        assert list(picks_cache._key_locks) == ["k"]
        assert picks_cache._key_locks["k"].waiters == 10

        release.set()
        await asyncio.gather(*tasks)
        assert calls == 1
        assert picks_cache._key_locks == {}

    async def test_a_failing_fetch_still_drops_the_idle_lock(self):
        async def boom():
            raise RuntimeError("upstream exploded")

        with pytest.raises(RuntimeError):
            await picks_cache.cached_fetch("k", boom)
        assert picks_cache._key_locks == {}

    async def test_many_distinct_keys_do_not_leave_orphan_locks(self):
        await asyncio.gather(
            *[picks_cache.cached_fetch(f"k{i}", Counter()) for i in range(50)],
        )
        assert picks_cache._key_locks == {}

    @pytest.mark.skipif(sys.version_info < (3, 11), reason="cancellation cleanup asserted for Python 3.11+")
    async def test_second_cancellation_during_lock_release_still_removes_key_lock(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        """A cancel while release awaits ``_meta_lock`` must not orphan the entry."""
        release_entered = asyncio.Event()
        allow_release = asyncio.Event()
        real_release = picks_cache._release_key_lock

        async def blocked_release(key: str, entry: picks_cache._KeyLock) -> None:
            release_entered.set()
            await allow_release.wait()
            await real_release(key, entry)

        monkeypatch.setattr(picks_cache, "_release_key_lock", blocked_release)

        factory_entered = asyncio.Event()
        hold_factory = asyncio.Event()

        async def slow_factory():
            factory_entered.set()
            await hold_factory.wait()
            return ("payload", None)

        task = asyncio.create_task(picks_cache.cached_fetch("cancel-key", slow_factory))
        await factory_entered.wait()
        assert "cancel-key" in picks_cache._key_locks

        task.cancel()
        await release_entered.wait()
        assert "cancel-key" in picks_cache._key_locks

        # Second cancellation while cleanup is blocked (simulating wait on _meta_lock).
        task.cancel()
        await asyncio.sleep(0)

        allow_release.set()
        with pytest.raises(asyncio.CancelledError):
            await task

        assert picks_cache._key_locks == {}
