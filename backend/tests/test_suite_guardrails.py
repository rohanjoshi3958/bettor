"""Meta-tests for the guarantees the suite itself must keep: no real key, no real network."""

from __future__ import annotations

import httpx
import pytest

from services import picks_cache
from services.odds import service as svc


class TestEnvironmentIsolation:
    def test_no_odds_api_key_is_visible_to_tests(self):
        assert svc.get_api_key() is None

    def test_cache_ttl_uses_the_documented_default(self):
        assert picks_cache.picks_cache_ttl_seconds() == 90.0

    def test_the_cache_starts_empty(self):
        assert picks_cache._cache == {}


class TestNetworkIsolation:
    async def test_an_unmocked_async_request_fails_the_test(self):
        async with httpx.AsyncClient() as client:
            with pytest.raises(AssertionError, match="real network request"):
                await client.get("https://api.the-odds-api.com/v4/sports")

    def test_an_unmocked_sync_request_fails_the_test(self):
        with httpx.Client() as client:
            with pytest.raises(AssertionError, match="real network request"):
                client.get("https://api.the-odds-api.com/v4/sports")

    async def test_the_odds_service_client_is_mocked_when_requested(self, fake_odds_api):
        async with svc.httpx.AsyncClient() as client:
            response = await client.get(f"{svc.ODDS_BASE}/sports/soccer_epl/odds")
        assert response.status_code == 200
        assert fake_odds_api.paths == ["/v4/sports/soccer_epl/odds"]
