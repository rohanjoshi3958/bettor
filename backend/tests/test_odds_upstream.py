"""Odds API boundary: request shape, quota/error handling, retries, malformed payloads."""

from __future__ import annotations

import httpx
import pytest

from services.odds import service as svc
from services.odds import client as client_mod
from tests.helpers import Fail, Raise, Seq, freeze_time, h2h_quotes, odds_event, quote

from datetime import datetime, timezone

NOW = datetime(2026, 6, 15, 18, 0, tzinfo=timezone.utc)


@pytest.fixture
async def odds_client(fake_odds_api):
    """An httpx client wired to the fake Odds API (the fixture patches the client module's httpx)."""
    async with client_mod.httpx.AsyncClient() as client:
        yield client


def _two_book_h2h(event_id="epl-1"):
    return odds_event(
        event_id,
        home="Arsenal",
        away="Chelsea",
        commence_time="2026-06-15T19:00:00Z",
        quotes=h2h_quotes({"draftkings": {"Arsenal": 1.85}, "fanduel": {"Arsenal": 1.80}}),
        sport_key="soccer_epl",
    )


def _two_book_props(event_id="nba-1"):
    return odds_event(
        event_id,
        commence_time="2026-06-15T23:00:00Z",
        quotes=[
            quote("draftkings", "player_points", "Over", 1.85, description="Jayson Tatum", point=27.5),
            quote("fanduel", "player_points", "Over", 1.80, description="Jayson Tatum", point=27.5),
        ],
    )


