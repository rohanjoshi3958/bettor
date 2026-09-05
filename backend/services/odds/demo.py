"""Demo/fallback picks returned when no live API key is configured.

These are static, realistic-looking BetPick rows used for UI previews and
integration tests that don't want to exercise the upstream Odds API.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta

from services.odds.models import BetPick
from services.odds.normalization import _zone, implied_probability, local_day_bounds_utc
from services.odds.sports import _sport_titles


def _demo_commence_on_day(for_day: date, tz_name: str, hour: int, minute: int = 0) -> str:
    z = _zone(tz_name)
    dt = datetime.combine(for_day, time(hour, minute), tzinfo=z)
    utc = dt.astimezone(UTC)
    now_utc = datetime.now(UTC)
    if utc <= now_utc:
        _, end_utc = local_day_bounds_utc(for_day, tz_name)
        bump = now_utc + timedelta(hours=2, minutes=5)
        utc = min(bump, end_utc - timedelta(minutes=1))
        if utc <= now_utc:
            utc = end_utc - timedelta(minutes=1)
    return utc.isoformat().replace("+00:00", "Z")


def demo_picks(for_day: date, tz_name: str) -> list[BetPick]:
    titles = _sport_titles()
    nba_t = _demo_commence_on_day(for_day, tz_name, 19, 30)
    return [
        BetPick(
            sport_key="basketball_nba",
            sport_title=titles["basketball_nba"],
            event_id="demo-nba",
            home_team="Boston Celtics",
            away_team="Milwaukee Bucks",
            commence_time=nba_t,
            pick="PTS · Jayson Tatum — Over 27.5",
            market_key="player_points",
            implied_probability=round(implied_probability(1.62), 4),
            best_decimal_odds=1.62,
            best_book="DraftKings",
            avg_decimal_odds=1.58,
            edge_pct=2.5,
        ),
        BetPick(
            sport_key="basketball_nba",
            sport_title=titles["basketball_nba"],
            event_id="demo-nba",
            home_team="Boston Celtics",
            away_team="Milwaukee Bucks",
            commence_time=nba_t,
            pick="REB · Jayson Tatum — Over 8.5",
            market_key="player_rebounds",
            implied_probability=round(implied_probability(1.59), 4),
            best_decimal_odds=1.59,
            best_book="FanDuel",
            avg_decimal_odds=1.55,
            edge_pct=2.7,
        ),
        BetPick(
            sport_key="basketball_nba",
            sport_title=titles["basketball_nba"],
            event_id="demo-nba",
            home_team="Boston Celtics",
            away_team="Milwaukee Bucks",
            commence_time=nba_t,
            pick="PRA · Jayson Tatum — Over 38.5",
            market_key="player_points_rebounds_assists",
            implied_probability=round(implied_probability(1.61), 4),
            best_decimal_odds=1.61,
            best_book="Fanatics",
            avg_decimal_odds=1.56,
            edge_pct=3.0,
        ),
        BetPick(
            sport_key="basketball_nba",
            sport_title=titles["basketball_nba"],
            event_id="demo-nba",
            home_team="Boston Celtics",
            away_team="Milwaukee Bucks",
            commence_time=nba_t,
            pick="3PM · Derrick White — Over 3.5",
            market_key="player_threes",
            implied_probability=round(implied_probability(1.57), 4),
            best_decimal_odds=1.57,
            best_book="FanDuel",
            avg_decimal_odds=1.52,
            edge_pct=2.4,
        ),
        BetPick(
            sport_key="basketball_nba",
            sport_title=titles["basketball_nba"],
            event_id="demo-nba",
            home_team="Boston Celtics",
            away_team="Milwaukee Bucks",
            commence_time=nba_t,
            pick="AST · Jrue Holiday — Over 5.5",
            market_key="player_assists",
            implied_probability=round(implied_probability(1.6), 4),
            best_decimal_odds=1.6,
            best_book="DraftKings",
            avg_decimal_odds=1.57,
            edge_pct=1.9,
        ),
        BetPick(
            sport_key="americanfootball_nfl",
            sport_title=titles["americanfootball_nfl"],
            event_id="demo-nfl",
            home_team="Kansas City Chiefs",
            away_team="Buffalo Bills",
            commence_time=_demo_commence_on_day(for_day, tz_name, 16, 0),
            pick="Pass Yds · Josh Allen — Over 274.5",
            market_key="player_pass_yds",
            implied_probability=round(implied_probability(1.58), 4),
            best_decimal_odds=1.58,
            best_book="FanDuel",
            avg_decimal_odds=1.54,
            edge_pct=2.6,
        ),
        BetPick(
            sport_key="soccer_epl",
            sport_title=titles["soccer_epl"],
            event_id="demo-epl",
            home_team="Arsenal",
            away_team="Chelsea",
            commence_time=_demo_commence_on_day(for_day, tz_name, 12, 0),
            pick="Arsenal",
            market_key="h2h",
            implied_probability=round(implied_probability(1.55), 4),
            best_decimal_odds=1.55,
            best_book="Fanatics",
            avg_decimal_odds=1.51,
            edge_pct=2.6,
        ),
        BetPick(
            sport_key="soccer_fifa_world_cup",
            sport_title=titles["soccer_fifa_world_cup"],
            event_id="demo-wc",
            home_team="Brazil",
            away_team="Germany",
            commence_time=_demo_commence_on_day(for_day, tz_name, 14, 0),
            pick="Brazil",
            market_key="h2h",
            implied_probability=round(implied_probability(1.72), 4),
            best_decimal_odds=1.72,
            best_book="DraftKings",
            avg_decimal_odds=1.66,
            edge_pct=3.1,
        ),
    ]
