"""Verify that the refactored modules are independently importable and have the expected boundaries.

These tests document the module split described in the architecture docs:
- models: domain types only — no I/O, no httpx, no sports-specific logic
- sports: sport/provider configuration — no I/O beyond os.environ
- normalization: time and event-ID utilities — no HTTP, no ranking
- parser: payload → BetPick conversion — no HTTP
- ranking: scoring and grouping — no HTTP, no I/O
- demo: static fallback picks — no HTTP
- client: HTTP requests only — no ranking, no demo
- service: orchestration and re-exports
"""

from __future__ import annotations

import importlib
import inspect
import sys
import types
from datetime import date, timezone, datetime

import pytest


# ---------------------------------------------------------------------------
# Helper: check that a module does NOT import a given top-level module
# ---------------------------------------------------------------------------

def _direct_imports(module: types.ModuleType) -> set[str]:
    """Top-level module names directly referenced from the given module's source."""
    src = inspect.getsource(module)
    names: set[str] = set()
    for line in src.splitlines():
        stripped = line.strip()
        if stripped.startswith("import "):
            # "import httpx" → "httpx"; "import os" → "os"
            for part in stripped[len("import "):].split(","):
                names.add(part.strip().split(".")[0].split(" as ")[0])
        elif stripped.startswith("from "):
            # "from services.odds.models import ..." → "services"
            pkg = stripped[len("from "):].split(" import")[0].strip()
            names.add(pkg.split(".")[0])
    return names


# ---------------------------------------------------------------------------
# Module isolation checks
# ---------------------------------------------------------------------------

class TestModelsIsolation:
    """models.py must be a pure data module — no HTTP, no I/O beyond stdlib dataclass."""

    def test_models_module_imports(self):
        from services.odds import models
        imports = _direct_imports(models)
        assert "httpx" not in imports, "models must not import httpx"
        assert "asyncio" not in imports, "models must not import asyncio"
        assert "os" not in imports, "models must not import os (no env reads)"

    def test_betpick_is_frozen_dataclass(self):
        from services.odds.models import BetPick
        import dataclasses
        assert dataclasses.is_dataclass(BetPick)
        assert BetPick.__dataclass_params__.frozen  # type: ignore[attr-defined]

    def test_constants_are_module_level(self):
        from services.odds import models
        assert hasattr(models, "MIN_IMPLIED_PROBABILITY")
        assert hasattr(models, "RELAXED_IMPLIED_PROBABILITY")
        assert hasattr(models, "PICKS_PER_GAME")
        assert hasattr(models, "ALLOWED_BOOKMAKER_KEYS")

    def test_relaxed_is_strictly_less_than_primary(self):
        from services.odds.models import MIN_IMPLIED_PROBABILITY, RELAXED_IMPLIED_PROBABILITY
        assert RELAXED_IMPLIED_PROBABILITY < MIN_IMPLIED_PROBABILITY

    def test_allowed_bookmaker_keys_is_frozenset(self):
        from services.odds.models import ALLOWED_BOOKMAKER_KEYS
        assert isinstance(ALLOWED_BOOKMAKER_KEYS, frozenset)
        assert {"draftkings", "fanduel", "fanatics"} == ALLOWED_BOOKMAKER_KEYS


