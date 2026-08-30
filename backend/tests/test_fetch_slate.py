"""`fetch_best_picks`: demo/live source selection, fan-out, fallback and warning propagation."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import httpx
import pytest

from services.odds import service as svc
from tests.helpers import Fail, Raise, freeze_time, h2h_quotes, odds_event, quote

NOW = datetime(2026, 6, 15, 18, 0, tzinfo=timezone.utc)
TODAY = date(2026, 6, 15)
TOMORROW = date(2026, 6, 16)
TZ = "America/New_York"


def _epl_event(event_id="epl-1", commence_time="2026-06-15T22:00:00Z"):
    return odds_event(
        event_id,
        home="Arsenal",
        away="Chelsea",
        commence_time=commence_time,
        quotes=h2h_quotes({"draftkings": {"Arsenal": 1.85}, "fanduel": {"Arsenal": 1.80}}),
        sport_key="soccer_epl",
    )


def _nba_prop_event(event_id="nba-1", commence_time="2026-06-15T23:00:00Z"):
    return odds_event(
        event_id,
        commence_time=commence_time,
        quotes=[
            quote("draftkings", "player_points", "Over", 1.85, description="Jayson Tatum", point=27.5),
            quote("fanduel", "player_points", "Over", 1.80, description="Jayson Tatum", point=27.5),
            quote("draftkings", "player_assists", "Over", 1.90, description="Jrue Holiday", point=5.5),
            quote("fanduel", "player_assists", "Over", 1.82, description="Jrue Holiday", point=5.5),
        ],
    )


@pytest.fixture
def frozen(monkeypatch):
    return freeze_time(monkeypatch, NOW)


class TestDemoFallback:
    async def test_missing_api_key_serves_the_demo_slate(self, frozen):
        games, source, floor, relaxed, warning = await svc.fetch_best_picks(
            None, for_day=TODAY, timezone_name=TZ
        )
        assert source == "demo"
        assert games
        assert warning is None

    @pytest.mark.parametrize("blank_key", [None, "", "   "])
    async def test_blank_keys_all_count_as_no_key(self, frozen, blank_key):
        _, source, *_ = await svc.fetch_best_picks(blank_key, for_day=TODAY, timezone_name=TZ)
        assert source == "demo"

    async def test_demo_slate_covers_multiple_leagues(self, frozen):
        games, *_ = await svc.fetch_best_picks(None, for_day=TODAY, timezone_name=TZ)
        assert {g["sport_key"] for g in games} >= {"basketball_nba", "soccer_epl"}

    async def test_demo_games_carry_ranked_picks(self, frozen):
        games, *_ = await svc.fetch_best_picks(None, for_day=TODAY, timezone_name=TZ)
        nba = next(g for g in games if g["sport_key"] == "basketball_nba")
        assert len(nba["picks"]) == svc.PICKS_PER_GAME
        scores = [p["rank_score"] for p in nba["picks"]]
        assert scores == sorted(scores, reverse=True)

    async def test_demo_slate_never_lists_a_started_game(self, frozen):
        games, *_ = await svc.fetch_best_picks(None, for_day=TODAY, timezone_name=TZ)
        for game in games:
            assert svc._commence_time_utc(game["commence_time"]) > NOW

    async def test_demo_respects_max_games(self, frozen):
        games, *_ = await svc.fetch_best_picks(
            None, for_day=TODAY, timezone_name=TZ, max_games=1
        )
        assert len(games) == 1

    async def test_demo_respects_picks_per_game(self, frozen):
        games, *_ = await svc.fetch_best_picks(
            None, for_day=TODAY, timezone_name=TZ, picks_per_game=2
        )
        assert all(len(g["picks"]) <= 2 for g in games)

    async def test_demo_slate_is_available_for_future_days(self, frozen):
        games, source, *_ = await svc.fetch_best_picks(
            None, for_day=TODAY + timedelta(days=3), timezone_name=TZ
        )
        assert source == "demo"
        assert games


class TestDemoClockEdges:
    async def test_a_demo_kickoff_already_past_is_pushed_forward(self, monkeypatch):
        """Demo games have fixed local tip-off times, so late in the day they must be bumped."""
        freeze_time(monkeypatch, datetime(2026, 6, 16, 2, 0, tzinfo=timezone.utc))  # 22:00 ET
        games, source, *_ = await svc.fetch_best_picks(None, for_day=TODAY, timezone_name=TZ)
        assert source == "demo"
        assert games
        for game in games:
            assert svc._commence_time_utc(game["commence_time"]) > datetime(
                2026, 6, 16, 2, 0, tzinfo=timezone.utc
            )

    async def test_the_demo_slate_empties_at_the_very_end_of_the_local_day(self, monkeypatch):
        """There is no room left to bump a kickoff into, so the day is simply over."""
        freeze_time(monkeypatch, datetime(2026, 6, 16, 3, 59, 40, tzinfo=timezone.utc))
        games, source, *_ = await svc.fetch_best_picks(None, for_day=TODAY, timezone_name=TZ)
        assert source == "demo"
        assert games == []


class TestPastDays:
    async def test_past_day_returns_nothing_without_calling_upstream(self, frozen, fake_odds_api):
        games, source, floor, relaxed, warning = await svc.fetch_best_picks(
            "live-key", for_day=TODAY - timedelta(days=1), timezone_name=TZ
        )
        assert games == []
        assert source == "live"
        assert floor == svc.MIN_IMPLIED_PROBABILITY
        assert relaxed is False
        assert warning is None
        assert fake_odds_api.requests == []

    async def test_past_day_without_a_key_is_labelled_demo(self, frozen):
        _, source, *_ = await svc.fetch_best_picks(
            None, for_day=TODAY - timedelta(days=1), timezone_name=TZ
        )
        assert source == "demo"

    async def test_the_cutoff_is_evaluated_in_the_request_timezone(self, frozen, fake_odds_api):
        """At 18:00Z it is already 2026-06-16 in Tokyo, so that day is fetchable there only."""
        await svc.fetch_best_picks("live-key", for_day=TOMORROW, timezone_name="Asia/Tokyo")
        assert fake_odds_api.requests

        fake_odds_api.requests.clear()
        games, *_ = await svc.fetch_best_picks("live-key", for_day=TODAY, timezone_name="Asia/Tokyo")
        assert games == []
        assert fake_odds_api.requests == []


class TestLiveSlate:
    async def test_live_slate_merges_soccer_and_prop_leagues(self, frozen, fake_odds_api):
        fake_odds_api.set_bulk_odds("soccer_epl", [_epl_event()])
        fake_odds_api.set_events("basketball_nba", [odds_event("nba-1", commence_time="2026-06-15T23:00:00Z")])
        fake_odds_api.set_event_odds("basketball_nba", "nba-1", _nba_prop_event())

        games, source, floor, relaxed, warning = await svc.fetch_best_picks(
            "live-key", for_day=TODAY, timezone_name=TZ
        )
        assert source == "live"
        assert warning is None
        assert relaxed is False
        assert floor == svc.MIN_IMPLIED_PROBABILITY
        assert {g["event_id"] for g in games} == {"epl-1", "nba-1"}

    async def test_every_supported_league_is_queried(self, frozen, fake_odds_api):
        await svc.fetch_best_picks("live-key", for_day=TODAY, timezone_name=TZ)
        for sport_key in svc.SOCCER_SPORT_KEYS:
            assert fake_odds_api.count(f"/sports/{sport_key}/odds") == 1
        assert fake_odds_api.count("/sports/basketball_nba/events") == 1
        assert fake_odds_api.count("/sports/americanfootball_nfl/events") == 1

    async def test_the_slate_window_is_sent_upstream(self, frozen, fake_odds_api):
        await svc.fetch_best_picks("live-key", for_day=TODAY, timezone_name=TZ)
        params = fake_odds_api.params_for("/sports/soccer_epl/odds")
        assert params["commenceTimeFrom"] == "2026-06-15T04:00:00Z"
        assert params["commenceTimeTo"] == "2026-06-16T03:59:59Z"

    async def test_the_key_is_trimmed_before_use(self, frozen, fake_odds_api):
        await svc.fetch_best_picks("  live-key \n", for_day=TODAY, timezone_name=TZ)
        assert fake_odds_api.api_keys_used() == {"live-key"}

    async def test_scheduled_games_without_qualifying_picks_are_still_listed(
        self, frozen, fake_odds_api
    ):
        priced = _epl_event("epl-1")
        unpriced = odds_event(
            "epl-2",
            commence_time="2026-06-15T21:00:00Z",
            quotes=[],
            sport_key="soccer_epl",
        )
        fake_odds_api.set_bulk_odds("soccer_epl", [priced, unpriced])

        games, *_ = await svc.fetch_best_picks("live-key", for_day=TODAY, timezone_name=TZ)
        by_id = {g["event_id"]: g for g in games}
        assert by_id["epl-2"]["picks"] == []
        assert by_id["epl-1"]["picks"]

    async def test_games_outside_the_requested_day_are_dropped(self, frozen, fake_odds_api):
        """The upstream window is inclusive at the edges, so re-filter locally."""
        fake_odds_api.set_bulk_odds(
            "soccer_epl",
            [_epl_event("today", "2026-06-15T22:00:00Z"), _epl_event("next-day", "2026-06-17T22:00:00Z")],
        )
        games, *_ = await svc.fetch_best_picks("live-key", for_day=TODAY, timezone_name=TZ)
        assert {g["event_id"] for g in games} == {"today"}

    async def test_started_games_are_dropped_from_the_slate(self, frozen, fake_odds_api):
        fake_odds_api.set_bulk_odds(
            "soccer_epl",
            [_epl_event("started", "2026-06-15T16:00:00Z"), _epl_event("upcoming", "2026-06-15T22:00:00Z")],
        )
        games, *_ = await svc.fetch_best_picks("live-key", for_day=TODAY, timezone_name=TZ)
        assert {g["event_id"] for g in games} == {"upcoming"}

    async def test_games_are_returned_in_kickoff_order(self, frozen, fake_odds_api):
        fake_odds_api.set_bulk_odds(
            "soccer_epl",
            [_epl_event("late", "2026-06-15T23:00:00Z"), _epl_event("early", "2026-06-15T20:00:00Z")],
        )
        games, *_ = await svc.fetch_best_picks("live-key", for_day=TODAY, timezone_name=TZ)
        assert [g["event_id"] for g in games] == ["early", "late"]

    async def test_max_games_limits_the_response(self, frozen, fake_odds_api):
        fake_odds_api.set_bulk_odds(
            "soccer_epl",
            [_epl_event("a", "2026-06-15T20:00:00Z"), _epl_event("b", "2026-06-15T23:00:00Z")],
        )
        games, *_ = await svc.fetch_best_picks(
            "live-key", for_day=TODAY, timezone_name=TZ, max_games=1
        )
        assert [g["event_id"] for g in games] == ["a"]

    async def test_relaxed_floor_is_reported_when_it_rescues_the_slate(self, frozen, fake_odds_api):
        marginal = odds_event(
            "epl-1",
            home="Arsenal",
            away="Chelsea",
            commence_time="2026-06-15T22:00:00Z",
            quotes=h2h_quotes({"draftkings": {"Arsenal": 1.99}, "fanduel": {"Arsenal": 1.96}}),
            sport_key="soccer_epl",
        )
        fake_odds_api.set_bulk_odds("soccer_epl", [marginal])

        games, _, floor, relaxed, _ = await svc.fetch_best_picks(
            "live-key", for_day=TODAY, timezone_name=TZ
        )
        assert relaxed is True
        assert floor == svc.RELAXED_IMPLIED_PROBABILITY
        assert games[0]["picks"]

    async def test_long_prices_are_filtered_out_entirely(self, frozen, fake_odds_api):
        longshot = odds_event(
            "epl-1",
            home="Arsenal",
            away="Chelsea",
            commence_time="2026-06-15T22:00:00Z",
            quotes=h2h_quotes({"draftkings": {"Chelsea": 6.0}, "fanduel": {"Chelsea": 5.5}}),
            sport_key="soccer_epl",
        )
        fake_odds_api.set_bulk_odds("soccer_epl", [longshot])
        games, *_ = await svc.fetch_best_picks("live-key", for_day=TODAY, timezone_name=TZ)
        assert [g["picks"] for g in games] == [[]]


class TestUpstreamFailures:
    async def test_empty_upstream_slate_is_an_empty_response(self, frozen, fake_odds_api):
        games, source, _, _, warning = await svc.fetch_best_picks(
            "live-key", for_day=TODAY, timezone_name=TZ
        )
        assert games == []
        assert source == "live"
        assert warning is None

    async def test_rate_limited_slate_reports_a_warning(self, frozen, fake_odds_api):
        fake_odds_api.fail_everything(Fail(429))
        games, source, _, _, warning = await svc.fetch_best_picks(
            "live-key", for_day=TODAY, timezone_name=TZ
        )
        assert games == []
        assert source == "live"
        assert "429" in warning

    async def test_quota_exhausted_slate_reports_a_billing_warning(self, frozen, fake_odds_api):
        fake_odds_api.fail_everything(Fail(402))
        _, _, _, _, warning = await svc.fetch_best_picks(
            "live-key", for_day=TODAY, timezone_name=TZ
        )
        assert "402" in warning

    async def test_partial_rate_limit_still_returns_the_leagues_that_worked(
        self, frozen, fake_odds_api
    ):
        fake_odds_api.set_bulk_odds("soccer_epl", [_epl_event()])
        fake_odds_api.set_bulk_odds("soccer_spain_la_liga", Fail(429))
        games, _, _, _, warning = await svc.fetch_best_picks(
            "live-key", for_day=TODAY, timezone_name=TZ
        )
        assert [g["event_id"] for g in games] == ["epl-1"]
        assert "part of the slate may be" in warning

    async def test_server_errors_do_not_produce_a_quota_warning(self, frozen, fake_odds_api):
        fake_odds_api.fail_everything(Fail(500))
        games, _, _, _, warning = await svc.fetch_best_picks(
            "live-key", for_day=TODAY, timezone_name=TZ
        )
        assert games == []
        assert warning is None

    async def test_transport_errors_are_survivable(self, frozen, fake_odds_api):
        fake_odds_api.fail_everything(Raise(httpx.ConnectError("dns")))
        games, source, _, _, warning = await svc.fetch_best_picks(
            "live-key", for_day=TODAY, timezone_name=TZ
        )
        assert games == []
        assert source == "live"
        assert warning is None

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
    async def test_malformed_league_payloads_do_not_break_the_slate(
        self, frozen, fake_odds_api, payload
    ):
        fake_odds_api.set_bulk_odds("soccer_epl", payload)
        fake_odds_api.set_bulk_odds("soccer_italy_serie_a", [_epl_event("serie-1")])
        games, source, *_ = await svc.fetch_best_picks(
            "live-key", for_day=TODAY, timezone_name=TZ
        )
        assert source == "live"
        assert "serie-1" in {g["event_id"] for g in games}

    async def test_undecodable_body_does_not_break_the_slate(self, frozen, fake_odds_api):
        fake_odds_api.set_bulk_odds("soccer_epl", httpx.Response(200, text="<html>502</html>"))
        fake_odds_api.set_bulk_odds("soccer_italy_serie_a", [_epl_event("serie-1")])
        games, *_ = await svc.fetch_best_picks("live-key", for_day=TODAY, timezone_name=TZ)
        assert [g["event_id"] for g in games] == ["serie-1"]
