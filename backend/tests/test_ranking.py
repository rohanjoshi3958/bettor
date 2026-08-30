"""Pick ranking, the implied-probability floor, its relaxed fallback, and JSON serialization."""

from __future__ import annotations

import pytest

from services.odds import service as svc
from tests.helpers import iso_offset, make_pick


class TestRankScore:
    def test_blends_implied_probability_and_line_edge(self):
        pick = make_pick(implied_probability=0.60, edge_pct=4.0)
        assert svc.rank_score(pick) == pytest.approx(0.55 * 60.0 + 0.45 * 4.0)

    def test_higher_implied_probability_wins_at_equal_edge(self):
        strong = make_pick(implied_probability=0.62, edge_pct=3.0)
        weak = make_pick(implied_probability=0.55, edge_pct=3.0)
        assert svc.rank_score(strong) > svc.rank_score(weak)

    def test_higher_edge_wins_at_equal_implied_probability(self):
        shopped = make_pick(implied_probability=0.58, edge_pct=6.0)
        flat = make_pick(implied_probability=0.58, edge_pct=0.5)
        assert svc.rank_score(shopped) > svc.rank_score(flat)

    def test_edge_can_outweigh_a_small_implied_gap(self):
        """The 0.45 edge weight is meant to be able to beat a marginal probability advantage."""
        big_edge = make_pick(implied_probability=0.55, edge_pct=8.0)
        tiny_edge = make_pick(implied_probability=0.57, edge_pct=0.0)
        assert svc.rank_score(big_edge) > svc.rank_score(tiny_edge)


class TestGroupPicksIntoGames:
    def test_groups_by_sport_and_event(self):
        picks = [
            make_pick(sport_key="basketball_nba", event_id="a"),
            make_pick(sport_key="basketball_nba", event_id="b"),
            make_pick(sport_key="americanfootball_nfl", event_id="a"),
        ]
        games = svc.group_picks_into_games(picks)
        assert len(games) == 3

    def test_same_event_id_in_two_leagues_stays_separate(self):
        picks = [
            make_pick(sport_key="basketball_nba", sport_title="NBA", event_id="123"),
            make_pick(sport_key="americanfootball_nfl", sport_title="NFL", event_id="123"),
        ]
        games = svc.group_picks_into_games(picks)
        assert {g["sport_key"] for g in games} == {"basketball_nba", "americanfootball_nfl"}

    def test_mixed_event_id_types_merge_into_one_game(self):
        picks = [
            make_pick(event_id=1234, pick="a"),
            make_pick(event_id="1234", pick="b"),
            make_pick(event_id=1234.0, pick="c"),
        ]
        games = svc.group_picks_into_games(picks)
        assert len(games) == 1
        assert games[0]["event_id"] == "1234"
        assert len(games[0]["picks"]) == 3

    def test_keeps_only_top_picks_per_game_ordered_by_rank(self):
        picks = [
            make_pick(pick="worst", implied_probability=0.53, edge_pct=0.1),
            make_pick(pick="best", implied_probability=0.70, edge_pct=9.0),
            make_pick(pick="middle", implied_probability=0.60, edge_pct=2.0),
            make_pick(pick="fourth", implied_probability=0.54, edge_pct=0.2),
        ]
        games = svc.group_picks_into_games(picks, picks_per_game=3)
        labels = [p["pick"] for p in games[0]["picks"]]
        assert labels == ["best", "middle", "fourth"]

    def test_default_picks_per_game_is_three(self):
        picks = [make_pick(pick=f"p{i}", implied_probability=0.60 + i / 100) for i in range(6)]
        games = svc.group_picks_into_games(picks)
        assert len(games[0]["picks"]) == svc.PICKS_PER_GAME == 3

    def test_drops_picks_below_the_implied_floor(self):
        picks = [
            make_pick(pick="keep", implied_probability=svc.MIN_IMPLIED_PROBABILITY),
            make_pick(pick="drop", implied_probability=svc.MIN_IMPLIED_PROBABILITY - 0.01),
        ]
        games = svc.group_picks_into_games(picks)
        assert [p["pick"] for p in games[0]["picks"]] == ["keep"]

    def test_explicit_floor_overrides_the_default(self):
        picks = [make_pick(implied_probability=0.51)]
        assert svc.group_picks_into_games(picks) == []
        assert svc.group_picks_into_games(picks, min_implied=0.50) != []

    def test_games_with_no_qualifying_pick_are_omitted(self):
        picks = [make_pick(event_id="cold", implied_probability=0.10)]
        assert svc.group_picks_into_games(picks) == []

    def test_empty_input_returns_empty_list(self):
        assert svc.group_picks_into_games([]) == []

    def test_games_are_sorted_by_kickoff(self):
        picks = [
            make_pick(event_id="late", commence_time="2026-06-15T23:00:00Z"),
            make_pick(event_id="early", commence_time="2026-06-15T17:00:00Z"),
            make_pick(event_id="mid", commence_time="2026-06-15T20:00:00Z"),
        ]
        games = svc.group_picks_into_games(picks)
        assert [g["event_id"] for g in games] == ["early", "mid", "late"]

    def test_unknown_kickoffs_sort_last_and_tie_break_on_event_id(self):
        picks = [
            make_pick(event_id="zz", commence_time=""),
            make_pick(event_id="aa", commence_time=""),
            make_pick(event_id="timed", commence_time="2026-06-15T17:00:00Z"),
        ]
        games = svc.group_picks_into_games(picks)
        assert [g["event_id"] for g in games] == ["timed", "aa", "zz"]

    def test_max_games_truncates_after_sorting(self):
        picks = [
            make_pick(event_id="late", commence_time="2026-06-15T23:00:00Z"),
            make_pick(event_id="early", commence_time="2026-06-15T17:00:00Z"),
        ]
        games = svc.group_picks_into_games(picks, max_games=1)
        assert [g["event_id"] for g in games] == ["early"]

    def test_game_block_carries_matchup_metadata(self):
        pick = make_pick(home_team="Boston Celtics", away_team="Milwaukee Bucks")
        game = svc.group_picks_into_games([pick])[0]
        assert game["matchup"] == "Milwaukee Bucks @ Boston Celtics"
        assert game["home_team"] == "Boston Celtics"
        assert game["away_team"] == "Milwaukee Bucks"
        assert game["sport_title"] == "NBA"


