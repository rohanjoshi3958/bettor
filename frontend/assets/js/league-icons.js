/**
 * League visuals: raster assets under /assets/js/ (repo path `frontend/assets/js/`).
 */

const ASSET = "/assets/js";

/** Odds API `sport_key` → filename in frontend/assets/js/ */
const LEAGUE_IMAGE = {
  basketball_nba: "nba.png",
  americanfootball_nfl: "nfl.jpeg",
  baseball_mlb: "mlb.jpeg",
  soccer_epl: "premierleague.jpeg",
  soccer_spain_la_liga: "laliga.jpeg",
  soccer_italy_serie_a: "seriea.jpeg",
  soccer_germany_bundesliga: "bundesilga.jpeg",
  soccer_france_ligue: "ligue1.png",
  soccer_uefa_champs_league: "championsleague.jpeg",
  soccer_uefa_europa_league: "europaleague.png",
};

const FALLBACK_SVG = `<svg class="league-tile-svg" viewBox="0 0 64 64" aria-hidden="true"><circle cx="32" cy="32" r="26" fill="rgba(255,255,255,.08)" stroke="rgba(62,224,168,.45)" stroke-width="2"/><text x="32" y="41" text-anchor="middle" fill="#8b98a8" font-size="22" font-weight="700" font-family="system-ui,sans-serif">?</text></svg>`;

function imgHtml(filename) {
  const src = `${ASSET}/${filename}`;
  return `<img class="league-tile-img" src="${src}" alt="" width="88" height="88" loading="lazy" decoding="async" />`;
}

export function leagueIconHtml(sportKey) {
  const file = LEAGUE_IMAGE[sportKey];
  if (file) return imgHtml(file);
  return FALLBACK_SVG;
}
