"""API integration tests for Odds API failure classes and fallback paths (BET-10).

Exercises the boundary between the odds service and the HTTP API with the upstream
mocked at the httpx transport. These paths are high-risk: rate limits, quota
exhaustion, malformed payloads, and demo/live source selection must degrade
gracefully without contacting the real Odds API.
"""

from __future__ import annotations

from datetime import datetime, timezone

import httpx
import pytest

from services.odds import service as svc
from tests.helpers import Fail, Raise, Seq, freeze_time, h2h_quotes, odds_event, quote

NOW = datetime(2026, 6, 15, 18, 0, tzinfo=timezone.utc)
TODAY = "2026-06-15"
TZ = "America/New_York"


@pytest.fixture
def frozen(monkeypatch):
    return freeze_time(monkeypatch, NOW)


def _epl_event(event_id="epl-1", commence_time="2026-06-15T22:00:00Z"):
    return odds_event(
        event_id,
        home="Arsenal",
        away="Chelsea",
        commence_time=commence_time,
        quotes=h2h_quotes({"draftkings": {"Arsenal": 1.85}, "fanduel": {"Arsenal": 1.80}}),
        sport_key="soccer_epl",
    )


def _nba_event(event_id="nba-1", commence_time="2026-06-15T23:00:00Z"):
    quotes = []
    for index, market in enumerate(
        ["player_points", "player_assists", "player_rebounds", "player_threes"]
    ):
        quotes.append(
            quote(
                "draftkings",
                market,
                "Over",
                1.85 - index / 100,
                description="Jayson Tatum",
                point=10.5,
            )
        )
        quotes.append(
            quote(
                "fanduel",
                market,
                "Over",
                1.80 - index / 100,
                description="Jayson Tatum",
                point=10.5,
            )
        )
    return odds_event(event_id, commence_time=commence_time, quotes=quotes)


def _live(monkeypatch) -> None:
    monkeypatch.setenv("THE_ODDS_API_KEY", "live-key")


# --------------------------------------------------------------------------------------
# Successful upstream / empty slate
# --------------------------------------------------------------------------------------


class TestSuccessfulAndEmptyUpstream:
    def test_successful_upstream_returns_a_live_slate(
        self, client, frozen, fake_odds_api, monkeypatch
    ):
        _live(monkeypatch)
        fake_odds_api.set_bulk_odds("soccer_epl", [_epl_event()])

        response = client.get("/api/picks", params={"date": TODAY, "timezone": TZ})
        assert response.status_code == 200
        body = response.json()
        assert body["source"] == "live"
        assert body["odds_api_warning"] is None
        assert body["used_relaxed_implied_fallback"] is False
        assert any(g["event_id"] == "epl-1" for g in body["games"])
        assert body["pick_count"] > 0

    def test_empty_event_list_is_a_valid_empty_live_slate(
        self, client, frozen, fake_odds_api, monkeypatch
    ):
        _live(monkeypatch)
        # Defaults are empty lists for every league.
        response = client.get("/api/picks", params={"date": TODAY, "timezone": TZ})
        assert response.status_code == 200
        body = response.json()
        assert body["source"] == "live"
        assert body["games"] == []
        assert body["game_count"] == 0
        assert body["pick_count"] == 0
        assert body["odds_api_warning"] is None


# --------------------------------------------------------------------------------------
# Malformed / incomplete payloads
# --------------------------------------------------------------------------------------