class TestSportsIsolation:
    """sports.py must not import from httpx, asyncio, or any sibling odds module."""

    def test_sports_module_imports(self):
        from services.odds import sports
        imports = _direct_imports(sports)
        assert "httpx" not in imports
        assert "asyncio" not in imports

    def test_all_soccer_keys_have_display_titles(self):
        from services.odds.sports import SOCCER_SPORT_KEYS, _sport_titles
        titles = _sport_titles()
        missing = [k for k in SOCCER_SPORT_KEYS if k not in titles]
        assert missing == [], f"Missing display titles for: {missing}"

    def test_nba_and_nfl_market_sets_are_disjoint(self):
        from services.odds.sports import _nba_market_set, _nfl_market_set
        assert _nba_market_set().isdisjoint(_nfl_market_set())

    def test_market_sets_match_csv_strings(self):
        from services.odds.sports import NBA_PROP_MARKETS, NFL_PROP_MARKETS, _nba_market_set, _nfl_market_set
        nba_from_csv = {m for m in NBA_PROP_MARKETS.replace("\n", "").split(",") if m.strip()}
        nfl_from_csv = {m for m in NFL_PROP_MARKETS.replace("\n", "").split(",") if m.strip()}
        assert _nba_market_set() == nba_from_csv
        assert _nfl_market_set() == nfl_from_csv

    def test_supported_sport_key_covers_all_soccer_keys(self):
        from services.odds.sports import SOCCER_SPORT_KEYS, supported_sport_key
        for sk in SOCCER_SPORT_KEYS:
            assert supported_sport_key(sk), f"{sk} should be supported"

    @pytest.mark.parametrize("sk", ["basketball_nba", "americanfootball_nfl"])
    def test_supported_sport_key_includes_prop_sports(self, sk):
        from services.odds.sports import supported_sport_key
        assert supported_sport_key(sk)

    def test_unknown_sport_key_is_not_supported(self):
        from services.odds.sports import supported_sport_key
        assert not supported_sport_key("icehockey_nhl")

    def test_get_api_key_returns_none_without_env_var(self, monkeypatch):
        monkeypatch.delenv("THE_ODDS_API_KEY", raising=False)
        from services.odds.sports import get_api_key
        assert get_api_key() is None

    def test_get_api_key_strips_whitespace(self, monkeypatch):
        monkeypatch.setenv("THE_ODDS_API_KEY", "  my-key  ")
        from services.odds.sports import get_api_key
        assert get_api_key() == "my-key"


class TestNormalizationIsolation:
    """normalization.py must not import from httpx, asyncio, ranking, parser, or client."""

    def test_normalization_module_imports(self):
        from services.odds import normalization
        imports = _direct_imports(normalization)
        assert "httpx" not in imports
        assert "asyncio" not in imports

    def test_no_import_from_ranking_parser_or_client(self):
        from services.odds import normalization
        src = inspect.getsource(normalization)
        for forbidden in ("services.odds.ranking", "services.odds.parser", "services.odds.client"):
            assert forbidden not in src, f"normalization must not import from {forbidden}"

    def test_implied_probability_is_exported(self):
        from services.odds.normalization import implied_probability
        assert callable(implied_probability)

    def test_local_day_bounds_returns_aware_datetimes(self):
        from services.odds.normalization import local_day_bounds_utc
        start, end = local_day_bounds_utc(date(2026, 6, 15), "America/New_York")
        assert start.tzinfo is not None
        assert end.tzinfo is not None
        assert end > start

    def test_normalize_event_id_collapses_numeric_forms(self):
        from services.odds.normalization import _normalize_event_id
        assert _normalize_event_id(7) == _normalize_event_id(7.0) == _normalize_event_id("7") == "7"

    def test_commence_time_utc_parses_z_suffix(self):
        from services.odds.normalization import _commence_time_utc
        result = _commence_time_utc("2026-06-15T19:00:00Z")
        assert result is not None
        assert result.tzinfo is not None
        assert result.utcoffset().total_seconds() == 0  # type: ignore[union-attr]

    def test_shells_from_scheduled_events_sorted_by_kickoff(self):
        from services.odds.normalization import shells_from_scheduled_events
        events = [
            {"id": "late", "commence_time": "2026-06-15T23:00:00Z"},
            {"id": "early", "commence_time": "2026-06-15T17:00:00Z"},
        ]
        shells = shells_from_scheduled_events(events, "basketball_nba", "NBA")
        assert [s["event_id"] for s in shells] == ["early", "late"]

    def test_filter_picks_by_game_day_requires_betpick(self):
        from services.odds.normalization import filter_picks_by_game_day
        from services.odds.models import BetPick
        # Just check it accepts BetPick and returns a list
        result = filter_picks_by_game_day([], date.today(), "UTC")
        assert result == []


