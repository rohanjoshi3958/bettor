import { browserTz, PICKS_FUTURE_END_OFFSET } from "./constants.js";
import { els } from "./elements.js";
import { minPickDateString, maxPickDateString } from "./dates.js";

/** Shared mutable picks UI state (one page load). */
export const picksSession = {
  /** Flat list from last /api/picks; drill-down: leagues → games → game detail */
  slateGames: [],
  navState: { view: "leagues" },
  /** Last calendar day the user chose (or server confirmed). */
  lastExplicitGameDate: null,
  /** Per YYYY-MM-DD, `{ games, picksPerGame }` or legacy games array (session only). */
  lastGoodSlateByDate: new Map(),
  picksFetchController: null,
  picksBackfillGeneration: 0,
};

/**
 * YYYY-MM-DD for API calls. Does not overwrite the date control when it is briefly empty.
 */
export function getGameDateString() {
  const min = minPickDateString(browserTz);
  const max = maxPickDateString(browserTz, PICKS_FUTURE_END_OFFSET);
  const input = els.gameDateInput;
  if (!input) {
    let v = picksSession.lastExplicitGameDate;
    if (!v || v < min || v > max) v = min;
    picksSession.lastExplicitGameDate = v;
    return v;
  }
  const v = input.value?.trim();
  if (v) {
    if (v < min || v > max) {
      input.value = min;
      picksSession.lastExplicitGameDate = min;
      return min;
    }
    picksSession.lastExplicitGameDate = v;
    return v;
  }
  if (
    picksSession.lastExplicitGameDate &&
    picksSession.lastExplicitGameDate >= min &&
    picksSession.lastExplicitGameDate <= max
  ) {
    return picksSession.lastExplicitGameDate;
  }
  input.value = min;
  picksSession.lastExplicitGameDate = min;
  return min;
}

/** Set `<input type="date">` min/max and clamp to today … today+N in browserTz. */
export function clampGameDateToBounds() {
  const input = els.gameDateInput;
  if (!input) return;
  const min = minPickDateString(browserTz);
  const max = maxPickDateString(browserTz, PICKS_FUTURE_END_OFFSET);
  input.min = min;
  input.max = max;
  const v = input.value?.trim();
  if (v && (v < min || v > max)) {
    input.value = min;
    picksSession.lastExplicitGameDate = min;
  }
}

export function initGameDateDefault() {
  const input = els.gameDateInput;
  if (input) {
    const min = minPickDateString(browserTz);
    const max = maxPickDateString(browserTz, PICKS_FUTURE_END_OFFSET);
    input.min = min;
    input.max = max;
    const cur = input.value?.trim();
    if (!cur || cur < min || cur > max) {
      input.value = min;
    }
    picksSession.lastExplicitGameDate = input.value.trim();
    clampGameDateToBounds();
  } else {
    picksSession.lastExplicitGameDate = minPickDateString(browserTz);
  }
}
