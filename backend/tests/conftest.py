"""Test-wide fixtures: environment isolation, network blocking, cache reset, Odds API fake."""

from __future__ import annotations

import sys
from pathlib import Path

import httpx
import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

# Imported eagerly: `app.main` loads `.env` into os.environ at import time, so it must happen
# before `isolated_env` strips the Odds API key — otherwise a developer's local key leaks in.
from app.main import app as fastapi_app  # noqa: E402
from tests.helpers import (  # noqa: E402
    FakeOddsAPI,
    install_fake_odds_api,
    install_instant_sleep,
    reset_picks_cache,
)


@pytest.fixture(autouse=True)
def isolated_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """No test may inherit a real Odds API key or a developer's cache/quota tuning."""
    for name in (
        "THE_ODDS_API_KEY",
        "PICKS_CACHE_TTL_SECONDS",
        "MAX_PROP_EVENTS_PER_SPORT",
        "ODDS_PROP_CONCURRENCY",
        "LOG_LEVEL",
        "LOG_FORMAT",
        "RENDER",
        "ENVIRONMENT",
        "ENV",
    ):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture(autouse=True)
def block_outbound_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fail loudly instead of reaching the internet; mocked transports are unaffected."""

    def refuse(request: httpx.Request) -> None:
        raise AssertionError(
            f"test attempted a real network request to {request.url} — mock the Odds API instead"
        )

    async def refuse_async(self, request: httpx.Request, **kwargs: object) -> None:
        refuse(request)

    def refuse_sync(self, request: httpx.Request, **kwargs: object) -> None:
        refuse(request)

    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", refuse_async)
    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", refuse_sync)


@pytest.fixture(autouse=True)
def clean_picks_cache() -> None:
    reset_picks_cache()
    yield
    reset_picks_cache()


@pytest.fixture(autouse=True)
def clean_odds_metrics() -> None:
    from services.odds_metrics import reset_metrics

    reset_metrics()
    yield
    reset_metrics()


@pytest.fixture(autouse=True)
def instant_sleep(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    """Collapse the service's retry backoff; the returned list records requested delays."""
    return install_instant_sleep(monkeypatch)


@pytest.fixture
def fake_odds_api(monkeypatch: pytest.MonkeyPatch) -> FakeOddsAPI:
    return install_fake_odds_api(monkeypatch, FakeOddsAPI())


@pytest.fixture
def client():
    """FastAPI test client. One per test so cache locks stay bound to a single event loop."""
    from fastapi.testclient import TestClient

    with TestClient(fastapi_app) as test_client:
        yield test_client