class TestMalformedPayloads:
    @pytest.mark.parametrize(
        "payload",
        [
            {"message": "unexpected object"},
            [None],
            [{"id": None, "bookmakers": None}],
            [{"id": "x", "commence_time": 1781000000, "bookmakers": []}],
            [{"id": "x", "commence_time": "2026-06-15T22:00:00Z", "bookmakers": [{}]}],
        ],
    )
    def test_malformed_league_payload_does_not_500(
        self, client, frozen, fake_odds_api, monkeypatch, payload
    ):
        _live(monkeypatch)
        fake_odds_api.set_bulk_odds("soccer_epl", payload)
        fake_odds_api.set_bulk_odds("soccer_italy_serie_a", [_epl_event("serie-1")])

        response = client.get("/api/picks", params={"date": TODAY, "timezone": TZ})
        assert response.status_code == 200
        body = response.json()
        assert body["source"] == "live"
        assert "serie-1" in {g["event_id"] for g in body["games"]}

    def test_undecodable_html_body_does_not_500(
        self, client, frozen, fake_odds_api, monkeypatch
    ):
        _live(monkeypatch)
        fake_odds_api.set_bulk_odds(
            "soccer_epl", httpx.Response(200, text="<html>502 Bad Gateway</html>")
        )
        fake_odds_api.set_bulk_odds("soccer_italy_serie_a", [_epl_event("serie-1")])

        response = client.get("/api/picks", params={"date": TODAY, "timezone": TZ})
        assert response.status_code == 200
        body = response.json()
        assert body["source"] == "live"
        assert [g["event_id"] for g in body["games"]] == ["serie-1"]
        assert body["odds_api_warning"] is None

    def test_malformed_single_game_payload_returns_null_game(
        self, client, frozen, fake_odds_api, monkeypatch
    ):
        _live(monkeypatch)
        fake_odds_api.set_event_odds(
            "basketball_nba", "nba-1", {"id": "nba-1", "bookmakers": None}
        )

        response = client.get(
            "/api/picks/game",
            params={
                "sport_key": "basketball_nba",
                "event_id": "nba-1",
                "date": TODAY,
                "timezone": TZ,
            },
        )
        assert response.status_code == 200
        body = response.json()
        assert body["source"] == "live"
        assert body["game"] is None
        assert body["pick_count"] == 0
        assert body["odds_api_warning"] is None


# --------------------------------------------------------------------------------------
# HTTP 429 / 402 and warning propagation
# --------------------------------------------------------------------------------------


class TestQuotaAndRateLimitWarnings:
    def test_full_slate_rate_limit_warns_and_stays_live(
        self, client, frozen, fake_odds_api, monkeypatch
    ):
        _live(monkeypatch)
        fake_odds_api.fail_everything(Fail(429))

        response = client.get("/api/picks", params={"date": TODAY, "timezone": TZ})
        assert response.status_code == 200
        body = response.json()
        assert body["source"] == "live"
        assert body["games"] == []
        assert "429" in body["odds_api_warning"]
        assert "part of the slate may be" not in body["odds_api_warning"]

    def test_full_slate_quota_exhaustion_warns_and_stays_live(
        self, client, frozen, fake_odds_api, monkeypatch
    ):
        _live(monkeypatch)
        fake_odds_api.fail_everything(Fail(402))

        response = client.get("/api/picks", params={"date": TODAY, "timezone": TZ})
        assert response.status_code == 200
        body = response.json()
        assert body["source"] == "live"
        assert body["games"] == []
        assert "402" in body["odds_api_warning"]

    def test_partial_rate_limit_returns_working_leagues_with_softened_warning(
        self, client, frozen, fake_odds_api, monkeypatch
    ):
        _live(monkeypatch)
        fake_odds_api.set_bulk_odds("soccer_epl", [_epl_event()])
        fake_odds_api.set_bulk_odds("soccer_spain_la_liga", Fail(429))

        response = client.get("/api/picks", params={"date": TODAY, "timezone": TZ})
        assert response.status_code == 200
        body = response.json()
        assert body["source"] == "live"
        assert [g["event_id"] for g in body["games"]] == ["epl-1"]
        assert "part of the slate may be" in body["odds_api_warning"]
        assert "429" in body["odds_api_warning"]

    def test_partial_quota_exhaustion_softens_the_warning(
        self, client, frozen, fake_odds_api, monkeypatch
    ):
        _live(monkeypatch)
        fake_odds_api.set_bulk_odds("soccer_epl", [_epl_event()])
        fake_odds_api.set_bulk_odds("soccer_spain_la_liga", Fail(402))

        response = client.get("/api/picks", params={"date": TODAY, "timezone": TZ})
        assert response.status_code == 200
        body = response.json()
        assert body["source"] == "live"
        assert [g["event_id"] for g in body["games"]] == ["epl-1"]
        assert "402" in body["odds_api_warning"]
        assert "incomplete" in body["odds_api_warning"]

    def test_game_refresh_rate_limit_propagates_warning(
        self, client, frozen, fake_odds_api, monkeypatch
    ):
        _live(monkeypatch)
        fake_odds_api.fail_everything(Fail(429))

        response = client.get(
            "/api/picks/game",
            params={
                "sport_key": "basketball_nba",
                "event_id": "nba-1",
                "date": TODAY,
                "timezone": TZ,
            },
        )
        assert response.status_code == 200
        body = response.json()
        assert body["source"] == "live"
        assert body["game"] is None
        assert "429" in body["odds_api_warning"]

    def test_game_refresh_quota_exhaustion_propagates_warning(
        self, client, frozen, fake_odds_api, monkeypatch
    ):
        _live(monkeypatch)
        fake_odds_api.fail_everything(Fail(402))

        response = client.get(
            "/api/picks/game",
            params={
                "sport_key": "soccer_epl",
                "event_id": "epl-1",
                "date": TODAY,
                "timezone": TZ,
            },
        )
        assert response.status_code == 200
        body = response.json()
        assert body["source"] == "live"
        assert body["game"] is None
        assert "402" in body["odds_api_warning"]

    def test_game_refresh_retry_after_429_still_warns_but_returns_picks(
        self, client, frozen, fake_odds_api, monkeypatch
    ):
        """Prop fetches retry once on 429; a later success still records the rate-limit event."""
        _live(monkeypatch)
        fake_odds_api.set_event_odds(
            "basketball_nba", "nba-1", Seq([Fail(429), _nba_event()])
        )

        response = client.get(
            "/api/picks/game",
            params={
                "sport_key": "basketball_nba",
                "event_id": "nba-1",
                "date": TODAY,
                "timezone": TZ,
            },
        )
        assert response.status_code == 200
        body = response.json()
        assert body["source"] == "live"
        assert body["game"] is not None
        assert body["pick_count"] > 0
        assert "part of the slate may be" in body["odds_api_warning"]

    def test_warned_responses_are_not_cached(
        self, client, frozen, fake_odds_api, monkeypatch
    ):
        _live(monkeypatch)
        fake_odds_api.fail_everything(Fail(429))
        params = {"date": TODAY, "timezone": TZ}

        assert client.get("/api/picks", params=params).headers["X-Picks-Cache"] == "miss"
        assert client.get("/api/picks", params=params).headers["X-Picks-Cache"] == "miss"


