"""HTTP integration tests for the JSON API, with the Odds API mocked at the transport boundary."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone

import httpx
import pytest

from app.api import picks as api_picks
from app.core.calendar import PICKS_FUTURE_END_OFFSET
from services.odds import service as svc
from tests.helpers import Fail, Raise, freeze_time, h2h_quotes, odds_event, quote

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
    for index, market in enumerate(["player_points", "player_assists", "player_rebounds", "player_threes"]):
        quotes.append(
            quote("draftkings", market, "Over", 1.85 - index / 100, description="Jayson Tatum", point=10.5)
        )
        quotes.append(
            quote("fanduel", market, "Over", 1.80 - index / 100, description="Jayson Tatum", point=10.5)
        )
    return odds_event(event_id, commence_time=commence_time, quotes=quotes)


class TestResponseCaps:
    """`picks_per_game` is enforced again on the way out, guarding cache/merge edge cases."""

    def test_a_single_game_is_truncated_to_the_requested_size(self):
        game = {"picks": [1, 2, 3, 4, 5]}
        api_picks._cap_picks(game, 2)
        assert game["picks"] == [1, 2]

    def test_a_game_within_the_limit_is_untouched(self):
        game = {"picks": [1]}
        api_picks._cap_picks(game, 3)
        assert game["picks"] == [1]

    @pytest.mark.parametrize("game", [None, {}, {"picks": None}])
    def test_missing_games_and_pick_lists_are_tolerated(self, game):
        api_picks._cap_picks(game, 3)

    def test_every_game_in_a_slate_is_truncated(self):
        games = [{"picks": [1, 2, 3]}, {"picks": [1]}, {"picks": None}]
        api_picks._cap_slate_games(games, 2)
        assert [g["picks"] for g in games] == [[1, 2], [1], None]


class TestHealth:
    def test_reports_demo_mode_when_no_key_is_configured(self, client):
        response = client.get("/api/health")
        assert response.status_code == 200
        assert response.json() == {"ok": True, "live_odds": False}

    def test_reports_live_mode_when_a_key_is_configured(self, client, monkeypatch):
        monkeypatch.setenv("THE_ODDS_API_KEY", "live-key")
        assert client.get("/api/health").json() == {"ok": True, "live_odds": True}

    def test_a_commented_out_key_is_still_demo_mode(self, client, monkeypatch):
        monkeypatch.setenv("THE_ODDS_API_KEY", "# THE_ODDS_API_KEY=abc")
        assert client.get("/api/health").json()["live_odds"] is False


class TestPicksSlateContract:
    def test_demo_slate_returns_the_documented_envelope(self, client, frozen):
        response = client.get("/api/picks", params={"date": TODAY, "timezone": TZ})
        assert response.status_code == 200
        body = response.json()
        assert set(body) == {
            "source",
            "game_count",
            "pick_count",
            "picks_per_game",
            "game_date",
            "timezone",
            "min_implied_probability",
            "target_implied_probability",
            "relaxed_implied_probability",
            "used_relaxed_implied_fallback",
            "odds_api_warning",
            "games",
        }
        assert body["source"] == "demo"
        assert body["game_date"] == TODAY
        assert body["timezone"] == TZ
        assert body["odds_api_warning"] is None

    def test_counts_match_the_returned_games(self, client, frozen):
        body = client.get("/api/picks", params={"date": TODAY, "timezone": TZ}).json()
        assert body["game_count"] == len(body["games"])
        assert body["pick_count"] == sum(len(g["picks"]) for g in body["games"])

    def test_thresholds_are_echoed_for_the_ui(self, client, frozen):
        body = client.get("/api/picks", params={"date": TODAY, "timezone": TZ}).json()
        assert body["target_implied_probability"] == svc.MIN_IMPLIED_PROBABILITY
        assert body["relaxed_implied_probability"] == svc.RELAXED_IMPLIED_PROBABILITY
        assert body["min_implied_probability"] in {
            svc.MIN_IMPLIED_PROBABILITY,
            svc.RELAXED_IMPLIED_PROBABILITY,
        }

    def test_responses_are_never_stored_by_browsers(self, client, frozen):
        response = client.get("/api/picks", params={"date": TODAY, "timezone": TZ})
        assert response.headers["Cache-Control"] == "no-store, must-revalidate"

    def test_defaults_to_today_in_the_request_timezone(self, client, frozen):
        body = client.get("/api/picks", params={"timezone": TZ}).json()
        assert body["game_date"] == TODAY

    def test_default_timezone_is_eastern(self, client, frozen):
        assert client.get("/api/picks").json()["timezone"] == "America/New_York"

    def test_each_game_exposes_the_frontend_fields(self, client, frozen):
        body = client.get("/api/picks", params={"date": TODAY, "timezone": TZ}).json()
        game = next(g for g in body["games"] if g["picks"])
        assert set(game) >= {
            "sport_key",
            "sport_title",
            "event_id",
            "home_team",
            "away_team",
            "commence_time",
            "matchup",
            "picks",
        }
        assert {"pick", "implied_pct", "rank_score", "best_book", "edge_pct"} <= set(game["picks"][0])


class TestPicksValidation:
    @pytest.mark.parametrize("bad_date", ["not-a-date", "2026-13-01", "06/15/2026", "2026-06"])
    def test_malformed_dates_are_rejected(self, client, frozen, bad_date):
        response = client.get("/api/picks", params={"date": bad_date, "timezone": TZ})
        assert response.status_code == 400
        assert response.json()["detail"] == "Invalid date=YYYY-MM-DD"

    def test_blank_date_falls_back_to_the_default_day(self, client, frozen):
        body = client.get("/api/picks", params={"date": "  ", "timezone": TZ}).json()
        assert body["game_date"] == TODAY

    def test_yesterday_is_outside_the_pickable_window(self, client, frozen):
        response = client.get("/api/picks", params={"date": "2026-06-14", "timezone": TZ})
        assert response.status_code == 400
        assert "Game day must be from" in response.json()["detail"]

    def test_the_last_pickable_day_is_accepted(self, client, frozen):
        last = date(2026, 6, 15) + timedelta(days=PICKS_FUTURE_END_OFFSET)
        response = client.get("/api/picks", params={"date": last.isoformat(), "timezone": TZ})
        assert response.status_code == 200
        assert response.json()["game_date"] == last.isoformat()

    def test_one_day_past_the_window_is_rejected(self, client, frozen):
        too_far = date(2026, 6, 15) + timedelta(days=PICKS_FUTURE_END_OFFSET + 1)
        response = client.get("/api/picks", params={"date": too_far.isoformat(), "timezone": TZ})
        assert response.status_code == 400

    @pytest.mark.parametrize("bad_max", [0, -1, 201, 5000])
    def test_out_of_range_max_games_is_rejected(self, client, frozen, bad_max):
        response = client.get(
            "/api/picks", params={"date": TODAY, "timezone": TZ, "max_games": bad_max}
        )
        assert response.status_code == 400
        assert response.json()["detail"] == "max_games must be 1–200 or omitted"

    def test_max_games_limits_the_slate(self, client, frozen):
        body = client.get(
            "/api/picks", params={"date": TODAY, "timezone": TZ, "max_games": 1}
        ).json()
        assert body["game_count"] == 1

    @pytest.mark.parametrize("bad_ppg", [0, -3, 26])
    def test_out_of_range_picks_per_game_is_rejected(self, client, frozen, bad_ppg):
        response = client.get(
            "/api/picks", params={"date": TODAY, "timezone": TZ, "picks_per_game": bad_ppg}
        )
        assert response.status_code == 422

    def test_picks_per_game_caps_every_game(self, client, frozen):
        body = client.get(
            "/api/picks", params={"date": TODAY, "timezone": TZ, "picks_per_game": 1}
        ).json()
        assert body["picks_per_game"] == 1
        assert all(len(g["picks"]) <= 1 for g in body["games"])

    def test_an_unknown_timezone_is_served_as_utc(self, client, frozen):
        response = client.get("/api/picks", params={"timezone": "Mars/Olympus"})
        assert response.status_code == 200
        body = response.json()
        assert body["timezone"] == "Mars/Olympus"
        assert body["game_date"] == TODAY


class TestPicksSlateCaching:
    def test_first_request_misses_and_second_hits(self, client, frozen):
        params = {"date": TODAY, "timezone": TZ}
        first = client.get("/api/picks", params=params)
        second = client.get("/api/picks", params=params)
        assert first.headers["X-Picks-Cache"] == "miss"
        assert second.headers["X-Picks-Cache"] == "hit"
        assert first.json()["games"] == second.json()["games"]

    def test_a_cached_slate_avoids_further_upstream_calls(self, client, frozen, fake_odds_api, monkeypatch):
        monkeypatch.setenv("THE_ODDS_API_KEY", "live-key")
        fake_odds_api.set_bulk_odds("soccer_epl", [_epl_event()])
        params = {"date": TODAY, "timezone": TZ}

        client.get("/api/picks", params=params)
        upstream_calls = len(fake_odds_api.requests)
        assert upstream_calls > 0

        assert client.get("/api/picks", params=params).headers["X-Picks-Cache"] == "hit"
        assert len(fake_odds_api.requests) == upstream_calls

    def test_disabling_the_ttl_bypasses_the_cache(self, client, frozen, monkeypatch):
        monkeypatch.setenv("PICKS_CACHE_TTL_SECONDS", "0")
        params = {"date": TODAY, "timezone": TZ}
        assert client.get("/api/picks", params=params).headers["X-Picks-Cache"] == "bypass"
        assert client.get("/api/picks", params=params).headers["X-Picks-Cache"] == "bypass"

    @pytest.mark.parametrize(
        "changed",
        [
            {"date": "2026-06-16"},
            {"timezone": "UTC"},
            {"picks_per_game": 2},
            {"max_games": 1},
        ],
    )
    def test_request_parameters_are_part_of_the_cache_key(self, client, frozen, changed):
        base = {"date": TODAY, "timezone": TZ}
        client.get("/api/picks", params=base)
        response = client.get("/api/picks", params={**base, **changed})
        assert response.headers["X-Picks-Cache"] == "miss"

    def test_switching_to_a_live_key_does_not_serve_the_demo_slate(
        self, client, frozen, fake_odds_api, monkeypatch
    ):
        params = {"date": TODAY, "timezone": TZ}
        assert client.get("/api/picks", params=params).json()["source"] == "demo"

        monkeypatch.setenv("THE_ODDS_API_KEY", "live-key")
        fake_odds_api.set_bulk_odds("soccer_epl", [_epl_event()])
        response = client.get("/api/picks", params=params)
        assert response.headers["X-Picks-Cache"] == "miss"
        assert response.json()["source"] == "live"

    def test_concurrent_identical_requests_fetch_upstream_once(
        self, client, frozen, fake_odds_api, monkeypatch
    ):
        """Two browsers loading the same slate should not double the Odds API spend."""
        monkeypatch.setenv("THE_ODDS_API_KEY", "live-key")
        fake_odds_api.set_bulk_odds("soccer_epl", [_epl_event()])
        params = {"date": TODAY, "timezone": TZ}

        with ThreadPoolExecutor(max_workers=4) as pool:
            responses = list(
                pool.map(lambda _: client.get("/api/picks", params=params), range(4))
            )

        assert {r.status_code for r in responses} == {200}
        cache_states = sorted(r.headers["X-Picks-Cache"] for r in responses)
        assert cache_states.count("miss") == 1
        assert cache_states.count("hit") == 3
        assert fake_odds_api.count("/sports/soccer_epl/odds") == 1


class TestPicksSlateLive:
    def test_live_slate_is_built_from_the_mocked_upstream(
        self, client, frozen, fake_odds_api, monkeypatch
    ):
        monkeypatch.setenv("THE_ODDS_API_KEY", "live-key")
        fake_odds_api.set_bulk_odds("soccer_epl", [_epl_event()])
        fake_odds_api.set_events(
            "basketball_nba", [odds_event("nba-1", commence_time="2026-06-15T23:00:00Z")]
        )
        fake_odds_api.set_event_odds("basketball_nba", "nba-1", _nba_event())

        body = client.get("/api/picks", params={"date": TODAY, "timezone": TZ}).json()
        assert body["source"] == "live"
        assert {g["event_id"] for g in body["games"]} == {"epl-1", "nba-1"}
        assert body["pick_count"] > 0

    def test_an_empty_upstream_slate_is_a_valid_empty_response(
        self, client, frozen, fake_odds_api, monkeypatch
    ):
        monkeypatch.setenv("THE_ODDS_API_KEY", "live-key")
        body = client.get("/api/picks", params={"date": TODAY, "timezone": TZ}).json()
        assert body["source"] == "live"
        assert body["games"] == []
        assert body["game_count"] == 0
        assert body["odds_api_warning"] is None

    def test_rate_limits_surface_as_a_warning_not_an_error(
        self, client, frozen, fake_odds_api, monkeypatch
    ):
        monkeypatch.setenv("THE_ODDS_API_KEY", "live-key")
        fake_odds_api.fail_everything(Fail(429))
        response = client.get("/api/picks", params={"date": TODAY, "timezone": TZ})
        assert response.status_code == 200
        assert "429" in response.json()["odds_api_warning"]

    def test_quota_errors_surface_as_a_warning_not_an_error(
        self, client, frozen, fake_odds_api, monkeypatch
    ):
        monkeypatch.setenv("THE_ODDS_API_KEY", "live-key")
        fake_odds_api.fail_everything(Fail(402))
        response = client.get("/api/picks", params={"date": TODAY, "timezone": TZ})
        assert response.status_code == 200
        assert "402" in response.json()["odds_api_warning"]

    def test_a_warned_slate_is_not_cached(self, client, frozen, fake_odds_api, monkeypatch):
        """A reload after a bad fan-out must be able to get a clean slate."""
        monkeypatch.setenv("THE_ODDS_API_KEY", "live-key")
        fake_odds_api.fail_everything(Fail(429))
        params = {"date": TODAY, "timezone": TZ}
        assert client.get("/api/picks", params=params).headers["X-Picks-Cache"] == "miss"
        assert client.get("/api/picks", params=params).headers["X-Picks-Cache"] == "miss"

    def test_a_malformed_upstream_payload_does_not_produce_a_500(
        self, client, frozen, fake_odds_api, monkeypatch
    ):
        monkeypatch.setenv("THE_ODDS_API_KEY", "live-key")
        fake_odds_api.fail_everything({"message": "unexpected"})
        response = client.get("/api/picks", params={"date": TODAY, "timezone": TZ})
        assert response.status_code == 200
        assert response.json()["games"] == []

    def test_upstream_outage_does_not_produce_a_500(
        self, client, frozen, fake_odds_api, monkeypatch
    ):
        monkeypatch.setenv("THE_ODDS_API_KEY", "live-key")
        fake_odds_api.fail_everything(Raise(httpx.ConnectError("dns")))
        response = client.get("/api/picks", params={"date": TODAY, "timezone": TZ})
        assert response.status_code == 200
        assert response.json()["games"] == []


class TestPicksSingleGame:
    def test_demo_game_returns_its_ranked_picks(self, client, frozen):
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
        assert body["game"]["event_id"] == "demo-nba"
        assert body["pick_count"] == len(body["game"]["picks"]) == svc.PICKS_PER_GAME

    def test_response_envelope_matches_the_frontend_contract(self, client, frozen):
        body = client.get(
            "/api/picks/game",
            params={"sport_key": "basketball_nba", "event_id": "demo-nba", "timezone": TZ},
        ).json()
        assert set(body) == {
            "source",
            "game",
            "pick_count",
            "picks_per_game",
            "game_date",
            "timezone",
            "min_implied_probability",
            "target_implied_probability",
            "relaxed_implied_probability",
            "used_relaxed_implied_fallback",
            "odds_api_warning",
        }

    def test_an_unknown_event_returns_a_null_game(self, client, frozen):
        body = client.get(
            "/api/picks/game",
            params={"sport_key": "basketball_nba", "event_id": "nope", "timezone": TZ},
        ).json()
        assert body["game"] is None
        assert body["pick_count"] == 0

    @pytest.mark.parametrize("sport_key", ["icehockey_nhl", "baseball_mlb", "soccer"])
    def test_unsupported_leagues_are_rejected(self, client, frozen, sport_key):
        response = client.get(
            "/api/picks/game",
            params={"sport_key": sport_key, "event_id": "x", "timezone": TZ},
        )
        assert response.status_code == 400
        assert response.json()["detail"] == "Unsupported sport_key for this endpoint"

    @pytest.mark.parametrize(
        "params",
        [
            {"event_id": "demo-nba"},
            {"sport_key": "basketball_nba"},
            {"sport_key": "", "event_id": "demo-nba"},
            {"sport_key": "basketball_nba", "event_id": ""},
        ],
    )
    def test_missing_identifiers_are_rejected(self, client, frozen, params):
        assert client.get("/api/picks/game", params=params).status_code == 422

    def test_malformed_dates_are_rejected(self, client, frozen):
        response = client.get(
            "/api/picks/game",
            params={"sport_key": "basketball_nba", "event_id": "demo-nba", "date": "yesterday"},
        )
        assert response.status_code == 400
        assert response.json()["detail"] == "Invalid date=YYYY-MM-DD"

    def test_days_outside_the_window_are_rejected(self, client, frozen):
        response = client.get(
            "/api/picks/game",
            params={
                "sport_key": "basketball_nba",
                "event_id": "demo-nba",
                "date": "2026-06-14",
                "timezone": TZ,
            },
        )
        assert response.status_code == 400
        assert "Game day must be from" in response.json()["detail"]

    def test_an_unknown_timezone_is_served_as_utc(self, client, frozen):
        response = client.get(
            "/api/picks/game",
            params={
                "sport_key": "basketball_nba",
                "event_id": "demo-nba",
                "timezone": "Mars/Olympus",
            },
        )
        assert response.status_code == 200
        assert response.json()["game_date"] == TODAY

    def test_picks_per_game_caps_the_response(self, client, frozen):
        body = client.get(
            "/api/picks/game",
            params={
                "sport_key": "basketball_nba",
                "event_id": "demo-nba",
                "timezone": TZ,
                "picks_per_game": 2,
            },
        ).json()
        assert body["picks_per_game"] == 2
        assert body["pick_count"] == 2

    def test_live_refresh_uses_one_upstream_call(self, client, frozen, fake_odds_api, monkeypatch):
        monkeypatch.setenv("THE_ODDS_API_KEY", "live-key")
        fake_odds_api.set_event_odds("basketball_nba", "nba-1", _nba_event())
        body = client.get(
            "/api/picks/game",
            params={
                "sport_key": "basketball_nba",
                "event_id": "nba-1",
                "date": TODAY,
                "timezone": TZ,
            },
        ).json()
        assert body["source"] == "live"
        assert body["game"]["event_id"] == "nba-1"
        assert fake_odds_api.paths == ["/v4/sports/basketball_nba/events/nba-1/odds"]

    def test_game_refresh_is_cached_separately_per_event(
        self, client, frozen, fake_odds_api, monkeypatch
    ):
        monkeypatch.setenv("THE_ODDS_API_KEY", "live-key")
        fake_odds_api.set_event_odds("basketball_nba", "nba-1", _nba_event("nba-1"))
        fake_odds_api.set_event_odds("basketball_nba", "nba-2", _nba_event("nba-2"))
        base = {"sport_key": "basketball_nba", "date": TODAY, "timezone": TZ}

        first = client.get("/api/picks/game", params={**base, "event_id": "nba-1"})
        again = client.get("/api/picks/game", params={**base, "event_id": "nba-1"})
        other = client.get("/api/picks/game", params={**base, "event_id": "nba-2"})

        assert first.headers["X-Picks-Cache"] == "miss"
        assert again.headers["X-Picks-Cache"] == "hit"
        assert other.headers["X-Picks-Cache"] == "miss"
        assert fake_odds_api.count("/events/nba-1/odds") == 1

    def test_the_slate_and_game_caches_do_not_collide(
        self, client, frozen, fake_odds_api, monkeypatch
    ):
        monkeypatch.setenv("THE_ODDS_API_KEY", "live-key")
        fake_odds_api.set_event_odds("basketball_nba", "nba-1", _nba_event())
        client.get("/api/picks", params={"date": TODAY, "timezone": TZ})
        response = client.get(
            "/api/picks/game",
            params={
                "sport_key": "basketball_nba",
                "event_id": "nba-1",
                "date": TODAY,
                "timezone": TZ,
            },
        )
        assert response.headers["X-Picks-Cache"] == "miss"
        assert response.json()["game"] is not None

    def test_rate_limited_refresh_returns_a_warning_not_an_error(
        self, client, frozen, fake_odds_api, monkeypatch
    ):
        monkeypatch.setenv("THE_ODDS_API_KEY", "live-key")
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
        assert body["game"] is None
        assert "429" in body["odds_api_warning"]

    def test_a_malformed_event_payload_does_not_produce_a_500(
        self, client, frozen, fake_odds_api, monkeypatch
    ):
        monkeypatch.setenv("THE_ODDS_API_KEY", "live-key")
        fake_odds_api.set_event_odds("basketball_nba", "nba-1", {"id": "nba-1", "bookmakers": None})
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
        assert response.json()["game"] is None
