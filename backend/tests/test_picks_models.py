"""Tests for the typed Pydantic API response models.

Covers:
- Representative NBA, NFL, and soccer pick/game shapes.
- Optional fields (market_key=None, odds_api_warning=None, game=None).
- Validation errors for missing required fields and bad types.
- Round-trip: service dicts produced by ``picks_to_json`` / ``group_picks_into_games``
  validate cleanly through the public models.
- HTTP integration: endpoint responses match the declared response_model schema.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from app.models import (
    GameModel,
    HealthResponse,
    PickModel,
    PicksGameResponse,
    PicksSlateResponse,
)
from services.odds.service import (
    MIN_IMPLIED_PROBABILITY,
    PICKS_PER_GAME,
    RELAXED_IMPLIED_PROBABILITY,
    group_picks_into_games,
    picks_to_json,
)
from tests.helpers import (
    freeze_time,
    h2h_quotes,
    make_pick,
    odds_event,
    quote,
)

NOW = datetime(2026, 6, 15, 18, 0, tzinfo=timezone.utc)
TODAY = "2026-06-15"
TZ = "America/New_York"

# ---------------------------------------------------------------------------
# Shared fixture helpers
# ---------------------------------------------------------------------------


def _nba_pick_dict(**overrides):
    base = {
        "sport_key": "basketball_nba",
        "sport_title": "NBA",
        "event_id": "nba-1",
        "matchup": "Milwaukee Bucks @ Boston Celtics",
        "home_team": "Boston Celtics",
        "away_team": "Milwaukee Bucks",
        "commence_time": "2026-06-15T23:00:00Z",
        "pick": "PTS · Jayson Tatum — Over 27.5",
        "market_key": "player_points",
        "implied_probability": 0.617,
        "implied_pct": 61.7,
        "rank_score": 52.3,
        "best_decimal_odds": 1.62,
        "best_book": "DraftKings",
        "avg_decimal_odds": 1.58,
        "edge_pct": 2.5,
    }
    base.update(overrides)
    return base


def _nfl_pick_dict(**overrides):
    base = {
        "sport_key": "americanfootball_nfl",
        "sport_title": "NFL",
        "event_id": "nfl-1",
        "matchup": "Buffalo Bills @ Kansas City Chiefs",
        "home_team": "Kansas City Chiefs",
        "away_team": "Buffalo Bills",
        "commence_time": "2026-06-15T20:00:00Z",
        "pick": "Pass Yds · Josh Allen — Over 274.5",
        "market_key": "player_pass_yds",
        "implied_probability": 0.633,
        "implied_pct": 63.3,
        "rank_score": 54.1,
        "best_decimal_odds": 1.58,
        "best_book": "FanDuel",
        "avg_decimal_odds": 1.54,
        "edge_pct": 2.6,
    }
    base.update(overrides)
    return base


def _soccer_pick_dict(**overrides):
    base = {
        "sport_key": "soccer_epl",
        "sport_title": "EPL",
        "event_id": "epl-1",
        "matchup": "Chelsea @ Arsenal",
        "home_team": "Arsenal",
        "away_team": "Chelsea",
        "commence_time": "2026-06-15T16:00:00Z",
        "pick": "Arsenal",
        "market_key": "h2h",
        "implied_probability": 0.645,
        "implied_pct": 64.5,
        "rank_score": 55.8,
        "best_decimal_odds": 1.55,
        "best_book": "Fanatics",
        "avg_decimal_odds": 1.51,
        "edge_pct": 2.6,
    }
    base.update(overrides)
    return base


def _meta_dict(**overrides):
    base = {
        "source": "demo",
        "pick_count": 3,
        "picks_per_game": 3,
        "game_date": TODAY,
        "timezone": TZ,
        "min_implied_probability": MIN_IMPLIED_PROBABILITY,
        "target_implied_probability": MIN_IMPLIED_PROBABILITY,
        "relaxed_implied_probability": RELAXED_IMPLIED_PROBABILITY,
        "used_relaxed_implied_fallback": False,
        "odds_api_warning": None,
    }
    base.update(overrides)
    return base


def _nba_game_dict(picks=None):
    return {
        "sport_key": "basketball_nba",
        "sport_title": "NBA",
        "event_id": "nba-1",
        "home_team": "Boston Celtics",
        "away_team": "Milwaukee Bucks",
        "commence_time": "2026-06-15T23:00:00Z",
        "matchup": "Milwaukee Bucks @ Boston Celtics",
        "picks": [_nba_pick_dict()] if picks is None else picks,
    }


# ---------------------------------------------------------------------------
# PickModel
# ---------------------------------------------------------------------------


class TestPickModel:
    def test_nba_prop_pick_validates(self):
        m = PickModel(**_nba_pick_dict())
        assert m.sport_key == "basketball_nba"
        assert m.market_key == "player_points"
        assert m.best_book == "DraftKings"

    def test_nfl_prop_pick_validates(self):
        m = PickModel(**_nfl_pick_dict())
        assert m.sport_key == "americanfootball_nfl"
        assert m.market_key == "player_pass_yds"

    def test_soccer_h2h_pick_validates(self):
        m = PickModel(**_soccer_pick_dict())
        assert m.sport_key == "soccer_epl"
        assert m.market_key == "h2h"
        assert m.pick == "Arsenal"

    def test_market_key_defaults_to_none(self):
        d = _nba_pick_dict()
        del d["market_key"]
        m = PickModel(**d)
        assert m.market_key is None

    def test_market_key_none_is_accepted_explicitly(self):
        m = PickModel(**_nba_pick_dict(market_key=None))
        assert m.market_key is None

    def test_missing_required_field_raises_validation_error(self):
        d = _nba_pick_dict()
        del d["pick"]
        with pytest.raises(ValidationError):
            PickModel(**d)

    def test_wrong_type_for_implied_probability_raises_validation_error(self):
        with pytest.raises(ValidationError):
            PickModel(**_nba_pick_dict(implied_probability="not-a-float"))

    def test_all_fields_are_present_in_model_dump(self):
        m = PickModel(**_nba_pick_dict())
        dumped = m.model_dump()
        expected = {
            "sport_key", "sport_title", "event_id", "matchup", "home_team", "away_team",
            "commence_time", "pick", "market_key", "implied_probability", "implied_pct",
            "rank_score", "best_decimal_odds", "best_book", "avg_decimal_odds", "edge_pct",
        }
        assert set(dumped) == expected


# ---------------------------------------------------------------------------
# GameModel
# ---------------------------------------------------------------------------


class TestGameModel:
    def test_nba_game_with_picks_validates(self):
        m = GameModel(**_nba_game_dict())
        assert m.sport_key == "basketball_nba"
        assert len(m.picks) == 1
        assert isinstance(m.picks[0], PickModel)

    def test_game_shell_with_empty_picks_validates(self):
        m = GameModel(**_nba_game_dict(picks=[]))
        assert m.picks == []

    def test_nested_pick_dicts_are_coerced_to_pick_models(self):
        game = _nba_game_dict(picks=[_nba_pick_dict(), _nfl_pick_dict()])
        m = GameModel(**game)
        assert all(isinstance(p, PickModel) for p in m.picks)

    def test_missing_required_field_raises_validation_error(self):
        d = _nba_game_dict()
        del d["event_id"]
        with pytest.raises(ValidationError):
            GameModel(**d)

    def test_game_fields_match_api_contract(self):
        m = GameModel(**_nba_game_dict())
        dumped = m.model_dump()
        assert set(dumped) >= {
            "sport_key", "sport_title", "event_id", "home_team", "away_team",
            "commence_time", "matchup", "picks",
        }


# ---------------------------------------------------------------------------
# PicksSlateResponse
# ---------------------------------------------------------------------------


class TestPicksSlateResponse:
    def test_minimal_slate_validates(self):
        m = PicksSlateResponse(
            **_meta_dict(),
            game_count=0,
            games=[],
        )
        assert m.game_count == 0
        assert m.games == []

    def test_slate_with_nba_game_validates(self):
        game = _nba_game_dict()
        m = PicksSlateResponse(
            **_meta_dict(pick_count=1),
            game_count=1,
            games=[game],
        )
        assert len(m.games) == 1
        assert isinstance(m.games[0], GameModel)

    def test_multiple_sport_games_validate(self):
        games = [
            _nba_game_dict(),
            {
                "sport_key": "americanfootball_nfl",
                "sport_title": "NFL",
                "event_id": "nfl-1",
                "home_team": "Kansas City Chiefs",
                "away_team": "Buffalo Bills",
                "commence_time": "2026-06-15T20:00:00Z",
                "matchup": "Buffalo Bills @ Kansas City Chiefs",
                "picks": [_nfl_pick_dict()],
            },
            {
                "sport_key": "soccer_epl",
                "sport_title": "EPL",
                "event_id": "epl-1",
                "home_team": "Arsenal",
                "away_team": "Chelsea",
                "commence_time": "2026-06-15T16:00:00Z",
                "matchup": "Chelsea @ Arsenal",
                "picks": [_soccer_pick_dict()],
            },
        ]
        m = PicksSlateResponse(
            **_meta_dict(pick_count=3),
            game_count=3,
            games=games,
        )
        sport_keys = {g.sport_key for g in m.games}
        assert sport_keys == {"basketball_nba", "americanfootball_nfl", "soccer_epl"}

    def test_odds_api_warning_defaults_to_none(self):
        m = PicksSlateResponse(**_meta_dict(), game_count=0, games=[])
        assert m.odds_api_warning is None

    def test_odds_api_warning_can_hold_a_message(self):
        m = PicksSlateResponse(
            **_meta_dict(odds_api_warning="rate limit hit"),
            game_count=0,
            games=[],
        )
        assert m.odds_api_warning == "rate limit hit"

    def test_slate_dump_contains_all_envelope_fields(self):
        m = PicksSlateResponse(**_meta_dict(), game_count=0, games=[])
        dumped = m.model_dump()
        assert set(dumped) == {
            "source", "game_count", "pick_count", "picks_per_game", "game_date",
            "timezone", "min_implied_probability", "target_implied_probability",
            "relaxed_implied_probability", "used_relaxed_implied_fallback",
            "odds_api_warning", "games",
        }

    def test_missing_game_count_raises_validation_error(self):
        d = {**_meta_dict(), "games": []}
        with pytest.raises(ValidationError):
            PicksSlateResponse(**d)


# ---------------------------------------------------------------------------
# PicksGameResponse
# ---------------------------------------------------------------------------


class TestPicksGameResponse:
    def test_response_with_a_game_validates(self):
        m = PicksGameResponse(
            **_meta_dict(pick_count=1),
            game=_nba_game_dict(),
        )
        assert isinstance(m.game, GameModel)

    def test_null_game_is_valid(self):
        m = PicksGameResponse(**_meta_dict(pick_count=0), game=None)
        assert m.game is None

    def test_game_defaults_to_none(self):
        m = PicksGameResponse(**_meta_dict(pick_count=0))
        assert m.game is None

    def test_response_dump_contains_all_envelope_fields(self):
        m = PicksGameResponse(**_meta_dict(pick_count=0))
        dumped = m.model_dump()
        assert set(dumped) == {
            "source", "game", "pick_count", "picks_per_game", "game_date",
            "timezone", "min_implied_probability", "target_implied_probability",
            "relaxed_implied_probability", "used_relaxed_implied_fallback",
            "odds_api_warning",
        }

    def test_nested_game_picks_are_validated(self):
        game = _nba_game_dict(picks=[_nba_pick_dict()])
        m = PicksGameResponse(**_meta_dict(pick_count=1), game=game)
        assert len(m.game.picks) == 1
        assert isinstance(m.game.picks[0], PickModel)


# ---------------------------------------------------------------------------
# HealthResponse
# ---------------------------------------------------------------------------


class TestHealthResponse:
    def test_demo_mode(self):
        m = HealthResponse(ok=True, live_odds=False)
        assert m.ok is True
        assert m.live_odds is False

    def test_live_mode(self):
        m = HealthResponse(ok=True, live_odds=True)
        assert m.live_odds is True


# ---------------------------------------------------------------------------
# Service round-trip: service dicts → Pydantic models
# ---------------------------------------------------------------------------


class TestServiceRoundTrip:
    """picks_to_json and group_picks_into_games output must validate through the public models."""

    def test_nba_picks_to_json_validates_through_pick_model(self):
        pick = make_pick(
            sport_key="basketball_nba",
            sport_title="NBA",
            market_key="player_points",
        )
        dicts = picks_to_json([pick])
        assert len(dicts) == 1
        m = PickModel(**dicts[0])
        assert m.market_key == "player_points"

    def test_nfl_picks_to_json_validates_through_pick_model(self):
        pick = make_pick(
            sport_key="americanfootball_nfl",
            sport_title="NFL",
            market_key="player_pass_yds",
            pick="Pass Yds · Josh Allen — Over 274.5",
        )
        dicts = picks_to_json([pick])
        m = PickModel(**dicts[0])
        assert m.sport_key == "americanfootball_nfl"
        assert m.market_key == "player_pass_yds"

    def test_soccer_h2h_pick_market_key_is_h2h(self):
        pick = make_pick(
            sport_key="soccer_epl",
            sport_title="EPL",
            market_key="h2h",
            pick="Arsenal",
        )
        dicts = picks_to_json([pick])
        m = PickModel(**dicts[0])
        assert m.market_key == "h2h"

    def test_pick_with_null_market_key_round_trips(self):
        pick = make_pick(market_key=None)
        dicts = picks_to_json([pick])
        m = PickModel(**dicts[0])
        assert m.market_key is None

    def test_group_picks_into_games_validates_through_game_model(self):
        picks = [
            make_pick(event_id="e1", implied_probability=0.60),
            make_pick(event_id="e1", market_key="player_rebounds", implied_probability=0.58),
        ]
        game_dicts = group_picks_into_games(picks, picks_per_game=3)
        assert len(game_dicts) == 1
        m = GameModel(**game_dicts[0])
        assert len(m.picks) == 2
        assert all(isinstance(p, PickModel) for p in m.picks)

    def test_game_shell_with_no_picks_validates(self):
        game_dict = {
            "sport_key": "soccer_epl",
            "sport_title": "EPL",
            "event_id": "epl-shell",
            "home_team": "Arsenal",
            "away_team": "Chelsea",
            "commence_time": "2026-06-15T16:00:00Z",
            "matchup": "Chelsea @ Arsenal",
            "picks": [],
        }
        m = GameModel(**game_dict)
        assert m.picks == []

    def test_full_slate_dict_validates_through_picks_slate_response(self):
        picks = [make_pick(event_id="e1", implied_probability=0.60)]
        game_dicts = group_picks_into_games(picks, picks_per_game=3)
        m = PicksSlateResponse(
            source="demo",
            game_count=len(game_dicts),
            pick_count=sum(len(g["picks"]) for g in game_dicts),
            picks_per_game=3,
            game_date=TODAY,
            timezone=TZ,
            min_implied_probability=MIN_IMPLIED_PROBABILITY,
            target_implied_probability=MIN_IMPLIED_PROBABILITY,
            relaxed_implied_probability=RELAXED_IMPLIED_PROBABILITY,
            used_relaxed_implied_fallback=False,
            odds_api_warning=None,
            games=game_dicts,
        )
        assert m.game_count == 1
        assert isinstance(m.games[0], GameModel)


# ---------------------------------------------------------------------------
# HTTP integration: OpenAPI schema and response_model validation
# ---------------------------------------------------------------------------


class TestResponseModelIntegration:
    """Verify the HTTP layer uses response_model correctly."""

    def test_openapi_schema_exposes_picks_slate_response(self, client):
        schema = client.get("/openapi.json").json()
        assert "PicksSlateResponse" in schema["components"]["schemas"]

    def test_openapi_schema_exposes_picks_game_response(self, client):
        schema = client.get("/openapi.json").json()
        assert "PicksGameResponse" in schema["components"]["schemas"]

    def test_openapi_schema_exposes_health_response(self, client):
        schema = client.get("/openapi.json").json()
        assert "HealthResponse" in schema["components"]["schemas"]

    def test_demo_slate_response_body_matches_model_fields(self, client, frozen):
        body = client.get("/api/picks", params={"date": TODAY, "timezone": TZ}).json()
        m = PicksSlateResponse(**body)
        assert m.source == "demo"
        assert m.game_count == len(m.games)

    def test_demo_nba_game_response_matches_model_fields(self, client, frozen):
        body = client.get(
            "/api/picks/game",
            params={"sport_key": "basketball_nba", "event_id": "demo-nba", "date": TODAY, "timezone": TZ},
        ).json()
        m = PicksGameResponse(**body)
        assert isinstance(m.game, GameModel)
        assert all(isinstance(p, PickModel) for p in m.game.picks)

    def test_demo_nfl_game_response_validates(self, client, frozen):
        body = client.get(
            "/api/picks/game",
            params={"sport_key": "americanfootball_nfl", "event_id": "demo-nfl", "date": TODAY, "timezone": TZ},
        ).json()
        m = PicksGameResponse(**body)
        assert m.game is not None
        assert m.game.sport_key == "americanfootball_nfl"

    def test_demo_soccer_game_response_validates(self, client, frozen):
        body = client.get(
            "/api/picks/game",
            params={"sport_key": "soccer_epl", "event_id": "demo-epl", "date": TODAY, "timezone": TZ},
        ).json()
        m = PicksGameResponse(**body)
        assert m.game is not None
        assert m.game.sport_key == "soccer_epl"
        assert all(p.market_key == "h2h" for p in m.game.picks)

    def test_unknown_event_returns_null_game_matching_model(self, client, frozen):
        body = client.get(
            "/api/picks/game",
            params={"sport_key": "basketball_nba", "event_id": "no-such-event", "date": TODAY, "timezone": TZ},
        ).json()
        m = PicksGameResponse(**body)
        assert m.game is None
        assert m.pick_count == 0

    def test_live_nba_response_validates_through_model(self, client, frozen, fake_odds_api, monkeypatch):
        monkeypatch.setenv("THE_ODDS_API_KEY", "live-key")
        quotes = [
            quote("draftkings", "player_points", "Over", 1.85, description="Jayson Tatum", point=27.5),
            quote("fanduel", "player_points", "Over", 1.80, description="Jayson Tatum", point=27.5),
        ]
        fake_odds_api.set_event_odds(
            "basketball_nba", "nba-live",
            odds_event("nba-live", commence_time="2026-06-15T23:00:00Z", quotes=quotes),
        )
        body = client.get(
            "/api/picks/game",
            params={"sport_key": "basketball_nba", "event_id": "nba-live", "date": TODAY, "timezone": TZ},
        ).json()
        m = PicksGameResponse(**body)
        assert m.source == "live"
        assert m.game is not None
        assert m.game.sport_key == "basketball_nba"

    def test_live_soccer_slate_validates_through_model(self, client, frozen, fake_odds_api, monkeypatch):
        monkeypatch.setenv("THE_ODDS_API_KEY", "live-key")
        fake_odds_api.set_bulk_odds(
            "soccer_epl",
            [
                odds_event(
                    "epl-live",
                    home="Arsenal",
                    away="Chelsea",
                    commence_time="2026-06-15T22:00:00Z",
                    quotes=h2h_quotes({"draftkings": {"Arsenal": 1.85}, "fanduel": {"Arsenal": 1.80}}),
                    sport_key="soccer_epl",
                )
            ],
        )
        body = client.get("/api/picks", params={"date": TODAY, "timezone": TZ}).json()
        m = PicksSlateResponse(**body)
        assert m.source == "live"
        epl_games = [g for g in m.games if g.sport_key == "soccer_epl"]
        assert len(epl_games) >= 1

    def test_cache_control_header_is_still_set(self, client, frozen):
        response = client.get("/api/picks", params={"date": TODAY, "timezone": TZ})
        assert response.headers["Cache-Control"] == "no-store, must-revalidate"

    def test_x_picks_cache_header_is_still_set(self, client, frozen):
        response = client.get("/api/picks", params={"date": TODAY, "timezone": TZ})
        assert "X-Picks-Cache" in response.headers

    @pytest.fixture
    def frozen(self, monkeypatch):
        return freeze_time(monkeypatch, NOW)