class TestFetchBulkH2h:
    async def test_returns_picks_and_schedule_shells(self, fake_odds_api, odds_client):
        fake_odds_api.set_bulk_odds("soccer_epl", [_two_book_h2h()])
        picks, shells = await svc._fetch_bulk_h2h(odds_client, "key", "soccer_epl", "EPL")
        assert [p.pick for p in picks] == ["Arsenal"]
        assert [s["event_id"] for s in shells] == ["epl-1"]

    async def test_sends_the_documented_query_parameters(self, fake_odds_api, odds_client):
        await svc._fetch_bulk_h2h(odds_client, "secret", "soccer_epl", "EPL")
        params = fake_odds_api.params_for("/sports/soccer_epl/odds")
        assert params["apiKey"] == "secret"
        assert params["markets"] == "h2h"
        assert params["oddsFormat"] == "decimal"
        assert params["regions"] == "us"
        assert set(params["bookmakers"].split(",")) == set(svc.ALLOWED_BOOKMAKER_KEYS)

    async def test_slate_window_is_forwarded_when_provided(self, fake_odds_api, odds_client):
        await svc._fetch_bulk_h2h(
            odds_client,
            "key",
            "soccer_epl",
            "EPL",
            "2026-06-15T04:00:00Z",
            "2026-06-16T03:59:59Z",
        )
        params = fake_odds_api.params_for("/sports/soccer_epl/odds")
        assert params["commenceTimeFrom"] == "2026-06-15T04:00:00Z"
        assert params["commenceTimeTo"] == "2026-06-16T03:59:59Z"

    async def test_window_parameters_are_omitted_when_absent(self, fake_odds_api, odds_client):
        await svc._fetch_bulk_h2h(odds_client, "key", "soccer_epl", "EPL")
        params = fake_odds_api.params_for("/sports/soccer_epl/odds")
        assert "commenceTimeFrom" not in params
        assert "commenceTimeTo" not in params

    async def test_empty_slate_is_not_an_error(self, fake_odds_api, odds_client):
        fake_odds_api.set_bulk_odds("soccer_epl", [])
        assert await svc._fetch_bulk_h2h(odds_client, "key", "soccer_epl", "EPL") == ([], [])

    @pytest.mark.parametrize("payload", [{}, {"message": "nope"}, None, "text", 42])
    async def test_non_list_payloads_are_ignored(self, fake_odds_api, odds_client, payload):
        fake_odds_api.set_bulk_odds("soccer_epl", payload)
        assert await svc._fetch_bulk_h2h(odds_client, "key", "soccer_epl", "EPL") == ([], [])

    @pytest.mark.parametrize("body", ["<html>502 Bad Gateway</html>", "", "{truncated"])
    async def test_undecodable_body_degrades_to_empty(self, fake_odds_api, odds_client, body):
        """A proxy error page or truncated response must not surface as a 500 to the frontend."""
        fake_odds_api.set_bulk_odds("soccer_epl", httpx.Response(200, text=body))
        assert await svc._fetch_bulk_h2h(odds_client, "key", "soccer_epl", "EPL") == ([], [])

    async def test_rate_limit_is_recorded_as_a_quota_event(self, fake_odds_api, odds_client):
        fake_odds_api.set_bulk_odds("soccer_epl", Fail(429))
        quota: list[str] = []
        result = await svc._fetch_bulk_h2h(
            odds_client, "key", "soccer_epl", "EPL", quota_events=quota
        )
        assert result == ([], [])
        assert quota == ["rate_limit"]

    async def test_payment_required_is_recorded_as_a_quota_event(self, fake_odds_api, odds_client):
        fake_odds_api.set_bulk_odds("soccer_epl", Fail(402))
        quota: list[str] = []
        await svc._fetch_bulk_h2h(odds_client, "key", "soccer_epl", "EPL", quota_events=quota)
        assert quota == ["payment_required"]

    @pytest.mark.parametrize("status", [401, 404, 422, 500, 503])
    async def test_other_http_errors_are_swallowed_without_a_quota_event(
        self, fake_odds_api, odds_client, status
    ):
        fake_odds_api.set_bulk_odds("soccer_epl", Fail(status))
        quota: list[str] = []
        result = await svc._fetch_bulk_h2h(
            odds_client, "key", "soccer_epl", "EPL", quota_events=quota
        )
        assert result == ([], [])
        assert quota == []

    @pytest.mark.parametrize(
        "error",
        [
            httpx.ConnectError("dns"),
            httpx.ReadTimeout("slow"),
            httpx.RemoteProtocolError("truncated"),
        ],
    )
    async def test_transport_failures_are_swallowed(self, fake_odds_api, odds_client, error):
        fake_odds_api.set_bulk_odds("soccer_epl", Raise(error))
        assert await svc._fetch_bulk_h2h(odds_client, "key", "soccer_epl", "EPL") == ([], [])

    async def test_quota_events_are_optional(self, fake_odds_api, odds_client):
        fake_odds_api.set_bulk_odds("soccer_epl", Fail(429))
        assert await svc._fetch_bulk_h2h(odds_client, "key", "soccer_epl", "EPL") == ([], [])

    async def test_shells_include_games_with_no_qualifying_prices(self, fake_odds_api, odds_client):
        """Every scheduled game must appear in the UI even when no pick clears the floor."""
        no_prices = odds_event(
            "epl-2", commence_time="2026-06-15T21:00:00Z", quotes=[], sport_key="soccer_epl"
        )
        fake_odds_api.set_bulk_odds("soccer_epl", [_two_book_h2h(), no_prices])
        picks, shells = await svc._fetch_bulk_h2h(odds_client, "key", "soccer_epl", "EPL")
        assert len(picks) == 1
        assert [s["event_id"] for s in shells] == ["epl-1", "epl-2"]


