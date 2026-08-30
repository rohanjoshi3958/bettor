"""Event/game grouping: schedule shells, merging picks onto them, and kickoff ordering."""

from __future__ import annotations

from datetime import datetime, timezone

from services.odds import service as svc
from tests.helpers import freeze_time, iso_offset, make_pick, odds_event

NOW = datetime(2026, 6, 15, 18, 0, tzinfo=timezone.utc)


def _schedule(event_id, commence_time, **kwargs):
    return odds_event(event_id, commence_time=commence_time, **kwargs)


class TestShellsFromScheduledEvents:
    def test_one_shell_per_scheduled_event(self):
        events = [
            _schedule("a", "2026-06-15T20:00:00Z", home="Celtics", away="Bucks"),
            _schedule("b", "2026-06-15T23:00:00Z"),
        ]
        shells = svc.shells_from_scheduled_events(events, "basketball_nba", "NBA")
        assert [s["event_id"] for s in shells] == ["a", "b"]
        assert shells[0]["matchup"] == "Bucks @ Celtics"
        assert shells[0]["sport_title"] == "NBA"
        assert shells[0]["picks"] == []

    def test_shells_are_kickoff_ordered(self):
        events = [
            _schedule("late", "2026-06-15T23:00:00Z"),
            _schedule("early", "2026-06-15T17:00:00Z"),
        ]
        shells = svc.shells_from_scheduled_events(events, "basketball_nba", "NBA")
        assert [s["event_id"] for s in shells] == ["early", "late"]

    def test_missing_fields_become_empty_strings(self):
        shells = svc.shells_from_scheduled_events([{}], "basketball_nba", "NBA")
        assert shells[0]["event_id"] == ""
        assert shells[0]["home_team"] == ""
        assert shells[0]["commence_time"] == ""
        assert shells[0]["matchup"] == " @ "

    def test_numeric_event_ids_are_normalized(self):
        shells = svc.shells_from_scheduled_events(
            [_schedule(4321.0, "2026-06-15T20:00:00Z")], "basketball_nba", "NBA"
        )
        assert shells[0]["event_id"] == "4321"

    def test_empty_payload_yields_no_shells(self):
        assert svc.shells_from_scheduled_events([], "basketball_nba", "NBA") == []


class TestShellsFromBetPicks:
    def test_one_shell_per_distinct_game(self):
        picks = [
            make_pick(event_id="a", pick="p1"),
            make_pick(event_id="a", pick="p2"),
            make_pick(event_id="b", pick="p3"),
        ]
        shells = svc._shells_from_bet_picks(picks)
        assert {s["event_id"] for s in shells} == {"a", "b"}
        assert all(s["picks"] == [] for s in shells)

    def test_same_event_id_across_leagues_stays_separate(self):
        picks = [
            make_pick(sport_key="basketball_nba", event_id="1"),
            make_pick(sport_key="americanfootball_nfl", event_id="1"),
        ]
        assert len(svc._shells_from_bet_picks(picks)) == 2

    def test_no_picks_means_no_shells(self):
        assert svc._shells_from_bet_picks([]) == []


class TestEventsSortedByKickoff:
    def test_sorts_by_kickoff_then_id(self):
        events = [
            _schedule("b", "2026-06-15T20:00:00Z"),
            _schedule("a", "2026-06-15T20:00:00Z"),
            _schedule("c", "2026-06-15T17:00:00Z"),
        ]
        assert [e["id"] for e in svc._events_sorted_by_kickoff(events)] == ["c", "a", "b"]

    def test_unknown_kickoffs_go_last(self):
        events = [_schedule("no-time", ""), _schedule("timed", "2026-06-15T20:00:00Z")]
        assert [e["id"] for e in svc._events_sorted_by_kickoff(events)] == ["timed", "no-time"]

    def test_offset_and_zulu_timestamps_compare_correctly(self):
        events = [
            _schedule("zulu", "2026-06-15T20:00:00Z"),
            _schedule("offset", "2026-06-15T15:30:00-04:00"),
        ]
        assert [e["id"] for e in svc._events_sorted_by_kickoff(events)] == ["offset", "zulu"]


