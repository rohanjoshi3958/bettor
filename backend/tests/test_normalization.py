"""Odds normalization and parsing primitives."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from services.odds import service as svc


class TestNormalizeEventId:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            (None, ""),
            ("", ""),
            ("   ", ""),
            ("abc-123", "abc-123"),
            ("  abc-123  ", "abc-123"),
            (42, "42"),
            ("42", "42"),
            (42.0, "42"),
            ("42.0", "42"),
            (42.5, "42.5"),
            ("1e3", "1000"),
            (True, "true"),
            (False, "false"),
        ],
    )
    def test_normalizes_to_join_safe_string(self, raw, expected):
        assert svc._normalize_event_id(raw) == expected

    def test_int_float_and_string_forms_of_one_id_collapse(self):
        """Schedule shells and pick rows must key identically or games lose their picks."""
        assert (
            svc._normalize_event_id(7)
            == svc._normalize_event_id(7.0)
            == svc._normalize_event_id("7")
            == svc._normalize_event_id("7.0")
            == "7"
        )

    def test_non_numeric_float_string_is_left_alone(self):
        assert svc._normalize_event_id("12.5abc") == "12.5abc"


class TestImpliedProbability:
    @pytest.mark.parametrize(
        ("decimal_odds", "expected"),
        [
            (2.0, 0.5),
            (4.0, 0.25),
            (1.25, 0.8),
            (1.62, pytest.approx(0.617284, abs=1e-6)),
        ],
    )
    def test_is_reciprocal_of_decimal_price(self, decimal_odds, expected):
        assert svc.implied_probability(decimal_odds) == expected

    @pytest.mark.parametrize("bad_odds", [1.0, 0.9, 0.0, -3.0])
    def test_non_payout_prices_are_zero(self, bad_odds):
        assert svc.implied_probability(bad_odds) == 0.0

    def test_never_exceeds_one(self):
        assert svc.implied_probability(1.0000000001) <= 1.0

    def test_shorter_price_means_higher_implied_probability(self):
        assert svc.implied_probability(1.5) > svc.implied_probability(1.9)


class TestFloatPoint:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            (None, None),
            (27.5, 27.5),
            ("27.5", 27.5),
            (28, 28.0),
            ("nonsense", None),
            ([], None),
            (0, 0.0),
        ],
    )
    def test_coerces_prop_line_to_float(self, raw, expected):
        assert svc._float_point(raw) == expected

    def test_rounds_to_four_places(self):
        assert svc._float_point(27.5000049) == 27.5


class TestCommenceTimeParsing:
    def test_parses_trailing_z_as_utc(self):
        assert svc._commence_time_utc("2026-06-15T19:00:00Z") == datetime(
            2026, 6, 15, 19, 0, tzinfo=timezone.utc
        )

    def test_converts_offset_timestamps_to_utc(self):
        assert svc._commence_time_utc("2026-06-15T15:00:00-04:00") == datetime(
            2026, 6, 15, 19, 0, tzinfo=timezone.utc
        )

    def test_naive_timestamps_are_assumed_utc(self):
        assert svc._commence_time_utc("2026-06-15T19:00:00") == datetime(
            2026, 6, 15, 19, 0, tzinfo=timezone.utc
        )

    @pytest.mark.parametrize(
        "raw", ["", "tomorrow", "2026-13-45T99:99:99Z", None, 1781000000, {"at": "now"}]
    )
    def test_unparseable_values_return_none(self, raw):
        """Malformed upstream timestamps must degrade to "unknown", never raise."""
        assert svc._commence_time_utc(raw) is None

    def test_utc_iso_z_round_trips(self):
        dt = datetime(2026, 6, 15, 19, 0, tzinfo=timezone.utc)
        assert svc._utc_iso_z(dt) == "2026-06-15T19:00:00Z"

    def test_utc_iso_z_normalizes_offsets_and_naive_input(self):
        assert svc._utc_iso_z(datetime(2026, 6, 15, 19, 0)) == "2026-06-15T19:00:00Z"
        parsed = svc._commence_time_utc("2026-06-15T15:00:00-04:00")
        assert svc._utc_iso_z(parsed) == "2026-06-15T19:00:00Z"


class TestPropLabels:
    @pytest.mark.parametrize(
        ("market_key", "expected_prefix"),
        [
            ("player_points", "PTS"),
            ("player_points_rebounds_assists", "PRA"),
            ("player_anytime_td", "Anytime TD"),
        ],
    )
    def test_known_markets_use_short_labels(self, market_key, expected_prefix):
        label = svc._format_prop_pick(market_key, "Jayson Tatum", "Over", 27.5)
        assert label == f"{expected_prefix} · Jayson Tatum — Over 27.5"

    def test_unknown_market_falls_back_to_titleized_key(self):
        assert svc._format_prop_pick("player_blocks", "Bam Adebayo", "Over", 1.5) == (
            "Player Blocks · Bam Adebayo — Over 1.5"
        )

    def test_pointless_markets_omit_the_line(self):
        assert svc._format_prop_pick("player_anytime_td", "Travis Kelce", "Yes", None) == (
            "Anytime TD · Travis Kelce — Yes"
        )

    def test_outcome_key_groups_the_same_prop_across_books(self):
        dk = {"description": " Jayson Tatum ", "name": "Over ", "point": "27.5"}
        fd = {"description": "Jayson Tatum", "name": "Over", "point": 27.5}
        assert svc._prop_outcome_key("player_points", dk) == svc._prop_outcome_key(
            "player_points", fd
        )

    def test_outcome_key_separates_over_from_under_and_different_lines(self):
        base = {"description": "Jayson Tatum", "name": "Over", "point": 27.5}
        under = {**base, "name": "Under"}
        other_line = {**base, "point": 28.5}
        keys = {
            svc._prop_outcome_key("player_points", base),
            svc._prop_outcome_key("player_points", under),
            svc._prop_outcome_key("player_points", other_line),
        }
        assert len(keys) == 3


class TestBookmakerAllowlist:
    @pytest.mark.parametrize("key", ["draftkings", "fanduel", "fanatics"])
    def test_supported_books_are_allowed(self, key):
        assert svc._book_allowed({"key": key}) is True

    @pytest.mark.parametrize("book", [{"key": "betmgm"}, {"key": None}, {}, {"key": 7}])
    def test_everything_else_is_rejected(self, book):
        assert svc._book_allowed(book) is False


class TestSupportedSportKeys:
    @pytest.mark.parametrize(
        "sport_key",
        ["basketball_nba", "americanfootball_nfl", "soccer_epl", "soccer_fifa_world_cup"],
    )
    def test_known_leagues_are_supported(self, sport_key):
        assert svc.supported_sport_key(sport_key) is True

    @pytest.mark.parametrize("sport_key", ["", "icehockey_nhl", "basketball_NBA", "soccer"])
    def test_unknown_leagues_are_rejected(self, sport_key):
        assert svc.supported_sport_key(sport_key) is False

    def test_every_soccer_key_has_a_display_title(self):
        titles = svc._sport_titles()
        missing = [k for k in svc.SOCCER_SPORT_KEYS if k not in titles]
        assert missing == []


class TestApiKeyResolution:
    def test_missing_env_var_means_demo_mode(self, monkeypatch):
        monkeypatch.delenv("THE_ODDS_API_KEY", raising=False)
        assert svc.get_api_key() is None

    @pytest.mark.parametrize("raw", ["", "   ", "# paste your key here"])
    def test_blank_or_commented_values_mean_demo_mode(self, monkeypatch, raw):
        monkeypatch.setenv("THE_ODDS_API_KEY", raw)
        assert svc.get_api_key() is None

    def test_surrounding_whitespace_is_trimmed(self, monkeypatch):
        monkeypatch.setenv("THE_ODDS_API_KEY", "  live-key  ")
        assert svc.get_api_key() == "live-key"