# --------------------------------------------------------------------------------------
# Timeout / network failure
# --------------------------------------------------------------------------------------


class TestTimeoutAndNetworkFailure:
    def test_connect_error_returns_empty_live_slate_without_warning(
        self, client, frozen, fake_odds_api, monkeypatch
    ):
        _live(monkeypatch)
        fake_odds_api.fail_everything(Raise(httpx.ConnectError("dns")))

        response = client.get("/api/picks", params={"date": TODAY, "timezone": TZ})
        assert response.status_code == 200
        body = response.json()
        assert body["source"] == "live"
        assert body["games"] == []
        assert body["odds_api_warning"] is None

    def test_read_timeout_returns_empty_live_slate_without_warning(
        self, client, frozen, fake_odds_api, monkeypatch
    ):
        _live(monkeypatch)
        fake_odds_api.fail_everything(Raise(httpx.ReadTimeout("slow")))

        response = client.get("/api/picks", params={"date": TODAY, "timezone": TZ})
        assert response.status_code == 200
        body = response.json()
        assert body["source"] == "live"
        assert body["games"] == []
        assert body["odds_api_warning"] is None

    def test_game_refresh_timeout_returns_null_game(
        self, client, frozen, fake_odds_api, monkeypatch
    ):
        _live(monkeypatch)
        fake_odds_api.fail_everything(Raise(httpx.ReadTimeout("slow")))

        response = client.get(
            "/api/picks/game",
            params={
                "sport_key": "basketball_nba",
                "event_id": "nba-1",
                "date": TODAY,
                "timezone": TZ,
            },
        )
        assert response.status_code == 200
        body = response.json()
        assert body["source"] == "live"
        assert body["game"] is None
        assert body["odds_api_warning"] is None

    def test_server_errors_do_not_produce_a_quota_warning(
        self, client, frozen, fake_odds_api, monkeypatch
    ):
        _live(monkeypatch)
        fake_odds_api.fail_everything(Fail(500))

        response = client.get("/api/picks", params={"date": TODAY, "timezone": TZ})
        assert response.status_code == 200
        body = response.json()
        assert body["source"] == "live"
        assert body["games"] == []
        assert body["odds_api_warning"] is None