class TestEventsChosenForPropFetch:
    def test_started_games_are_not_worth_a_prop_call(self, monkeypatch):
        freeze_time(monkeypatch, NOW)
        events = [
            _schedule("started", "2026-06-15T17:00:00Z"),
            _schedule("upcoming", "2026-06-15T20:00:00Z"),
        ]
        chosen = svc._events_upcoming_sorted_for_props(events, cap=10)
        assert [e["id"] for e in chosen] == ["upcoming"]

    def test_a_game_starting_exactly_now_is_treated_as_started(self, monkeypatch):
        freeze_time(monkeypatch, NOW)
        events = [_schedule("tipoff", "2026-06-15T18:00:00Z")]
        assert svc._events_upcoming_sorted_for_props(events, cap=10) == []

    def test_unknown_kickoffs_are_kept_rather_than_silently_dropped(self, monkeypatch):
        freeze_time(monkeypatch, NOW)
        events = [_schedule("mystery", "")]
        assert [e["id"] for e in svc._events_upcoming_sorted_for_props(events, cap=10)] == [
            "mystery"
        ]

    def test_cap_limits_upstream_fan_out_to_the_earliest_games(self, monkeypatch):
        freeze_time(monkeypatch, NOW)
        events = [
            _schedule("third", "2026-06-15T23:00:00Z"),
            _schedule("first", "2026-06-15T19:00:00Z"),
            _schedule("second", "2026-06-15T21:00:00Z"),
        ]
        chosen = svc._events_upcoming_sorted_for_props(events, cap=2)
        assert [e["id"] for e in chosen] == ["first", "second"]

    def test_non_positive_cap_means_no_limit(self, monkeypatch):
        freeze_time(monkeypatch, NOW)
        events = [_schedule(str(i), f"2026-06-15T{19 + i}:00:00Z") for i in range(3)]
        assert len(svc._events_upcoming_sorted_for_props(events, cap=0)) == 3


class TestMergeScheduledWithPicks:
    def test_scheduled_games_without_picks_are_still_listed(self):
        shells = [
            {"sport_key": "basketball_nba", "event_id": "a", "commence_time": "2026-06-15T20:00:00Z"},
        ]
        merged = svc._merge_scheduled_with_picks(shells, [])
        assert len(merged) == 1
        assert merged[0]["picks"] == []

    def test_picks_are_attached_to_their_shell(self):
        shells = [
            {
                "sport_key": "basketball_nba",
                "event_id": "a",
                "commence_time": "2026-06-15T20:00:00Z",
                "picks": [],
            }
        ]
        ranked = [{"sport_key": "basketball_nba", "event_id": "a", "picks": [{"pick": "x"}]}]
        merged = svc._merge_scheduled_with_picks(shells, ranked)
        assert merged[0]["picks"] == [{"pick": "x"}]
        assert merged[0]["commence_time"] == "2026-06-15T20:00:00Z"

    def test_event_id_type_mismatch_between_schedule_and_picks_still_joins(self):
        """The schedule endpoint and odds endpoints have returned ids as both int and string."""
        shells = [{"sport_key": "basketball_nba", "event_id": 55, "commence_time": "2026-06-15T20:00:00Z"}]
        ranked = [{"sport_key": "basketball_nba", "event_id": "55.0", "picks": [{"pick": "x"}]}]
        merged = svc._merge_scheduled_with_picks(shells, ranked)
        assert len(merged) == 1
        assert merged[0]["picks"] == [{"pick": "x"}]

    def test_ranked_games_missing_from_the_schedule_are_appended(self):
        ranked = [
            {
                "sport_key": "soccer_epl",
                "event_id": "ghost",
                "commence_time": "2026-06-15T19:00:00Z",
                "picks": [{"pick": "x"}],
            }
        ]
        merged = svc._merge_scheduled_with_picks([], ranked)
        assert [g["event_id"] for g in merged] == ["ghost"]

    def test_duplicate_shells_are_collapsed(self):
        shell = {
            "sport_key": "basketball_nba",
            "event_id": "a",
            "commence_time": "2026-06-15T20:00:00Z",
        }
        merged = svc._merge_scheduled_with_picks([shell, dict(shell)], [])
        assert len(merged) == 1

    def test_merged_output_is_kickoff_ordered(self):
        shells = [
            {"sport_key": "s", "event_id": "late", "commence_time": "2026-06-15T23:00:00Z"},
            {"sport_key": "s", "event_id": "early", "commence_time": "2026-06-15T17:00:00Z"},
            {"sport_key": "s", "event_id": "unknown", "commence_time": ""},
        ]
        merged = svc._merge_scheduled_with_picks(shells, [])
        assert [g["event_id"] for g in merged] == ["early", "late", "unknown"]

    def test_max_games_truncates_after_ordering(self):
        shells = [
            {"sport_key": "s", "event_id": "late", "commence_time": "2026-06-15T23:00:00Z"},
            {"sport_key": "s", "event_id": "early", "commence_time": "2026-06-15T17:00:00Z"},
        ]
        merged = svc._merge_scheduled_with_picks(shells, [], max_games=1)
        assert [g["event_id"] for g in merged] == ["early"]

    def test_max_games_zero_returns_nothing(self):
        shells = [{"sport_key": "s", "event_id": "a", "commence_time": "2026-06-15T17:00:00Z"}]
        assert svc._merge_scheduled_with_picks(shells, [], max_games=0) == []

    def test_negative_max_games_is_ignored(self):
        shells = [{"sport_key": "s", "event_id": "a", "commence_time": "2026-06-15T17:00:00Z"}]
        assert len(svc._merge_scheduled_with_picks(shells, [], max_games=-1)) == 1


