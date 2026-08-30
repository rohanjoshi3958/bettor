"""`fetch_picks_for_event`: targeted refresh of one game (props or a soccer bulk slice)."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest

from services.odds import service as svc
from tests.helpers import Fail, Seq, freeze_time, h2h_quotes, odds_event, quote

NOW = datetime(2026, 6, 15, 18, 0, tzinfo=timezone.utc)
TODAY = date(2026, 6, 15)
TZ = "America/New_York"


@pytest.fixture
def frozen(monkeypatch):
    return freeze_time(monkeypatch, NOW)


def _nba_event(event_id="nba-1", commence_time="2026-06-15T23:00:00Z", outcomes=4):
    quotes = []
    markets = ["player_points", "player_assists", "player_rebounds", "player_threes"]
    for index, market in enumerate(markets[:outcomes]):
        quotes.append(
            quote("draftkings", market, "Over", 1.85 - index / 100, description="Jayson Tatum", point=10.5)
        )
        quotes.append(
            quote("fanduel", market, "Over", 1.80 - index / 100, description="Jayson Tatum", point=10.5)
        )
    return odds_event(event_id, commence_time=commence_time, quotes=quotes)


def _epl_event(event_id="epl-1", commence_time="2026-06-15T22:00:00Z"):
    return odds_event(
        event_id,
        home="Arsenal",
        away="Chelsea",
        commence_time=commence_time,
        quotes=h2h_quotes({"draftkings": {"Arsenal": 1.85}, "fanduel": {"Arsenal": 1.80}}),
        sport_key="soccer_epl",
    )


class TestGuards:
    @pytest.mark.parametrize("sport_key", ["icehockey_nhl", "", "soccer", "basketball_NBA"])
    async def test_unsupported_leagues_are_refused_before_any_call(
        self, frozen, fake_odds_api, sport_key
    ):
        game, source, floor, relaxed, warning = await svc.fetch_picks_for_event(
            "live-key", sport_key=sport_key, event_id="x", for_day=TODAY, timezone_name=TZ
        )
        assert game is None
        assert source == "live"
        assert (floor, relaxed, warning) == (svc.MIN_IMPLIED_PROBABILITY, False, None)
        assert fake_odds_api.requests == []

    async def test_past_days_are_refused_before_any_call(self, frozen, fake_odds_api):
        game, source, *_ = await svc.fetch_picks_for_event(
            "live-key",
            sport_key="basketball_nba",
            event_id="nba-1",
            for_day=TODAY - timedelta(days=1),
            timezone_name=TZ,
        )
        assert game is None
        assert source == "live"
        assert fake_odds_api.requests == []

    async def test_past_days_without_a_key_report_demo(self, frozen):
        _, source, *_ = await svc.fetch_picks_for_event(
            None,
            sport_key="basketball_nba",
            event_id="nba-1",
            for_day=TODAY - timedelta(days=1),
            timezone_name=TZ,
        )
        assert source == "demo"


class TestDemoMode:
    async def test_a_known_demo_event_returns_its_ranked_picks(self, frozen):
        game, source, _, relaxed, warning = await svc.fetch_picks_for_event(
            None, sport_key="basketball_nba", event_id="demo-nba", for_day=TODAY, timezone_name=TZ
        )
        assert source == "demo"
        assert warning is None
        assert game["event_id"] == "demo-nba"
        assert len(game["picks"]) == svc.PICKS_PER_GAME

    async def test_picks_per_game_is_honored(self, frozen):
        game, *_ = await svc.fetch_picks_for_event(
            None,
            sport_key="basketball_nba",
            event_id="demo-nba",
            for_day=TODAY,
            timezone_name=TZ,
            picks_per_game=1,
        )
        assert len(game["picks"]) == 1

    async def test_an_unknown_event_id_returns_nothing(self, frozen):
        game, source, *_ = await svc.fetch_picks_for_event(
            None, sport_key="basketball_nba", event_id="not-a-demo-game", for_day=TODAY, timezone_name=TZ
        )
        assert game is None
        assert source == "demo"

    async def test_a_demo_event_from_another_league_is_not_returned(self, frozen):
        game, *_ = await svc.fetch_picks_for_event(
            None, sport_key="soccer_epl", event_id="demo-nba", for_day=TODAY, timezone_name=TZ
        )
        assert game is None


class TestLiveProps:
    async def test_one_prop_call_is_made_for_the_requested_event(self, frozen, fake_odds_api):
        fake_odds_api.set_event_odds("basketball_nba", "nba-1", _nba_event())
        game, source, _, relaxed, warning = await svc.fetch_picks_for_event(
            "live-key", sport_key="basketball_nba", event_id="nba-1", for_day=TODAY, timezone_name=TZ
        )
        assert source == "live"
        assert warning is None
        assert game["event_id"] == "nba-1"
        assert fake_odds_api.paths == ["/v4/sports/basketball_nba/events/nba-1/odds"]

    async def test_only_the_top_picks_are_kept(self, frozen, fake_odds_api):
        fake_odds_api.set_event_odds("basketball_nba", "nba-1", _nba_event(outcomes=4))
        game, *_ = await svc.fetch_picks_for_event(
            "live-key", sport_key="basketball_nba", event_id="nba-1", for_day=TODAY, timezone_name=TZ
        )
        assert len(game["picks"]) == svc.PICKS_PER_GAME

    async def test_the_event_id_is_normalized_before_the_call(self, frozen, fake_odds_api):
        fake_odds_api.set_event_odds("basketball_nba", "1234", _nba_event("1234"))
        game, *_ = await svc.fetch_picks_for_event(
            "live-key", sport_key="basketball_nba", event_id="1234.0", for_day=TODAY, timezone_name=TZ
        )
        assert game["event_id"] == "1234"

    async def test_whitespace_in_the_key_is_trimmed(self, frozen, fake_odds_api):
        fake_odds_api.set_event_odds("basketball_nba", "nba-1", _nba_event())
        await svc.fetch_picks_for_event(
            " live-key ", sport_key="basketball_nba", event_id="nba-1", for_day=TODAY, timezone_name=TZ
        )
        assert fake_odds_api.api_keys_used() == {"live-key"}

    async def test_nfl_uses_the_football_market_list(self, frozen, fake_odds_api):
        event = odds_event(
            "nfl-1",
            home="Kansas City Chiefs",
            away="Buffalo Bills",
            commence_time="2026-06-15T21:00:00Z",
            quotes=[
                quote("draftkings", "player_pass_yds", "Over", 1.85, description="Josh Allen", point=274.5),
                quote("fanduel", "player_pass_yds", "Over", 1.80, description="Josh Allen", point=274.5),
            ],
        )
        fake_odds_api.set_event_odds("americanfootball_nfl", "nfl-1", event)
        game, *_ = await svc.fetch_picks_for_event(
            "live-key",
            sport_key="americanfootball_nfl",
            event_id="nfl-1",
            for_day=TODAY,
            timezone_name=TZ,
        )
        assert game["picks"][0]["pick"].startswith("Pass Yds · Josh Allen")
        params = fake_odds_api.params_for("/events/nfl-1/odds")
        assert "player_pass_yds" in params["markets"]
        assert "player_points" not in params["markets"]

    async def test_an_event_with_no_qualifying_prices_returns_nothing(self, frozen, fake_odds_api):
        fake_odds_api.set_event_odds(
            "basketball_nba", "nba-1", odds_event("nba-1", commence_time="2026-06-15T23:00:00Z", quotes=[])
        )
        game, source, *_ = await svc.fetch_picks_for_event(
            "live-key", sport_key="basketball_nba", event_id="nba-1", for_day=TODAY, timezone_name=TZ
        )
        assert game is None
        assert source == "live"

    async def test_a_started_game_is_not_returned(self, frozen, fake_odds_api):
        fake_odds_api.set_event_odds(
            "basketball_nba", "nba-1", _nba_event(commence_time="2026-06-15T17:00:00Z")
        )
        game, *_ = await svc.fetch_picks_for_event(
            "live-key", sport_key="basketball_nba", event_id="nba-1", for_day=TODAY, timezone_name=TZ
        )
        assert game is None

    async def test_an_unexpected_event_id_in_the_response_is_still_returned(
        self, frozen, fake_odds_api
    ):
        """Upstream occasionally echoes a different id form; the refresh should not come back empty."""
        fake_odds_api.set_event_odds("basketball_nba", "nba-1", _nba_event("NBA-1"))
        game, *_ = await svc.fetch_picks_for_event(
            "live-key", sport_key="basketball_nba", event_id="nba-1", for_day=TODAY, timezone_name=TZ
        )
        assert game is not None
        assert game["event_id"] == "NBA-1"

    async def test_a_game_on_another_day_is_not_returned(self, frozen, fake_odds_api):
        fake_odds_api.set_event_odds(
            "basketball_nba", "nba-1", _nba_event(commence_time="2026-06-18T23:00:00Z")
        )
        game, *_ = await svc.fetch_picks_for_event(
            "live-key", sport_key="basketball_nba", event_id="nba-1", for_day=TODAY, timezone_name=TZ
        )
        assert game is None


class TestLiveSoccer:
    async def test_the_bulk_slate_is_sliced_down_to_one_event(self, frozen, fake_odds_api):
        fake_odds_api.set_bulk_odds(
            "soccer_epl", [_epl_event("epl-1"), _epl_event("epl-2", "2026-06-15T20:00:00Z")]
        )
        game, source, *_ = await svc.fetch_picks_for_event(
            "live-key", sport_key="soccer_epl", event_id="epl-2", for_day=TODAY, timezone_name=TZ
        )
        assert source == "live"
        assert game["event_id"] == "epl-2"
        assert fake_odds_api.paths == ["/v4/sports/soccer_epl/odds"]

    async def test_the_day_window_is_forwarded(self, frozen, fake_odds_api):
        fake_odds_api.set_bulk_odds("soccer_epl", [_epl_event()])
        await svc.fetch_picks_for_event(
            "live-key", sport_key="soccer_epl", event_id="epl-1", for_day=TODAY, timezone_name=TZ
        )
        params = fake_odds_api.params_for("/sports/soccer_epl/odds")
        assert params["commenceTimeFrom"] == "2026-06-15T04:00:00Z"
        assert params["commenceTimeTo"] == "2026-06-16T03:59:59Z"

    async def test_an_event_missing_from_the_slate_returns_nothing(self, frozen, fake_odds_api):
        fake_odds_api.set_bulk_odds("soccer_epl", [_epl_event("epl-1")])
        game, *_ = await svc.fetch_picks_for_event(
            "live-key", sport_key="soccer_epl", event_id="epl-999", for_day=TODAY, timezone_name=TZ
        )
        assert game is None


class TestFailureHandling:
    async def test_a_rate_limited_refresh_reports_the_warning(self, frozen, fake_odds_api):
        fake_odds_api.fail_everything(Fail(429))
        game, source, _, _, warning = await svc.fetch_picks_for_event(
            "live-key", sport_key="basketball_nba", event_id="nba-1", for_day=TODAY, timezone_name=TZ
        )
        assert game is None
        assert source == "live"
        assert "429" in warning

    async def test_a_quota_error_reports_the_billing_warning(self, frozen, fake_odds_api):
        fake_odds_api.fail_everything(Fail(402))
        _, _, _, _, warning = await svc.fetch_picks_for_event(
            "live-key", sport_key="soccer_epl", event_id="epl-1", for_day=TODAY, timezone_name=TZ
        )
        assert "402" in warning

    async def test_a_partial_rate_limit_still_returns_the_game(self, frozen, fake_odds_api):
        fake_odds_api.set_event_odds("basketball_nba", "nba-1", Seq([Fail(429), _nba_event()]))
        game, _, _, _, warning = await svc.fetch_picks_for_event(
            "live-key", sport_key="basketball_nba", event_id="nba-1", for_day=TODAY, timezone_name=TZ
        )
        assert game is not None
        assert "part of the slate may be" in warning

    async def test_a_server_error_produces_no_warning(self, frozen, fake_odds_api):
        fake_odds_api.fail_everything(Fail(503))
        game, _, _, _, warning = await svc.fetch_picks_for_event(
            "live-key", sport_key="basketball_nba", event_id="nba-1", for_day=TODAY, timezone_name=TZ
        )
        assert game is None
        assert warning is None

    @pytest.mark.parametrize(
        "payload",
        [
            [],
            None,
            {"id": "nba-1", "bookmakers": None},
            {"id": "nba-1", "bookmakers": [{"markets": [{"key": "player_points", "outcomes": [{}]}]}]},
        ],
    )
    async def test_malformed_event_payloads_return_nothing(self, frozen, fake_odds_api, payload):
        fake_odds_api.set_event_odds("basketball_nba", "nba-1", payload)
        game, source, *_ = await svc.fetch_picks_for_event(
            "live-key", sport_key="basketball_nba", event_id="nba-1", for_day=TODAY, timezone_name=TZ
        )
        assert game is None
        assert source == "live"