# --------------------------------------------------------------------------------------
# Partial sportsbook / market data
# --------------------------------------------------------------------------------------


class TestPartialBookAndMarketData:
    def test_a_single_allowed_book_cannot_rank_picks_but_stays_200(
        self, client, frozen, fake_odds_api, monkeypatch
    ):
        """Line-shopping edge requires 2+ books; one book yields an empty pick list, not an error."""
        _live(monkeypatch)
        event = odds_event(
            "epl-1",
            home="Arsenal",
            away="Chelsea",
            commence_time="2026-06-15T22:00:00Z",
            quotes=h2h_quotes({"draftkings": {"Arsenal": 1.85}}),
            sport_key="soccer_epl",
        )
        fake_odds_api.set_bulk_odds("soccer_epl", [event])

        response = client.get("/api/picks", params={"date": TODAY, "timezone": TZ})
        assert response.status_code == 200
        body = response.json()
        assert body["source"] == "live"
        assert body["odds_api_warning"] is None
        game = next(g for g in body["games"] if g["event_id"] == "epl-1")
        assert game["picks"] == []
        assert body["pick_count"] == 0

    def test_disallowed_books_are_ignored_without_failing(
        self, client, frozen, fake_odds_api, monkeypatch
    ):
        _live(monkeypatch)
        event = odds_event(
            "epl-1",
            home="Arsenal",
            away="Chelsea",
            commence_time="2026-06-15T22:00:00Z",
            quotes=h2h_quotes(
                {
                    "bet365": {"Arsenal": 1.50},
                    "draftkings": {"Arsenal": 1.85},
                    "fanduel": {"Arsenal": 1.80},
                }
            ),
            sport_key="soccer_epl",
        )
        fake_odds_api.set_bulk_odds("soccer_epl", [event])

        response = client.get("/api/picks", params={"date": TODAY, "timezone": TZ})
        assert response.status_code == 200
        body = response.json()
        game = next(g for g in body["games"] if g["event_id"] == "epl-1")
        books = {p["best_book"].lower() for p in game["picks"]}
        assert "bet365" not in books
        assert game["picks"]

    def test_one_book_missing_an_outcome_still_ranks_overlapping_lines(
        self, client, frozen, fake_odds_api, monkeypatch
    ):
        """Partial sportsbook coverage: only outcomes posted by 2+ allowed books become picks."""
        _live(monkeypatch)
        event = odds_event(
            "epl-1",
            home="Arsenal",
            away="Chelsea",
            commence_time="2026-06-15T22:00:00Z",
            quotes=h2h_quotes(
                {
                    "draftkings": {"Arsenal": 1.85, "Chelsea": 4.20},
                    "fanduel": {"Arsenal": 1.80},  # Chelsea missing at FanDuel
                }
            ),
            sport_key="soccer_epl",
        )
        fake_odds_api.set_bulk_odds("soccer_epl", [event])

        response = client.get("/api/picks", params={"date": TODAY, "timezone": TZ})
        assert response.status_code == 200
        body = response.json()
        assert body["source"] == "live"
        game = next(g for g in body["games"] if g["event_id"] == "epl-1")
        pick_names = {p["pick"] for p in game["picks"]}
        assert "Arsenal" in pick_names
        assert "Chelsea" not in pick_names

    def test_junk_markets_alongside_usable_h2h_do_not_break_the_slate(
        self, client, frozen, fake_odds_api, monkeypatch
    ):
        _live(monkeypatch)
        event = _epl_event()
        event["bookmakers"][0]["markets"].append(
            {"key": "unknown_market", "outcomes": [{"name": "Yes", "price": 1.01}]}
        )
        event["bookmakers"][0]["markets"].append(
            {"key": "h2h", "outcomes": [{"name": "Arsenal"}]}  # missing price
        )
        fake_odds_api.set_bulk_odds("soccer_epl", [event])

        response = client.get("/api/picks", params={"date": TODAY, "timezone": TZ})
        assert response.status_code == 200
        body = response.json()
        assert body["source"] == "live"
        assert any(g["event_id"] == "epl-1" and g["picks"] for g in body["games"])


