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
