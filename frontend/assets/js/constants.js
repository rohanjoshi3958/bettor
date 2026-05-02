/** Must match backend `PICKS_FUTURE_END_OFFSET` (last allowed = today + this in browser TZ). */
export const PICKS_FUTURE_END_OFFSET = 5;

export const browserTz = Intl.DateTimeFormat().resolvedOptions().timeZone;

/** Prop leagues first, then soccer / others A–Z by title */
export const LEAGUE_SORT_ORDER = [
  "basketball_nba",
  "americanfootball_nfl",
];

export const PROP_SPORT_KEYS = new Set(LEAGUE_SORT_ORDER);