class TestParserIsolation:
    """parser.py must not import httpx or asyncio; it converts payloads into BetPick rows."""

    def test_parser_module_imports(self):
        from services.odds import parser
        imports = _direct_imports(parser)
        assert "httpx" not in imports
        assert "asyncio" not in imports

    def test_no_import_from_client_or_ranking(self):
        from services.odds import parser
        src = inspect.getsource(parser)
        for forbidden in ("services.odds.client", "services.odds.ranking"):
            assert forbidden not in src, f"parser must not import from {forbidden}"

    def test_h2h_events_to_picks_returns_betpick_rows(self):
        from services.odds.parser import _h2h_events_to_picks
        from services.odds.models import BetPick
        event = {
            "id": "test-1",
            "home_team": "Arsenal",
            "away_team": "Chelsea",
            "commence_time": "2026-06-15T19:00:00Z",
            "bookmakers": [
                {
                    "key": "draftkings",
                    "title": "DraftKings",
                    "markets": [{"key": "h2h", "outcomes": [{"name": "Arsenal", "price": 1.85}]}],
                },
                {
                    "key": "fanduel",
                    "title": "FanDuel",
                    "markets": [{"key": "h2h", "outcomes": [{"name": "Arsenal", "price": 1.80}]}],
                },
            ],
        }
        picks = _h2h_events_to_picks([event], "soccer_epl", "EPL")
        assert len(picks) == 1
        assert isinstance(picks[0], BetPick)
        assert picks[0].market_key == "h2h"
        assert picks[0].pick == "Arsenal"

    def test_prop_event_to_picks_returns_labelled_pick(self):
        from services.odds.parser import _prop_event_to_picks
        from services.odds.models import BetPick
        event = {
            "id": "nba-1",
            "home_team": "Boston Celtics",
            "away_team": "Milwaukee Bucks",
            "commence_time": "2026-06-15T23:00:00Z",
            "bookmakers": [
                {
                    "key": "draftkings",
                    "title": "DraftKings",
                    "markets": [
                        {
                            "key": "player_points",
                            "outcomes": [
                                {"name": "Over", "price": 1.85, "description": "Jayson Tatum", "point": 27.5}
                            ],
                        }
                    ],
                },
                {
                    "key": "fanduel",
                    "title": "FanDuel",
                    "markets": [
                        {
                            "key": "player_points",
                            "outcomes": [
                                {"name": "Over", "price": 1.80, "description": "Jayson Tatum", "point": 27.5}
                            ],
                        }
                    ],
                },
            ],
        }
        picks = _prop_event_to_picks(event, "basketball_nba", "NBA", {"player_points"})
        assert len(picks) == 1
        assert isinstance(picks[0], BetPick)
        assert picks[0].pick == "PTS · Jayson Tatum — Over 27.5"

    def test_book_allowed_uses_models_allowlist(self):
        from services.odds.parser import _book_allowed
        assert _book_allowed({"key": "draftkings"}) is True
        assert _book_allowed({"key": "betmgm"}) is False

    def test_edge_requires_two_books_minimum(self):
        from services.odds.parser import _h2h_events_to_picks
        event = {
            "id": "test-1",
            "bookmakers": [
                {
                    "key": "draftkings",
                    "title": "DraftKings",
                    "markets": [{"key": "h2h", "outcomes": [{"name": "Arsenal", "price": 1.85}]}],
                },
            ],
        }
        assert _h2h_events_to_picks([event], "soccer_epl", "EPL") == []


class TestRankingIsolation:
    """ranking.py must not import httpx, asyncio, or the client module."""

    def test_ranking_module_imports(self):
        from services.odds import ranking
        imports = _direct_imports(ranking)
        assert "httpx" not in imports
        assert "asyncio" not in imports

    def test_no_import_from_client_or_parser(self):
        from services.odds import ranking
        src = inspect.getsource(ranking)
        for forbidden in ("services.odds.client", "services.odds.parser"):
            assert forbidden not in src, f"ranking must not import from {forbidden}"

    def test_rank_score_blends_implied_and_edge(self):
        from services.odds.ranking import rank_score
        from tests.helpers import make_pick
        p = make_pick(implied_probability=0.60, edge_pct=4.0)
        assert rank_score(p) == pytest.approx(0.55 * 60.0 + 0.45 * 4.0)

    def test_group_picks_into_games_applies_implied_floor(self):
        from services.odds.ranking import group_picks_into_games
        from services.odds.models import MIN_IMPLIED_PROBABILITY
        from tests.helpers import make_pick
        above = make_pick(pick="above", implied_probability=MIN_IMPLIED_PROBABILITY)
        below = make_pick(pick="below", implied_probability=MIN_IMPLIED_PROBABILITY - 0.01)
        games = group_picks_into_games([above, below])
        assert len(games) == 1
        assert games[0]["picks"][0]["pick"] == "above"

    def test_picks_to_json_includes_rank_score(self):
        from services.odds.ranking import picks_to_json
        from tests.helpers import make_pick
        result = picks_to_json([make_pick()])[0]
        assert "rank_score" in result
        assert "implied_pct" in result

    def test_group_with_implied_fallback_uses_relaxed_on_empty(self):
        from services.odds.ranking import _group_with_implied_fallback
        from services.odds.models import RELAXED_IMPLIED_PROBABILITY, MIN_IMPLIED_PROBABILITY
        from tests.helpers import make_pick
        picks = [make_pick(implied_probability=RELAXED_IMPLIED_PROBABILITY)]
        games, floor, used_fallback = _group_with_implied_fallback(picks, 3, None)
        assert len(games) == 1
        assert floor == RELAXED_IMPLIED_PROBABILITY
        assert used_fallback is True