class TestImpliedFallback:
    def test_primary_floor_is_used_when_it_yields_games(self):
        picks = [make_pick(implied_probability=0.65)]
        games, floor_used, relaxed = svc._group_with_implied_fallback(picks, 3, None)
        assert games and floor_used == svc.MIN_IMPLIED_PROBABILITY
        assert relaxed is False

    def test_relaxed_floor_rescues_an_otherwise_empty_slate(self):
        picks = [make_pick(implied_probability=0.51)]
        games, floor_used, relaxed = svc._group_with_implied_fallback(picks, 3, None)
        assert len(games) == 1
        assert floor_used == svc.RELAXED_IMPLIED_PROBABILITY
        assert relaxed is True

    def test_relaxed_floor_is_looser_than_the_primary_floor(self):
        assert svc.RELAXED_IMPLIED_PROBABILITY < svc.MIN_IMPLIED_PROBABILITY

    def test_nothing_qualifies_under_either_floor(self):
        picks = [make_pick(implied_probability=0.20)]
        games, floor_used, relaxed = svc._group_with_implied_fallback(picks, 3, None)
        assert games == []
        assert floor_used == svc.RELAXED_IMPLIED_PROBABILITY
        assert relaxed is True

    def test_fallback_honors_picks_per_game_and_max_games(self):
        picks = [
            make_pick(event_id="a", pick=f"a{i}", implied_probability=0.51, edge_pct=i)
            for i in range(4)
        ] + [
            make_pick(
                event_id="b",
                pick="b0",
                implied_probability=0.51,
                commence_time=iso_offset(9),
            )
        ]
        games, _, relaxed = svc._group_with_implied_fallback(picks, 2, 1)
        assert relaxed is True
        assert len(games) == 1
        assert len(games[0]["picks"]) == 2


class TestPicksToJson:
    def test_exposes_the_frontend_contract(self):
        payload = svc.picks_to_json([make_pick()])[0]
        assert set(payload) == {
            "sport_key",
            "sport_title",
            "event_id",
            "matchup",
            "home_team",
            "away_team",
            "commence_time",
            "pick",
            "market_key",
            "implied_probability",
            "implied_pct",
            "rank_score",
            "best_decimal_odds",
            "best_book",
            "avg_decimal_odds",
            "edge_pct",
        }

    def test_derives_percentage_and_rank_score(self):
        payload = svc.picks_to_json([make_pick(implied_probability=0.6173, edge_pct=2.5)])[0]
        assert payload["implied_pct"] == 61.73
        assert payload["rank_score"] == 35.08

    def test_preserves_input_order(self):
        picks = [make_pick(pick="first"), make_pick(pick="second")]
        assert [p["pick"] for p in svc.picks_to_json(picks)] == ["first", "second"]

    def test_empty_input_returns_empty_list(self):
        assert svc.picks_to_json([]) == []