# --------------------------------------------------------------------------------------
# Demo fallback and live↔demo transitions
# --------------------------------------------------------------------------------------


class TestDemoAndLiveTransitions:
    def test_missing_api_key_serves_demo_without_warning(self, client, frozen):
        response = client.get("/api/picks", params={"date": TODAY, "timezone": TZ})
        assert response.status_code == 200
        body = response.json()
        assert body["source"] == "demo"
        assert body["games"]
        assert body["odds_api_warning"] is None

    def test_blank_api_key_serves_demo(self, client, frozen, monkeypatch):
        monkeypatch.setenv("THE_ODDS_API_KEY", "   ")
        body = client.get("/api/picks", params={"date": TODAY, "timezone": TZ}).json()
        assert body["source"] == "demo"
        assert body["odds_api_warning"] is None

    def test_live_upstream_failure_does_not_fall_back_to_demo(
        self, client, frozen, fake_odds_api, monkeypatch
    ):
        """With a key configured, empty/failed upstream stays `live` — never silently demo."""
        _live(monkeypatch)
        fake_odds_api.fail_everything(Fail(429))

        body = client.get("/api/picks", params={"date": TODAY, "timezone": TZ}).json()
        assert body["source"] == "live"
        assert body["games"] == []
        assert body["odds_api_warning"] is not None

    def test_switching_from_demo_to_live_misses_cache_and_uses_upstream(
        self, client, frozen, fake_odds_api, monkeypatch
    ):
        params = {"date": TODAY, "timezone": TZ}
        demo = client.get("/api/picks", params=params)
        assert demo.json()["source"] == "demo"

        _live(monkeypatch)
        fake_odds_api.set_bulk_odds("soccer_epl", [_epl_event()])
        live = client.get("/api/picks", params=params)
        assert live.headers["X-Picks-Cache"] == "miss"
        assert live.json()["source"] == "live"
        assert any(g["event_id"] == "epl-1" for g in live.json()["games"])

    def test_demo_single_game_refresh_does_not_call_upstream(
        self, client, frozen, fake_odds_api
    ):
        response = client.get(
            "/api/picks/game",
            params={
                "sport_key": "basketball_nba",
                "event_id": "demo-nba",
                "date": TODAY,
                "timezone": TZ,
            },
        )
        assert response.status_code == 200
        body = response.json()
        assert body["source"] == "demo"
        assert body["game"] is not None
        assert fake_odds_api.requests == []


# --------------------------------------------------------------------------------------
# Ranking floor fallback (relaxed implied probability)
# --------------------------------------------------------------------------------------


class TestRelaxedImpliedFallback:
    def test_marginal_prices_flip_the_relaxed_fallback_flag(
        self, client, frozen, fake_odds_api, monkeypatch
    ):
        _live(monkeypatch)
        marginal = odds_event(
            "epl-1",
            home="Arsenal",
            away="Chelsea",
            commence_time="2026-06-15T22:00:00Z",
            quotes=h2h_quotes({"draftkings": {"Arsenal": 1.99}, "fanduel": {"Arsenal": 1.96}}),
            sport_key="soccer_epl",
        )
        fake_odds_api.set_bulk_odds("soccer_epl", [marginal])

        response = client.get("/api/picks", params={"date": TODAY, "timezone": TZ})
        assert response.status_code == 200
        body = response.json()
        assert body["source"] == "live"
        assert body["used_relaxed_implied_fallback"] is True
        assert body["min_implied_probability"] == svc.RELAXED_IMPLIED_PROBABILITY
        assert body["games"][0]["picks"]

    def test_prices_above_the_primary_floor_do_not_use_relaxed(
        self, client, frozen, fake_odds_api, monkeypatch
    ):
        _live(monkeypatch)
        fake_odds_api.set_bulk_odds("soccer_epl", [_epl_event()])

        body = client.get("/api/picks", params={"date": TODAY, "timezone": TZ}).json()
        assert body["used_relaxed_implied_fallback"] is False
        assert body["min_implied_probability"] == svc.MIN_IMPLIED_PROBABILITY