class TestFetchEvents:
    async def test_returns_the_schedule_payload(self, fake_odds_api, odds_client):
        fake_odds_api.set_events("basketball_nba", [{"id": "nba-1"}])
        assert await svc._fetch_events(odds_client, "key", "basketball_nba") == [{"id": "nba-1"}]

    async def test_sends_only_the_api_key_and_optional_window(self, fake_odds_api, odds_client):
        await svc._fetch_events(
            odds_client, "secret", "basketball_nba", "2026-06-15T04:00:00Z", "2026-06-16T03:59:59Z"
        )
        params = fake_odds_api.params_for("/sports/basketball_nba/events")
        assert params == {
            "apiKey": "secret",
            "commenceTimeFrom": "2026-06-15T04:00:00Z",
            "commenceTimeTo": "2026-06-16T03:59:59Z",
        }

    @pytest.mark.parametrize("payload", [{}, None, "oops"])
    async def test_non_list_payloads_become_an_empty_schedule(
        self, fake_odds_api, odds_client, payload
    ):
        fake_odds_api.set_events("basketball_nba", payload)
        assert await svc._fetch_events(odds_client, "key", "basketball_nba") == []

    async def test_invalid_json_body_degrades_to_empty(self, fake_odds_api, odds_client):
        fake_odds_api.set_events("basketball_nba", httpx.Response(200, text="not json"))
        assert await svc._fetch_events(odds_client, "key", "basketball_nba") == []

    @pytest.mark.parametrize(
        ("status", "expected"), [(429, ["rate_limit"]), (402, ["payment_required"]), (500, [])]
    )
    async def test_error_statuses_map_to_quota_events(
        self, fake_odds_api, odds_client, status, expected
    ):
        fake_odds_api.set_events("basketball_nba", Fail(status))
        quota: list[str] = []
        assert await svc._fetch_events(odds_client, "key", "basketball_nba", quota_events=quota) == []
        assert quota == expected

    async def test_transport_failure_returns_empty(self, fake_odds_api, odds_client):
        fake_odds_api.set_events("basketball_nba", Raise(httpx.ConnectTimeout("t")))
        assert await svc._fetch_events(odds_client, "key", "basketball_nba") == []


class TestFetchEventProps:
    async def test_parses_prop_picks_for_one_event(self, fake_odds_api, odds_client):
        fake_odds_api.set_event_odds("basketball_nba", "nba-1", _two_book_props())
        picks = await svc._fetch_event_props(
            odds_client,
            "key",
            "basketball_nba",
            "NBA",
            "nba-1",
            svc.NBA_PROP_MARKETS,
            svc._nba_market_set(),
        )
        assert [p.pick for p in picks] == ["PTS · Jayson Tatum — Over 27.5"]

    async def test_requests_the_configured_markets(self, fake_odds_api, odds_client):
        await svc._fetch_event_props(
            odds_client,
            "secret",
            "basketball_nba",
            "NBA",
            "nba-1",
            "player_points,player_assists",
            {"player_points", "player_assists"},
        )
        params = fake_odds_api.params_for("/events/nba-1/odds")
        assert params["markets"] == "player_points,player_assists"
        assert params["oddsFormat"] == "decimal"
        assert params["apiKey"] == "secret"

    @pytest.mark.parametrize("payload", [[], None, "text", 7])
    async def test_non_object_payloads_produce_no_picks(
        self, fake_odds_api, odds_client, payload
    ):
        fake_odds_api.set_event_odds("basketball_nba", "nba-1", payload)
        assert (
            await svc._fetch_event_props(
                odds_client, "key", "basketball_nba", "NBA", "nba-1", "player_points", {"player_points"}
            )
            == []
        )

    async def test_invalid_json_body_degrades_to_empty(self, fake_odds_api, odds_client):
        fake_odds_api.set_event_odds(
            "basketball_nba", "nba-1", httpx.Response(200, text="<html>502</html>")
        )
        assert (
            await svc._fetch_event_props(
                odds_client, "key", "basketball_nba", "NBA", "nba-1", "player_points", {"player_points"}
            )
            == []
        )

    async def test_rate_limit_is_retried_once_and_can_succeed(
        self, fake_odds_api, odds_client, instant_sleep
    ):
        fake_odds_api.set_event_odds(
            "basketball_nba", "nba-1", Seq([Fail(429), _two_book_props()])
        )
        quota: list[str] = []
        picks = await svc._fetch_event_props(
            odds_client,
            "key",
            "basketball_nba",
            "NBA",
            "nba-1",
            svc.NBA_PROP_MARKETS,
            svc._nba_market_set(),
            quota,
        )
        assert len(picks) == 1
        assert fake_odds_api.count("/events/nba-1/odds") == 2
        assert quota == ["rate_limit"]
        assert instant_sleep == [1.2]

    async def test_repeated_rate_limits_give_up_after_one_retry(
        self, fake_odds_api, odds_client, instant_sleep
    ):
        fake_odds_api.set_event_odds("basketball_nba", "nba-1", Fail(429))
        quota: list[str] = []
        picks = await svc._fetch_event_props(
            odds_client, "key", "basketball_nba", "NBA", "nba-1", "player_points", {"player_points"}, quota
        )
        assert picks == []
        assert fake_odds_api.count("/events/nba-1/odds") == 2
        assert quota == ["rate_limit", "rate_limit"]

    async def test_payment_required_is_not_retried(self, fake_odds_api, odds_client):
        fake_odds_api.set_event_odds("basketball_nba", "nba-1", Fail(402))
        quota: list[str] = []
        picks = await svc._fetch_event_props(
            odds_client, "key", "basketball_nba", "NBA", "nba-1", "player_points", {"player_points"}, quota
        )
        assert picks == []
        assert fake_odds_api.count("/events/nba-1/odds") == 1
        assert quota == ["payment_required"]

    async def test_timeout_is_retried_once(self, fake_odds_api, odds_client, instant_sleep):
        fake_odds_api.set_event_odds(
            "basketball_nba", "nba-1", Seq([Raise(httpx.ReadTimeout("slow")), _two_book_props()])
        )
        picks = await svc._fetch_event_props(
            odds_client,
            "key",
            "basketball_nba",
            "NBA",
            "nba-1",
            svc.NBA_PROP_MARKETS,
            svc._nba_market_set(),
        )
        assert len(picks) == 1
        assert instant_sleep == [0.6]

    async def test_repeated_timeouts_give_up(self, fake_odds_api, odds_client):
        fake_odds_api.set_event_odds("basketball_nba", "nba-1", Raise(httpx.ReadTimeout("slow")))
        picks = await svc._fetch_event_props(
            odds_client, "key", "basketball_nba", "NBA", "nba-1", "player_points", {"player_points"}
        )
        assert picks == []
        assert fake_odds_api.count("/events/nba-1/odds") == 2

    async def test_connection_errors_are_not_retried(self, fake_odds_api, odds_client):
        fake_odds_api.set_event_odds("basketball_nba", "nba-1", Raise(httpx.ConnectError("dns")))
        picks = await svc._fetch_event_props(
            odds_client, "key", "basketball_nba", "NBA", "nba-1", "player_points", {"player_points"}
        )
        assert picks == []
        assert fake_odds_api.count("/events/nba-1/odds") == 1