class TestDemoIsolation:
    """demo.py must not import httpx or asyncio; it provides static fallback picks."""

    def test_demo_module_imports(self):
        from services.odds import demo
        imports = _direct_imports(demo)
        assert "httpx" not in imports
        assert "asyncio" not in imports

    def test_no_import_from_client(self):
        from services.odds import demo
        src = inspect.getsource(demo)
        assert "services.odds.client" not in src

    def test_demo_picks_returns_betpick_rows(self):
        from services.odds.demo import demo_picks
        from services.odds.models import BetPick
        picks = demo_picks(date(2030, 1, 15), "America/New_York")
        assert len(picks) > 0
        assert all(isinstance(p, BetPick) for p in picks)

    def test_demo_picks_cover_multiple_sports(self):
        from services.odds.demo import demo_picks
        picks = demo_picks(date(2030, 1, 15), "UTC")
        sports = {p.sport_key for p in picks}
        assert "basketball_nba" in sports
        assert "americanfootball_nfl" in sports
        assert any(s.startswith("soccer_") for s in sports)

    def test_demo_picks_have_valid_implied_probability(self):
        from services.odds.demo import demo_picks
        picks = demo_picks(date(2030, 1, 15), "UTC")
        for p in picks:
            assert 0.0 < p.implied_probability <= 1.0, f"Bad implied_probability on {p.pick}"


class TestClientIsolation:
    """client.py handles HTTP only — no ranking, no demo picks."""

    def test_client_module_imports_httpx_and_asyncio(self):
        from services.odds import client
        imports = _direct_imports(client)
        assert "httpx" in imports
        assert "asyncio" in imports

    def test_no_import_from_ranking_or_demo(self):
        from services.odds import client
        src = inspect.getsource(client)
        for forbidden in ("services.odds.ranking", "services.odds.demo"):
            assert forbidden not in src, f"client must not import from {forbidden}"

    def test_quota_constants_defined_in_client(self):
        from services.odds.client import ODDS_BASE, MAX_PROP_EVENTS_PER_SPORT
        assert ODDS_BASE.startswith("https://")
        assert isinstance(MAX_PROP_EVENTS_PER_SPORT, int)

    def test_note_quota_issue_records_rate_limit(self):
        from services.odds.client import _note_odds_api_quota_issue
        events: list[str] = []
        _note_odds_api_quota_issue(events, 429)
        assert events == ["rate_limit"]

    def test_note_quota_issue_records_payment_required(self):
        from services.odds.client import _note_odds_api_quota_issue
        events: list[str] = []
        _note_odds_api_quota_issue(events, 402)
        assert events == ["payment_required"]

    def test_note_quota_issue_ignores_other_codes(self):
        from services.odds.client import _note_odds_api_quota_issue
        events: list[str] = []
        _note_odds_api_quota_issue(events, 500)
        assert events == []

    def test_warning_message_is_none_without_quota_events(self):
        from services.odds.client import _odds_api_warning_message
        assert _odds_api_warning_message([], has_usable_response=True) is None

    def test_warning_softened_when_partial_data_available(self):
        from services.odds.client import _odds_api_warning_message
        msg_partial = _odds_api_warning_message(["rate_limit"], has_usable_response=True)
        msg_none = _odds_api_warning_message(["rate_limit"], has_usable_response=False)
        assert msg_partial is not None
        assert msg_none is not None
        assert "part of the slate" in msg_partial
        assert "429" in msg_none

    async def test_fetch_bulk_h2h_and_fetch_events_are_async(self):
        import inspect as _inspect
        from services.odds.client import _fetch_bulk_h2h, _fetch_events, _fetch_event_props, _fetch_prop_sport
        for fn in (_fetch_bulk_h2h, _fetch_events, _fetch_event_props, _fetch_prop_sport):
            assert _inspect.iscoroutinefunction(fn), f"{fn.__name__} must be async"


