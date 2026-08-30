"""Sport/provider configuration: supported keys, prop markets, display titles, API key resolution.

This module is a pure-data layer — no I/O, no domain logic, no HTTP.
"""

from __future__ import annotations

import os

SOCCER_SPORT_KEYS = (
    "soccer_epl",
    "soccer_spain_la_liga",
    "soccer_italy_serie_a",
    "soccer_germany_bundesliga",
    "soccer_france_ligue",
    "soccer_uefa_champs_league",
    "soccer_uefa_europa_league",
    "soccer_fifa_world_cup",
)

NBA_PROP_MARKETS = (
    "player_points,player_rebounds,player_assists,player_threes,"
    "player_points_rebounds_assists,player_points_rebounds,player_points_assists,player_rebounds_assists"
)

NFL_PROP_MARKETS = (
    "player_pass_yds,player_pass_tds,player_rush_yds,player_reception_yds,"
    "player_receptions,player_anytime_td"
)

_MARKET_LABELS: dict[str, str] = {
    "player_points": "PTS",
    "player_rebounds": "REB",
    "player_assists": "AST",
    "player_threes": "3PM",
    "player_points_rebounds_assists": "PRA",
    "player_points_rebounds": "P+R",
    "player_points_assists": "P+A",
    "player_rebounds_assists": "R+A",
    "player_pass_yds": "Pass Yds",
    "player_pass_tds": "Pass TD",
    "player_rush_yds": "Rush Yds",
    "player_reception_yds": "Rec Yds",
    "player_receptions": "Rec",
    "player_anytime_td": "Anytime TD",
}


def _nba_market_set() -> set[str]:
    return {m.strip() for m in NBA_PROP_MARKETS.replace("\n", "").split(",") if m.strip()}


def _nfl_market_set() -> set[str]:
    return {m.strip() for m in NFL_PROP_MARKETS.replace("\n", "").split(",") if m.strip()}


def _sport_titles() -> dict[str, str]:
    return {
        "basketball_nba": "NBA",
        "americanfootball_nfl": "NFL",
        "soccer_epl": "EPL",
        "soccer_spain_la_liga": "La Liga",
        "soccer_italy_serie_a": "Serie A",
        "soccer_germany_bundesliga": "Bundesliga",
        "soccer_france_ligue": "Ligue 1",
        "soccer_uefa_champs_league": "UCL",
        "soccer_uefa_europa_league": "Europa League",
        "soccer_fifa_world_cup": "FIFA World Cup",
    }


def supported_sport_key(sport_key: str) -> bool:
    return sport_key in SOCCER_SPORT_KEYS or sport_key in (
        "basketball_nba",
        "americanfootball_nfl",
    )


def get_api_key() -> str | None:
    raw = os.environ.get("THE_ODDS_API_KEY")
    if raw is None:
        return None
    k = raw.strip()
    if not k or k.startswith("#"):
        return None
    return k
