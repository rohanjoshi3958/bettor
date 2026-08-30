"""Turning raw Odds API bookmaker payloads into `BetPick` rows (line shopping + edge)."""

from __future__ import annotations

import pytest

from services.odds import service as svc
from tests.helpers import h2h_quotes, odds_event, quote


def _h2h_event(prices, **kwargs):
    return odds_event(
        "evt-1",
        home="Arsenal",
        away="Chelsea",
        commence_time="2026-06-15T19:00:00Z",
        quotes=h2h_quotes(prices),
        sport_key="soccer_epl",
        **kwargs,
    )


class TestCollectH2hPrices:
    def test_collects_one_price_per_book_per_outcome(self):
        event = _h2h_event(
            {
                "draftkings": {"Arsenal": 1.85, "Chelsea": 4.2},
                "fanduel": {"Arsenal": 1.80, "Chelsea": 4.4},
            }
        )
        prices = svc._collect_h2h_prices(event)
        assert sorted(prices) == ["Arsenal", "Chelsea"]
        assert sorted(prices["Arsenal"]) == [("Draftkings", 1.85), ("Fanduel", 1.80)]

    def test_ignores_books_outside_the_allowlist(self):
        event = _h2h_event({"betmgm": {"Arsenal": 1.85}, "draftkings": {"Arsenal": 1.80}})
        prices = svc._collect_h2h_prices(event)
        assert [book for book, _ in prices["Arsenal"]] == ["Draftkings"]

    def test_ignores_non_h2h_markets(self):
        event = odds_event(
            "evt-1",
            quotes=[
                quote("draftkings", "spreads", "Arsenal", 1.9, point=-1.5),
                quote("fanduel", "h2h", "Arsenal", 1.85),
            ],
        )
        assert svc._collect_h2h_prices(event) == {"Arsenal": [("Fanduel", 1.85)]}

    @pytest.mark.parametrize("price", [None, "not-a-number", [1.9]])
    def test_unusable_prices_are_skipped(self, price):
        event = odds_event("evt-1", quotes=[quote("draftkings", "h2h", "Arsenal", price)])
        assert svc._collect_h2h_prices(event) == {}

    @pytest.mark.parametrize("price", [1.0, 0.5, -2.0])
    def test_prices_that_cannot_pay_out_are_skipped(self, price):
        event = odds_event("evt-1", quotes=[quote("draftkings", "h2h", "Arsenal", price)])
        assert svc._collect_h2h_prices(event) == {}

    def test_numeric_string_prices_are_accepted(self):
        event = odds_event("evt-1", quotes=[quote("draftkings", "h2h", "Arsenal", "1.85")])
        assert svc._collect_h2h_prices(event) == {"Arsenal": [("Draftkings", 1.85)]}

    def test_outcomes_without_a_name_are_skipped(self):
        event = odds_event("evt-1", quotes=[quote("draftkings", "h2h", "", 1.85)])
        assert svc._collect_h2h_prices(event) == {}

    def test_book_title_falls_back_to_key_then_unknown(self):
        event = odds_event("evt-1", quotes=[quote("draftkings", "h2h", "Arsenal", 1.85)])
        event["bookmakers"][0].pop("title")
        assert svc._collect_h2h_prices(event) == {"Arsenal": [("draftkings", 1.85)]}

        event["bookmakers"][0]["title"] = None
        event["bookmakers"][0]["key_backup"] = event["bookmakers"][0]["key"]
        assert svc._collect_h2h_prices(event) == {"Arsenal": [("draftkings", 1.85)]}

    @pytest.mark.parametrize(
        "event",
        [
            {},
            {"bookmakers": None},
            {"bookmakers": []},
            {"bookmakers": [{"key": "draftkings", "markets": None}]},
            {"bookmakers": [{"key": "draftkings", "markets": [{"key": "h2h", "outcomes": None}]}]},
        ],
    )
    def test_missing_or_null_sections_do_not_raise(self, event):
        assert svc._collect_h2h_prices(event) == {}


