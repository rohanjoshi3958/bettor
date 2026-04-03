import { LEAGUE_SORT_ORDER } from "./constants.js";

export function groupGamesByLeague(games) {
  const map = new Map();
  for (const g of games) {
    const sk = g.sport_key || "unknown";
    if (!map.has(sk)) {
      map.set(sk, {
        sport_key: sk,
        sport_title: g.sport_title || sk,
        games: [],
      });
    }
    map.get(sk).games.push(g);
  }
  const rows = [...map.values()];
  rows.sort((a, b) => {
    const ia = LEAGUE_SORT_ORDER.indexOf(a.sport_key);
    const ib = LEAGUE_SORT_ORDER.indexOf(b.sport_key);
    const sa = ia === -1 ? 999 : ia;
    const sb = ib === -1 ? 999 : ib;
    if (sa !== sb) return sa - sb;
    return a.sport_title.localeCompare(b.sport_title);
  });
  for (const row of rows) {
    row.games.sort((a, b) => {
      const ta = new Date(a.commence_time || 0).getTime();
      const tb = new Date(b.commence_time || 0).getTime();
      return ta - tb || String(a.event_id).localeCompare(String(b.event_id));
    });
  }
  return rows;
}

/** Total pick rows across all games (for comparing slate quality). */
export function slatePickTotal(games) {
  if (!Array.isArray(games)) return 0;
  return games.reduce(
    (acc, g) => acc + (Array.isArray(g.picks) ? g.picks.length : 0),
    0,
  );
}

export function cloneGames(games) {
  return JSON.parse(JSON.stringify(games));
}