class TestFetchPropSport:
    async def test_lists_every_game_but_only_prices_upcoming_ones(
        self, monkeypatch, fake_odds_api, odds_client
    ):
        freeze_time(monkeypatch, NOW)
        fake_odds_api.set_events(
            "basketball_nba",
            [
                odds_event("started", commence_time="2026-06-15T17:00:00Z"),
                odds_event("upcoming", commence_time="2026-06-15T23:00:00Z"),
            ],
        )
        fake_odds_api.set_event_odds("basketball_nba", "upcoming", _two_book_props("upcoming"))

        picks, shells = await svc._fetch_prop_sport(
            odds_client,
            "key",
            "basketball_nba",
            "NBA",
            svc.NBA_PROP_MARKETS,
            svc._nba_market_set(),
        )
        assert {s["event_id"] for s in shells} == {"started", "upcoming"}
        assert fake_odds_api.count("/events/started/odds") == 0
        assert fake_odds_api.count("/events/upcoming/odds") == 1
        assert len(picks) == 1

    async def test_prop_fan_out_respects_the_event_cap(
        self, monkeypatch, fake_odds_api, odds_client
    ):
        freeze_time(monkeypatch, NOW)
        monkeypatch.setattr(client_mod, "MAX_PROP_EVENTS_PER_SPORT", 2)
        fake_odds_api.set_events(
            "basketball_nba",
            [odds_event(f"g{i}", commence_time=f"2026-06-15T{19 + i}:00:00Z") for i in range(5)],
        )
        _, shells = await svc._fetch_prop_sport(
            odds_client, "key", "basketball_nba", "NBA", "player_points", {"player_points"}
        )
        assert len(shells) == 5
        assert fake_odds_api.count("/odds") == 2

    async def test_empty_schedule_skips_prop_calls(self, monkeypatch, fake_odds_api, odds_client):
        freeze_time(monkeypatch, NOW)
        fake_odds_api.set_events("basketball_nba", [])
        picks, shells = await svc._fetch_prop_sport(
            odds_client, "key", "basketball_nba", "NBA", "player_points", {"player_points"}
        )
        assert (picks, shells) == ([], [])
        assert fake_odds_api.count("/events/") == 0

    async def test_all_games_started_still_returns_shells(
        self, monkeypatch, fake_odds_api, odds_client
    ):
        freeze_time(monkeypatch, NOW)
        fake_odds_api.set_events(
            "basketball_nba", [odds_event("done", commence_time="2026-06-15T15:00:00Z")]
        )
        picks, shells = await svc._fetch_prop_sport(
            odds_client, "key", "basketball_nba", "NBA", "player_points", {"player_points"}
        )
        assert picks == []
        assert [s["event_id"] for s in shells] == ["done"]

    async def test_one_failing_event_does_not_lose_the_others(
        self, monkeypatch, fake_odds_api, odds_client
    ):
        freeze_time(monkeypatch, NOW)
        fake_odds_api.set_events(
            "basketball_nba",
            [
                odds_event("good", commence_time="2026-06-15T20:00:00Z"),
                odds_event("bad", commence_time="2026-06-15T21:00:00Z"),
            ],
        )
        fake_odds_api.set_event_odds("basketball_nba", "good", _two_book_props("good"))
        fake_odds_api.set_event_odds("basketball_nba", "bad", Fail(500))

        picks, shells = await svc._fetch_prop_sport(
            odds_client,
            "key",
            "basketball_nba",
            "NBA",
            svc.NBA_PROP_MARKETS,
            svc._nba_market_set(),
        )
        assert [p.event_id for p in picks] == ["good"]
        assert len(shells) == 2

    async def test_schedule_failure_yields_nothing(self, monkeypatch, fake_odds_api, odds_client):
        freeze_time(monkeypatch, NOW)
        fake_odds_api.set_events("basketball_nba", Fail(429))
        quota: list[str] = []
        picks, shells = await svc._fetch_prop_sport(
            odds_client,
            "key",
            "basketball_nba",
            "NBA",
            "player_points",
            {"player_points"},
            quota_events=quota,
        )
        assert (picks, shells) == ([], [])
        assert quota == ["rate_limit"]


