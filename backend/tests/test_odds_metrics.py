"""Odds API / HTTP metrics instrumentation (BET-9)."""

from __future__ import annotations

from datetime import UTC, datetime

import httpx
import pytest

from services import odds_metrics as metrics
from services.odds import client as client_mod
from services.odds import service as svc
from services.picks_cache import cached_fetch
from tests.helpers import Fail, Raise, freeze_time, h2h_quotes, odds_event

NOW = datetime(2026, 6, 15, 18, 0, tzinfo=UTC)


@pytest.fixture
async def odds_client(fake_odds_api):
    async with client_mod.httpx.AsyncClient() as client:
        yield client


def _counter_value(name: str, **labels: str) -> int:
    snap = metrics.snapshot()
    for row in snap.counters.get(name, []):
        if all(row["labels"].get(k) == v for k, v in labels.items()):
            return int(row["value"])
    return 0


def _gauge(name: str) -> float | None:
    return metrics.snapshot().gauges.get(name)


class TestOddsApiRequestMetrics:
    async def test_success_records_latency_and_status(self, fake_odds_api, odds_client):
        fake_odds_api.set_bulk_odds(
            "soccer_epl",
            [
                odds_event(
                    "epl-1",
                    home="Arsenal",
                    away="Chelsea",
                    commence_time="2026-06-15T19:00:00Z",
                    quotes=h2h_quotes({"draftkings": {"Arsenal": 1.85}, "fanduel": {"Arsenal": 1.80}}),
                    sport_key="soccer_epl",
                )
            ],
        )
        await svc._fetch_bulk_h2h(odds_client, "key", "soccer_epl", "EPL")

        assert (
            _counter_value(
                "bettor_odds_api_requests_total",
                endpoint="bulk_h2h",
                market="h2h",
                sport_key="soccer_epl",
                outcome="success",
            )
            == 1
        )
        assert (
            _counter_value(
                "bettor_odds_api_http_status_total",
                endpoint="bulk_h2h",
                market="h2h",
                status_code="200",
            )
            == 1
        )
        hist = metrics.snapshot().histograms["bettor_odds_api_request_duration_seconds"]
        assert any(
            row["labels"] == {"endpoint": "bulk_h2h", "market": "h2h", "sport_key": "soccer_epl"}
            and row["count"] == 1
            for row in hist
        )
        assert _gauge("bettor_odds_api_quota_remaining") == 5000.0
        assert _gauge("bettor_odds_api_quota_used") == 100.0

    async def test_429_and_402_are_distinguishable(self, fake_odds_api, odds_client):
        fake_odds_api.set_bulk_odds("soccer_epl", Fail(429))
        await svc._fetch_bulk_h2h(odds_client, "key", "soccer_epl", "EPL")
        fake_odds_api.set_bulk_odds("soccer_epl", Fail(402))
        await svc._fetch_bulk_h2h(odds_client, "key", "soccer_epl", "EPL")

        assert (
            _counter_value(
                "bettor_odds_api_rate_limit_total",
                endpoint="bulk_h2h",
                market="h2h",
                sport_key="soccer_epl",
            )
            == 1
        )
        assert (
            _counter_value(
                "bettor_odds_api_quota_exhausted_total",
                endpoint="bulk_h2h",
                market="h2h",
                sport_key="soccer_epl",
            )
            == 1
        )
        assert (
            _counter_value(
                "bettor_odds_api_http_status_total",
                endpoint="bulk_h2h",
                market="h2h",
                status_code="429",
            )
            == 1
        )
        assert (
            _counter_value(
                "bettor_odds_api_http_status_total",
                endpoint="bulk_h2h",
                market="h2h",
                status_code="402",
            )
            == 1
        )

    async def test_timeout_is_counted(self, fake_odds_api, odds_client):
        fake_odds_api.set_event_odds(
            "basketball_nba",
            "nba-1",
            Raise(httpx.ReadTimeout("slow")),
        )
        await svc._fetch_event_props(
            odds_client,
            "key",
            "basketball_nba",
            "NBA",
            "nba-1",
            "player_points",
            {"player_points"},
        )
        # Two attempts (initial + one retry) both time out.
        assert (
            _counter_value(
                "bettor_odds_api_timeouts_total",
                endpoint="event_props",
                market="props",
                sport_key="basketball_nba",
            )
            == 2
        )

    async def test_metrics_never_include_api_key(self, fake_odds_api, odds_client):
        fake_odds_api.set_bulk_odds("soccer_epl", [])
        await svc._fetch_bulk_h2h(odds_client, "super-secret-key", "soccer_epl", "EPL")
        text = metrics.render_prometheus()
        blob = str(metrics.snapshot_dict())
        assert "super-secret-key" not in text
        assert "super-secret-key" not in blob
        assert "apiKey" not in text