class TestExcludePastKickoffGames:
    def test_started_games_are_removed(self, monkeypatch):
        freeze_time(monkeypatch, NOW)
        games = [
            {"event_id": "started", "commence_time": "2026-06-15T17:59:00Z"},
            {"event_id": "upcoming", "commence_time": "2026-06-15T18:01:00Z"},
        ]
        kept = svc._exclude_past_kickoff_games(games)
        assert [g["event_id"] for g in kept] == ["upcoming"]

    def test_games_with_unknown_kickoff_are_kept(self, monkeypatch):
        freeze_time(monkeypatch, NOW)
        games = [{"event_id": "mystery", "commence_time": ""}]
        assert len(svc._exclude_past_kickoff_games(games)) == 1

    def test_kickoff_predicate_matches_the_filter(self, monkeypatch):
        freeze_time(monkeypatch, NOW)
        assert svc._kickoff_still_upcoming({"commence_time": "2026-06-15T18:01:00Z"}) is True
        assert svc._kickoff_still_upcoming({"commence_time": "2026-06-15T18:00:00Z"}) is False
        assert svc._kickoff_still_upcoming({"commence_time": "garbage"}) is True

    def test_empty_slate_stays_empty(self):
        assert svc._exclude_past_kickoff_games([]) == []


class TestMergeGameKey:
    def test_trims_and_normalizes_both_halves(self):
        assert svc._merge_game_key("  basketball_nba ", 12.0) == ("basketball_nba", "12")

    def test_missing_values_produce_empty_key_parts(self):
        assert svc._merge_game_key(None, None) == ("", "")


class TestPickShellRoundTrip:
    def test_demo_style_picks_merge_back_onto_their_own_shells(self):
        """`fetch_best_picks` builds demo shells from picks; the join must not lose anything."""
        picks = [
            make_pick(event_id="a", pick="p1", commence_time=iso_offset(4)),
            make_pick(event_id="b", pick="p2", commence_time=iso_offset(6)),
        ]
        ranked = svc.group_picks_into_games(picks)
        merged = svc._merge_scheduled_with_picks(svc._shells_from_bet_picks(picks), ranked)
        assert [g["event_id"] for g in merged] == ["a", "b"]
        assert all(len(g["picks"]) == 1 for g in merged)
