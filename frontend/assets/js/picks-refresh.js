import { PROP_SPORT_KEYS } from "./constants.js";
import { els } from "./elements.js";
import { readHttpErrorDetail } from "./fetch-utils.js";
import { singleGamePicksUrl } from "./api-urls.js";
import { picksSession } from "./picks-session.js";
import {
  mergeGameFromApiPayload,
  renderSlateUI,
} from "./slate-views.js";
import { groupGamesByLeague } from "./slate-model.js";

export async function refreshGameData(sportKey, eventId, opts = {}) {
  const silent = Boolean(opts.silent);
  const btn = opts.button;
  const prev = btn?.textContent;
  if (btn && !silent) {
    btn.disabled = true;
    btn.textContent = "Updating…";
  }

  try {
    const res = await fetch(singleGamePicksUrl(sportKey, eventId), {
      cache: "no-store",
    });
    if (!res.ok) throw new Error(await readHttpErrorDetail(res));
    const data = await res.json();
    mergeGameFromApiPayload(sportKey, eventId, data.game);
    renderSlateUI();

    if (!silent) {
      if (typeof data.odds_api_warning === "string" && data.odds_api_warning) {
        if (els.oddsApiBanner) {
          els.oddsApiBanner.textContent = data.odds_api_warning;
          els.oddsApiBanner.classList.remove("hidden");
        }
      } else if (els.oddsApiBanner) {
        els.oddsApiBanner.classList.add("hidden");
      }

      if (data.source === "live") {
        els.statusPill.textContent = "Live odds";
        els.statusPill.className = "pill pill-live";
      }
    }
  } catch (e) {
    if (!silent) {
      els.error.textContent =
        e instanceof Error ? e.message : "Could not refresh this game.";
      els.error.classList.remove("hidden");
    }
  } finally {
    if (btn && !silent) {
      btn.disabled = false;
      btn.textContent = prev;
    }
  }
}

/**
 * NBA/NFL: re-fetch prop games that returned empty while siblings in the same league have picks.
 */
export function maybeBackfillEmptyPropGames(source, generation) {
  if (source !== "live") return;
  const run = () => {
    if (generation !== picksSession.picksBackfillGeneration) return;
    const leagues = groupGamesByLeague(picksSession.slateGames);
    const tasks = [];
    for (const league of leagues) {
      const propGames = (league.games || []).filter((g) =>
        PROP_SPORT_KEYS.has(g.sport_key),
      );
      if (propGames.length === 0) continue;
      const anyPicks = propGames.some((g) => (g.picks || []).length > 0);
      if (!anyPicks) continue;
      for (const g of propGames) {
        if ((g.picks || []).length === 0) {
          tasks.push([g.sport_key, String(g.event_id ?? "")]);
        }
      }
    }
    if (tasks.length === 0) return;
    void (async () => {
      for (const [sk, eid] of tasks) {
        if (generation !== picksSession.picksBackfillGeneration) return;
        await refreshGameData(sk, eid, { silent: true });
        await new Promise((r) => setTimeout(r, 100));
      }
    })();
  };
  if (typeof requestIdleCallback === "function") {
    requestIdleCallback(() => run(), { timeout: 2000 });
  } else {
    setTimeout(run, 50);
  }
}