class TestCacheAndPipelineMetrics:
    async def test_cache_hit_miss_bypass(self, monkeypatch):
        calls = {"n": 0}

        async def factory():
            calls["n"] += 1
            return ("ok", None)

        monkeypatch.setenv("PICKS_CACHE_TTL_SECONDS", "60")
        _, hit1 = await cached_fetch("k1", factory)
        _, hit2 = await cached_fetch("k1", factory)
        assert hit1 is False and hit2 is True
        assert _counter_value("bettor_picks_cache_requests_total", result="miss") == 1
        assert _counter_value("bettor_picks_cache_requests_total", result="hit") == 1

        monkeypatch.setenv("PICKS_CACHE_TTL_SECONDS", "0")
        await cached_fetch("k2", factory)
        assert _counter_value("bettor_picks_cache_requests_total", result="bypass") == 1

    async def test_warning_and_fallback_frequency(self, fake_odds_api, monkeypatch):
        freeze_time(monkeypatch, NOW)
        monkeypatch.setenv("THE_ODDS_API_KEY", "live-key")
        monkeypatch.setenv("MAX_PROP_EVENTS_PER_SPORT", "0")
        for sk in svc.SOCCER_SPORT_KEYS:
            fake_odds_api.set_bulk_odds(sk, Fail(429))
        fake_odds_api.set_events("basketball_nba", [])
        fake_odds_api.set_events("americanfootball_nfl", [])

        rows, source, _, _, warn = await svc.fetch_best_picks(
            "live-key",
            for_day=NOW.date(),
            timezone_name="America/New_York",
        )
        assert source == "live"
        assert warn is not None
        assert "429" in warn
        assert rows == []
        assert (
            _counter_value(
                "bettor_odds_api_warnings_total",
                kind="rate_limit",
                partial="false",
            )
            == 1
        )


class TestMetricsEndpoint:
    @pytest.fixture(autouse=True)
    def metrics_admin_token(self, monkeypatch: pytest.MonkeyPatch) -> str:
        token = "test-metrics-admin-token"
        monkeypatch.setenv("METRICS_ADMIN_TOKEN", token)
        return token

    def test_prometheus_export(self, client, metrics_admin_token):
        metrics.record_odds_api_request(
            endpoint="bulk_h2h",
            market="h2h",
            sport_key="soccer_epl",
            outcome="success",
            duration_seconds=0.12,
            status_code=200,
        )
        response = client.get(
            "/api/metrics",
            headers={"Authorization": f"Bearer {metrics_admin_token}"},
        )
        assert response.status_code == 200
        assert "text/plain" in response.headers["content-type"]
        body = response.text
        assert "bettor_odds_api_requests_total" in body
        assert 'outcome="success"' in body
        assert "bettor_uptime_seconds" in body

    def test_json_export(self, client, metrics_admin_token):
        metrics.record_cache_result("hit")
        response = client.get(
            "/api/metrics",
            params={"format": "json"},
            headers={"X-Metrics-Token": metrics_admin_token},
        )
        assert response.status_code == 200
        data = response.json()
        assert "counters" in data
        assert "gauges" in data
        assert "histograms" in data

    def test_rejects_missing_credentials(self, client):
        response = client.get("/api/metrics")
        assert response.status_code == 401

    def test_rejects_wrong_token(self, client):
        response = client.get(
            "/api/metrics",
            headers={"Authorization": "Bearer wrong-token"},
        )
        assert response.status_code == 401

    def test_tokens_match_treats_non_ascii_as_mismatch(self, metrics_admin_token):
        from app.api.metrics import _tokens_match

        # Same length, non-ASCII — compare_digest would raise TypeError without the guard.
        assert _tokens_match("é" * len(metrics_admin_token), metrics_admin_token) is False

    def test_unavailable_when_token_not_configured(self, client, monkeypatch):
        monkeypatch.delenv("METRICS_ADMIN_TOKEN", raising=False)
        response = client.get(
            "/api/metrics",
            headers={"Authorization": "Bearer anything"},
        )
        assert response.status_code == 503

    def test_http_latency_recorded_separately_from_metrics_path(self, client, metrics_admin_token):
        # Hitting /api/health should record HTTP metrics; /api/metrics should not.
        assert client.get("/api/health").status_code == 200
        before = _counter_value("bettor_http_requests_total", method="GET", path="/api/health", status_code="200")
        assert before >= 1
        client.get(
            "/api/metrics",
            headers={"Authorization": f"Bearer {metrics_admin_token}"},
        )
        assert (
            _counter_value(
                "bettor_http_requests_total",
                method="GET",
                path="/api/metrics",
                status_code="200",
            )
            == 0
        )
        assert "bettor_http_request_duration_seconds" in metrics.render_prometheus()