class TestH2hEventsToPicks:
    def test_requires_at_least_two_books_for_an_outcome(self):
        event = _h2h_event({"draftkings": {"Arsenal": 1.85}})
        assert svc._h2h_events_to_picks([event], "soccer_epl", "EPL") == []

    def test_picks_the_best_price_and_measures_edge_against_the_mean(self):
        event = _h2h_event(
            {
                "draftkings": {"Arsenal": 1.90},
                "fanduel": {"Arsenal": 1.80},
                "fanatics": {"Arsenal": 1.70},
            }
        )
        pick = svc._h2h_events_to_picks([event], "soccer_epl", "EPL")[0]
        assert pick.best_decimal_odds == 1.9
        assert pick.best_book == "Draftkings"
        assert pick.avg_decimal_odds == 1.8
        assert pick.edge_pct == pytest.approx(5.56, abs=0.01)
        assert pick.implied_probability == pytest.approx(0.5263, abs=1e-4)

    def test_edge_is_zero_when_every_book_agrees(self):
        event = _h2h_event({"draftkings": {"Arsenal": 1.85}, "fanduel": {"Arsenal": 1.85}})
        pick = svc._h2h_events_to_picks([event], "soccer_epl", "EPL")[0]
        assert pick.edge_pct == 0.0

    def test_carries_event_identity_onto_every_pick(self):
        event = _h2h_event(
            {"draftkings": {"Arsenal": 1.85, "Chelsea": 4.2}, "fanduel": {"Arsenal": 1.80, "Chelsea": 4.4}}
        )
        picks = svc._h2h_events_to_picks([event], "soccer_epl", "EPL")
        assert len(picks) == 2
        for pick in picks:
            assert pick.sport_key == "soccer_epl"
            assert pick.sport_title == "EPL"
            assert pick.event_id == "evt-1"
            assert pick.market_key == "h2h"
            assert pick.home_team == "Arsenal"
            assert pick.away_team == "Chelsea"
            assert pick.commence_time == "2026-06-15T19:00:00Z"

    def test_normalizes_numeric_event_ids(self):
        event = _h2h_event({"draftkings": {"Arsenal": 1.85}, "fanduel": {"Arsenal": 1.80}})
        event["id"] = 1234.0
        pick = svc._h2h_events_to_picks([event], "soccer_epl", "EPL")[0]
        assert pick.event_id == "1234"

    def test_empty_and_bookmakerless_events_produce_nothing(self):
        assert svc._h2h_events_to_picks([], "soccer_epl", "EPL") == []
        assert svc._h2h_events_to_picks([{}], "soccer_epl", "EPL") == []

    def test_missing_team_names_degrade_to_empty_strings(self):
        event = _h2h_event({"draftkings": {"Arsenal": 1.85}, "fanduel": {"Arsenal": 1.80}})
        event.pop("home_team")
        event["away_team"] = None
        pick = svc._h2h_events_to_picks([event], "soccer_epl", "EPL")[0]
        assert (pick.home_team, pick.away_team) == ("", "")


class TestCollectPropPrices:
    def _prop_event(self, quotes):
        return odds_event(
            "nba-1",
            commence_time="2026-06-15T23:00:00Z",
            quotes=quotes,
        )

    def test_groups_the_same_prop_line_across_books(self):
        event = self._prop_event(
            [
                quote("draftkings", "player_points", "Over", 1.90, description="Jayson Tatum", point=27.5),
                quote("fanduel", "player_points", "Over", 1.80, description="Jayson Tatum", point=27.5),
            ]
        )
        grouped = svc._collect_prop_prices(event, {"player_points"})
        assert list(grouped) == [("player_points", "Jayson Tatum", "Over", 27.5)]
        assert len(grouped[("player_points", "Jayson Tatum", "Over", 27.5)]) == 2

    def test_different_lines_are_not_merged(self):
        event = self._prop_event(
            [
                quote("draftkings", "player_points", "Over", 1.90, description="Jayson Tatum", point=27.5),
                quote("fanduel", "player_points", "Over", 1.80, description="Jayson Tatum", point=28.5),
            ]
        )
        grouped = svc._collect_prop_prices(event, {"player_points"})
        assert len(grouped) == 2

    def test_markets_outside_the_requested_set_are_ignored(self):
        event = self._prop_event(
            [quote("draftkings", "player_blocks", "Over", 1.90, description="Bam Adebayo", point=1.5)]
        )
        assert svc._collect_prop_prices(event, {"player_points"}) == {}

    def test_prop_prices_respect_the_book_allowlist(self):
        event = self._prop_event(
            [quote("betmgm", "player_points", "Over", 1.90, description="Jayson Tatum", point=27.5)]
        )
        assert svc._collect_prop_prices(event, {"player_points"}) == {}

    @pytest.mark.parametrize("price", [None, "nope", 1.0])
    def test_unusable_prop_prices_are_skipped(self, price):
        event = self._prop_event(
            [quote("draftkings", "player_points", "Over", price, description="Jayson Tatum", point=27.5)]
        )
        assert svc._collect_prop_prices(event, {"player_points"}) == {}

    def test_markets_without_a_key_are_ignored(self):
        event = self._prop_event(
            [quote("draftkings", "player_points", "Over", 1.9, description="Jayson Tatum", point=27.5)]
        )
        event["bookmakers"][0]["markets"][0].pop("key")
        assert svc._collect_prop_prices(event, {"player_points"}) == {}


