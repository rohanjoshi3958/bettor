"""Date and timezone filtering: local day bounds, slate windows, pickable-day enforcement."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest
from fastapi import HTTPException

from app.core import calendar as cal
from services.odds import service as svc
from tests.helpers import freeze_time, make_pick


class TestZoneResolution:
    def test_valid_iana_names_are_used(self):
        assert svc._zone("America/New_York").key == "America/New_York"

    @pytest.mark.parametrize("bad", ["", "Mars/Olympus_Mons", "EST5EDT_TYPO", "not a zone"])
    def test_unknown_names_fall_back_to_utc(self, bad):
        assert svc._zone(bad).key == "UTC"


class TestLocalDayBoundsUtc:
    def test_eastern_summer_day_is_04_to_04_utc(self):
        start, end = svc.local_day_bounds_utc(date(2026, 6, 15), "America/New_York")
        assert start == datetime(2026, 6, 15, 4, tzinfo=timezone.utc)
        assert end == datetime(2026, 6, 16, 4, tzinfo=timezone.utc)

    def test_eastern_winter_day_is_05_to_05_utc(self):
        start, end = svc.local_day_bounds_utc(date(2026, 1, 15), "America/New_York")
        assert start == datetime(2026, 1, 15, 5, tzinfo=timezone.utc)
        assert end == datetime(2026, 1, 16, 5, tzinfo=timezone.utc)

    def test_spring_forward_day_is_only_23_hours_long(self):
        start, end = svc.local_day_bounds_utc(date(2026, 3, 8), "America/New_York")
        assert end - start == timedelta(hours=23)

    def test_fall_back_day_is_25_hours_long(self):
        start, end = svc.local_day_bounds_utc(date(2026, 11, 1), "America/New_York")
        assert end - start == timedelta(hours=25)

    def test_utc_day_is_midnight_to_midnight(self):
        start, end = svc.local_day_bounds_utc(date(2026, 6, 15), "UTC")
        assert start == datetime(2026, 6, 15, tzinfo=timezone.utc)
        assert end == datetime(2026, 6, 16, tzinfo=timezone.utc)

    def test_a_day_ahead_zone_starts_the_previous_utc_day(self):
        start, _ = svc.local_day_bounds_utc(date(2026, 6, 15), "Asia/Tokyo")
        assert start == datetime(2026, 6, 14, 15, tzinfo=timezone.utc)

    def test_unknown_zone_falls_back_to_utc_bounds(self):
        assert svc.local_day_bounds_utc(date(2026, 6, 15), "Bad/Zone") == svc.local_day_bounds_utc(
            date(2026, 6, 15), "UTC"
        )

    def test_bounds_are_half_open(self):
        start, end = svc.local_day_bounds_utc(date(2026, 6, 15), "UTC")
        next_start, _ = svc.local_day_bounds_utc(date(2026, 6, 16), "UTC")
        assert end == next_start


class TestFilterPicksByGameDay:
    # 2026-06-15 18:00Z == 14:00 in New York.
    NOW = datetime(2026, 6, 15, 18, 0, tzinfo=timezone.utc)

    def test_keeps_only_games_on_the_requested_local_day(self, monkeypatch):
        freeze_time(monkeypatch, self.NOW)
        picks = [
            make_pick(event_id="tonight", commence_time="2026-06-15T23:00:00Z"),
            make_pick(event_id="tomorrow", commence_time="2026-06-16T23:00:00Z"),
        ]
        kept = svc.filter_picks_by_game_day(picks, date(2026, 6, 15), "America/New_York")
        assert [p.event_id for p in kept] == ["tonight"]

    def test_late_utc_kickoff_belongs_to_the_previous_eastern_day(self, monkeypatch):
        """23:00 ET on Jun 15 is 03:00 UTC on Jun 16 — the frontend still shows it under Jun 15."""
        freeze_time(monkeypatch, self.NOW)
        picks = [make_pick(event_id="late", commence_time="2026-06-16T03:00:00Z")]
        assert svc.filter_picks_by_game_day(picks, date(2026, 6, 15), "America/New_York")
        assert svc.filter_picks_by_game_day(picks, date(2026, 6, 16), "America/New_York") == []

    def test_the_same_kickoff_lands_on_different_days_per_timezone(self, monkeypatch):
        freeze_time(monkeypatch, self.NOW)
        picks = [make_pick(commence_time="2026-06-16T03:00:00Z")]
        assert svc.filter_picks_by_game_day(picks, date(2026, 6, 15), "America/New_York")
        assert svc.filter_picks_by_game_day(picks, date(2026, 6, 16), "UTC")

    def test_todays_started_games_are_dropped(self, monkeypatch):
        freeze_time(monkeypatch, self.NOW)
        picks = [
            make_pick(event_id="started", commence_time="2026-06-15T17:00:00Z"),
            make_pick(event_id="upcoming", commence_time="2026-06-15T19:00:00Z"),
        ]
        kept = svc.filter_picks_by_game_day(picks, date(2026, 6, 15), "America/New_York")
        assert [p.event_id for p in kept] == ["upcoming"]

    def test_a_kickoff_exactly_now_counts_as_started(self, monkeypatch):
        freeze_time(monkeypatch, self.NOW)
        picks = [make_pick(commence_time="2026-06-15T18:00:00Z")]
        assert svc.filter_picks_by_game_day(picks, date(2026, 6, 15), "America/New_York") == []

    def test_future_days_are_not_subject_to_the_started_check(self, monkeypatch):
        freeze_time(monkeypatch, self.NOW)
        picks = [make_pick(commence_time="2026-06-17T00:30:00Z")]
        assert len(svc.filter_picks_by_game_day(picks, date(2026, 6, 16), "America/New_York")) == 1

    def test_unparseable_kickoffs_are_dropped(self, monkeypatch):
        freeze_time(monkeypatch, self.NOW)
        picks = [make_pick(commence_time=""), make_pick(commence_time="soon")]
        assert svc.filter_picks_by_game_day(picks, date(2026, 6, 15), "America/New_York") == []

    def test_empty_input_returns_empty_list(self, monkeypatch):
        freeze_time(monkeypatch, self.NOW)
        assert svc.filter_picks_by_game_day([], date(2026, 6, 15), "UTC") == []


class TestFilterShellsByGameDay:
    NOW = datetime(2026, 6, 15, 18, 0, tzinfo=timezone.utc)

    def test_only_shells_inside_the_local_day_survive(self, monkeypatch):
        freeze_time(monkeypatch, self.NOW)
        shells = [
            {"event_id": "in", "commence_time": "2026-06-15T23:00:00Z"},
            {"event_id": "out", "commence_time": "2026-06-17T23:00:00Z"},
        ]
        kept = svc._filter_shells_by_game_day(shells, date(2026, 6, 15), "America/New_York")
        assert [s["event_id"] for s in kept] == ["in"]

    def test_shells_keep_started_games_because_merge_filters_later(self, monkeypatch):
        """Shell filtering is day-only; `_exclude_past_kickoff_games` removes started games."""
        freeze_time(monkeypatch, self.NOW)
        shells = [{"event_id": "started", "commence_time": "2026-06-15T17:00:00Z"}]
        assert len(svc._filter_shells_by_game_day(shells, date(2026, 6, 15), "America/New_York")) == 1

    def test_shells_without_a_kickoff_are_dropped(self, monkeypatch):
        freeze_time(monkeypatch, self.NOW)
        shells = [{"event_id": "mystery", "commence_time": ""}, {"event_id": "missing"}]
        assert svc._filter_shells_by_game_day(shells, date(2026, 6, 15), "UTC") == []


class TestPickableGameDay:
    NOW = datetime(2026, 6, 15, 18, 0, tzinfo=timezone.utc)

    def test_window_runs_from_today_through_the_offset(self, monkeypatch):
        freeze_time(monkeypatch, self.NOW)
        first, last = cal.pickable_game_day_bounds(ZoneInfo("America/New_York"))
        assert first == date(2026, 6, 15)
        assert last == date(2026, 6, 15) + timedelta(days=cal.PICKS_FUTURE_END_OFFSET)

    def test_default_day_is_today_in_the_request_timezone(self, monkeypatch):
        freeze_time(monkeypatch, self.NOW)
        assert cal.default_pickable_game_day(ZoneInfo("America/New_York")) == date(2026, 6, 15)

    def test_default_day_respects_a_zone_that_already_rolled_over(self, monkeypatch):
        freeze_time(monkeypatch, self.NOW)
        assert cal.default_pickable_game_day(ZoneInfo("Asia/Tokyo")) == date(2026, 6, 16)

    @pytest.mark.parametrize("offset_days", [0, 1, 5])
    def test_days_inside_the_window_are_accepted(self, monkeypatch, offset_days):
        freeze_time(monkeypatch, self.NOW)
        day = date(2026, 6, 15) + timedelta(days=offset_days)
        cal.enforce_pickable_game_day(day, ZoneInfo("America/New_York"))

    @pytest.mark.parametrize("offset_days", [-1, -30, 6, 400])
    def test_days_outside_the_window_are_rejected(self, monkeypatch, offset_days):
        freeze_time(monkeypatch, self.NOW)
        day = date(2026, 6, 15) + timedelta(days=offset_days)
        with pytest.raises(HTTPException) as exc:
            cal.enforce_pickable_game_day(day, ZoneInfo("America/New_York"))
        assert exc.value.status_code == 400
        assert "Game day must be from" in exc.value.detail

    def test_rejection_message_names_the_allowed_range(self, monkeypatch):
        freeze_time(monkeypatch, self.NOW)
        with pytest.raises(HTTPException) as exc:
            cal.enforce_pickable_game_day(date(2020, 1, 1), ZoneInfo("America/New_York"))
        assert "2026-06-15" in exc.value.detail
        assert "2026-06-20" in exc.value.detail


class TestUpstreamWindowFormatting:
    def test_slate_window_is_sent_as_zulu_timestamps(self):
        """The Odds API rejects offsets; commenceTimeFrom/To must be `...Z`."""
        start, end = svc.local_day_bounds_utc(date(2026, 6, 15), "America/New_York")
        assert svc._utc_iso_z(start) == "2026-06-15T04:00:00Z"
        assert svc._utc_iso_z(end - timedelta(seconds=1)) == "2026-06-16T03:59:59Z"