class TestQuotaWarningCopy:
    def test_no_quota_events_means_no_warning(self):
        assert svc._odds_api_warning_message([], has_usable_response=True) is None

    def test_rate_limit_without_data_is_stated_plainly(self):
        message = svc._odds_api_warning_message(["rate_limit"], has_usable_response=False)
        assert "429" in message
        assert "MAX_PROP_EVENTS_PER_SPORT" in message

    def test_rate_limit_with_partial_data_is_softened(self):
        message = svc._odds_api_warning_message(["rate_limit"], has_usable_response=True)
        assert "part of the slate may be" in message

    def test_payment_required_without_data_points_at_billing(self):
        message = svc._odds_api_warning_message(["payment_required"], has_usable_response=False)
        assert "402" in message
        assert "plan" in message

    def test_payment_required_with_partial_data_is_softened(self):
        message = svc._odds_api_warning_message(["payment_required"], has_usable_response=True)
        assert "incomplete" in message

    def test_rate_limit_wins_when_both_occurred(self):
        message = svc._odds_api_warning_message(
            ["payment_required", "rate_limit"], has_usable_response=False
        )
        assert "429" in message

    def test_unknown_quota_events_produce_no_warning(self):
        assert svc._odds_api_warning_message(["something_else"], has_usable_response=True) is None

    @pytest.mark.parametrize(
        ("status", "expected"),
        [(429, ["rate_limit"]), (402, ["payment_required"]), (500, []), (200, [])],
    )
    def test_only_quota_statuses_are_recorded(self, status, expected):
        events: list[str] = []
        svc._note_odds_api_quota_issue(events, status)
        assert events == expected

    def test_recording_is_a_no_op_without_a_collector(self):
        svc._note_odds_api_quota_issue(None, 429)