class TestServiceOrchestration:
    """service.py must re-export every public symbol from the split modules."""

    def test_all_public_init_exports_available_on_service(self):
        from services.odds import service
        public = [
            "BetPick", "fetch_best_picks", "fetch_picks_for_event",
            "get_api_key", "group_picks_into_games", "implied_probability",
            "picks_to_json", "rank_score", "supported_sport_key",
            "MIN_IMPLIED_PROBABILITY", "PICKS_PER_GAME", "RELAXED_IMPLIED_PROBABILITY",
        ]
        for name in public:
            assert hasattr(service, name), f"service must re-export {name}"

    def test_fetch_best_picks_is_async(self):
        import inspect as _inspect
        from services.odds.service import fetch_best_picks, fetch_picks_for_event
        assert _inspect.iscoroutinefunction(fetch_best_picks)
        assert _inspect.iscoroutinefunction(fetch_picks_for_event)

    def test_service_re_exports_client_symbols(self):
        from services.odds import service
        for name in ("_fetch_bulk_h2h", "_fetch_events", "_fetch_event_props",
                      "_fetch_prop_sport", "_odds_api_warning_message",
                      "_note_odds_api_quota_issue", "MAX_PROP_EVENTS_PER_SPORT", "ODDS_BASE"):
            assert hasattr(service, name), f"service must re-export {name}"

    def test_service_re_exports_normalization_symbols(self):
        from services.odds import service
        for name in ("_normalize_event_id", "_commence_time_utc", "_utc_iso_z",
                      "shells_from_scheduled_events", "filter_picks_by_game_day",
                      "local_day_bounds_utc"):
            assert hasattr(service, name), f"service must re-export {name}"

    def test_service_re_exports_sports_symbols(self):
        from services.odds import service
        for name in ("SOCCER_SPORT_KEYS", "NBA_PROP_MARKETS", "NFL_PROP_MARKETS",
                      "_nba_market_set", "_nfl_market_set", "_sport_titles"):
            assert hasattr(service, name), f"service must re-export {name}"


class TestDependencyFlow:
    """Verify that the import graph is a DAG with no upward or cross-layer dependencies."""

    def test_models_does_not_import_any_sibling(self):
        from services.odds import models
        src = inspect.getsource(models)
        for sibling in ("sports", "normalization", "parser", "ranking", "demo", "client", "service"):
            assert f"services.odds.{sibling}" not in src

    def test_sports_does_not_import_any_sibling(self):
        from services.odds import sports
        src = inspect.getsource(sports)
        for sibling in ("models", "normalization", "parser", "ranking", "demo", "client", "service"):
            assert f"services.odds.{sibling}" not in src, f"sports must not import {sibling}"

    def test_normalization_only_imports_models(self):
        from services.odds import normalization
        src = inspect.getsource(normalization)
        for sibling in ("sports", "parser", "ranking", "demo", "client", "service"):
            assert f"services.odds.{sibling}" not in src, f"normalization must not import {sibling}"

    def test_parser_only_imports_models_normalization_sports(self):
        from services.odds import parser
        src = inspect.getsource(parser)
        for forbidden in ("ranking", "demo", "client", "service"):
            assert f"services.odds.{forbidden}" not in src, f"parser must not import {forbidden}"

    def test_ranking_only_imports_models_normalization(self):
        from services.odds import ranking
        src = inspect.getsource(ranking)
        for forbidden in ("sports", "parser", "demo", "client", "service"):
            assert f"services.odds.{forbidden}" not in src, f"ranking must not import {forbidden}"

    def test_demo_does_not_import_client_or_service(self):
        from services.odds import demo
        src = inspect.getsource(demo)
        for forbidden in ("client", "service", "parser", "ranking"):
            assert f"services.odds.{forbidden}" not in src, f"demo must not import {forbidden}"

    def test_client_does_not_import_ranking_demo_or_service(self):
        from services.odds import client
        src = inspect.getsource(client)
        for forbidden in ("ranking", "demo", "service"):
            assert f"services.odds.{forbidden}" not in src, f"client must not import {forbidden}"
