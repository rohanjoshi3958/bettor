import { browserTz } from "./constants.js";
import { getGameDateString } from "./picks-session.js";

export function picksUrl() {
  const date = getGameDateString();
  const params = new URLSearchParams({
    date,
    timezone: browserTz,
    picks_per_game: "3",
  });
  return `/api/picks?${params.toString()}`;
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
  return `/api/picks/game?${params.toString()}`;
}