class TestPropEventToPicks:
    def _event(self, quotes, **kwargs):
        return odds_event("nba-1", commence_time="2026-06-15T23:00:00Z", quotes=quotes, **kwargs)

    def test_builds_a_labelled_pick_from_two_or_more_books(self):
        event = self._event(
            [
                quote("draftkings", "player_points", "Over", 1.90, description="Jayson Tatum", point=27.5),
                quote("fanduel", "player_points", "Over", 1.70, description="Jayson Tatum", point=27.5),
            ]
        )
        pick = svc._prop_event_to_picks(event, "basketball_nba", "NBA", {"player_points"})[0]
        assert pick.pick == "PTS · Jayson Tatum — Over 27.5"
        assert pick.market_key == "player_points"
        assert pick.best_decimal_odds == 1.9
        assert pick.avg_decimal_odds == 1.8
        assert pick.edge_pct == pytest.approx(5.56, abs=0.01)

    def test_single_book_props_are_dropped(self):
        event = self._event(
            [quote("draftkings", "player_points", "Over", 1.90, description="Jayson Tatum", point=27.5)]
        )
        assert svc._prop_event_to_picks(event, "basketball_nba", "NBA", {"player_points"}) == []

    def test_outcomes_with_neither_player_nor_side_are_dropped(self):
        event = self._event(
            [
                quote("draftkings", "player_points", "", 1.90, point=27.5),
                quote("fanduel", "player_points", "", 1.80, point=27.5),
            ]
        )
        assert svc._prop_event_to_picks(event, "basketball_nba", "NBA", {"player_points"}) == []

    def test_pointless_markets_still_produce_picks(self):
        event = self._event(
            [
                quote("draftkings", "player_anytime_td", "Yes", 1.85, description="Travis Kelce"),
                quote("fanduel", "player_anytime_td", "Yes", 1.80, description="Travis Kelce"),
            ],
            sport_key="americanfootball_nfl",
        )
        pick = svc._prop_event_to_picks(
            event, "americanfootball_nfl", "NFL", {"player_anytime_td"}
        )[0]
        assert pick.pick == "Anytime TD · Travis Kelce — Yes"

    def test_over_and_under_are_ranked_independently(self):
        event = self._event(
            [
                quote("draftkings", "player_points", "Over", 1.90, description="Jayson Tatum", point=27.5),
                quote("fanduel", "player_points", "Over", 1.80, description="Jayson Tatum", point=27.5),
                quote("draftkings", "player_points", "Under", 1.95, description="Jayson Tatum", point=27.5),
                quote("fanduel", "player_points", "Under", 1.85, description="Jayson Tatum", point=27.5),
            ]
        )
        picks = svc._prop_event_to_picks(event, "basketball_nba", "NBA", {"player_points"})
        assert {p.pick for p in picks} == {
            "PTS · Jayson Tatum — Over 27.5",
            "PTS · Jayson Tatum — Under 27.5",
        }

    def test_malformed_event_returns_no_picks(self):
        assert svc._prop_event_to_picks({}, "basketball_nba", "NBA", {"player_points"}) == []
        assert (
            svc._prop_event_to_picks(
                {"bookmakers": None}, "basketball_nba", "NBA", {"player_points"}
            )
            == []
        )

    def test_configured_market_sets_match_the_request_strings(self):
        """The CSV sent upstream and the set used to filter responses must not drift apart."""
        nba_csv = {m for m in svc.NBA_PROP_MARKETS.replace("\n", "").split(",")}
        nfl_csv = {m for m in svc.NFL_PROP_MARKETS.replace("\n", "").split(",")}
        assert svc._nba_market_set() == nba_csv
        assert svc._nfl_market_set() == nfl_csv
        assert svc._nba_market_set().isdisjoint(svc._nfl_market_set())
