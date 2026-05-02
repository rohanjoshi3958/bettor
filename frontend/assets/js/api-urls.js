import { API_BASE } from "./config.js";
import { browserTz } from "./constants.js";
import { getGameDateString } from "./picks-session.js";

function apiPath(path) {
  const base = String(API_BASE ?? "").replace(/\/$/, "");
  return `${base}${path.startsWith("/") ? path : `/${path}`}`;
}

export function picksUrl() {
  const date = getGameDateString();
  const params = new URLSearchParams({
    date,
    timezone: browserTz,
    picks_per_game: "3",
  });
  return `${apiPath("/api/picks")}?${params.toString()}`;
}

/**
 * @param {{ marketKey?: string, commenceTime?: string, backfill?: number }} [opts]
 */
export function lineHistoryUrl(sportKey, eventId, pick, opts = {}) {
  const params = new URLSearchParams({
    sport_key: sportKey,
    event_id: eventId,
    pick,
    backfill: String(opts.backfill ?? 1),
  });
  if (opts.marketKey) params.set("market_key", String(opts.marketKey));
  if (opts.commenceTime) params.set("commence_time", String(opts.commenceTime));
  return `${apiPath("/api/picks/line-history")}?${params.toString()}`;
}

export function singleGamePicksUrl(sportKey, eventId) {
  const date = getGameDateString();
  const params = new URLSearchParams({
    sport_key: sportKey,
    event_id: eventId,
    date,
    timezone: browserTz,
    picks_per_game: "3",
  });
  return `${apiPath("/api/picks/game")}?${params.toString()}`;
}
