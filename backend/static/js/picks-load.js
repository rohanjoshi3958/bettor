import { browserTz } from "./constants.js";
import { els } from "./elements.js";
import { readHttpErrorDetail } from "./fetch-utils.js";
import { picksUrl } from "./api-urls.js";
import {
  picksSession,
  getGameDateString,
  clampGameDateToBounds,
} from "./picks-session.js";
import { cloneGames, slatePickTotal } from "./slate-model.js";
import { renderGameList } from "./slate-views.js";
import { maybeBackfillEmptyPropGames } from "./picks-refresh.js";

export async function loadPicks() {
  clampGameDateToBounds();
  const requestedDate = getGameDateString();
  picksSession.picksBackfillGeneration += 1;
  const backfillGen = picksSession.picksBackfillGeneration;
  if (picksSession.picksFetchController) {
    picksSession.picksFetchController.abort();
  }
  picksSession.picksFetchController = new AbortController();
  const { signal } = picksSession.picksFetchController;

  els.error.classList.add("hidden");
  els.statusPill.textContent = "Loading…";
  els.statusPill.className = "pill pill-muted";

  try {
    const res = await fetch(picksUrl(), {
      cache: "no-store",
      signal,
    });
    if (!res.ok) throw new Error(await readHttpErrorDetail(res));
    const data = await res.json();

    if (signal.aborted) return;
    if (getGameDateString() !== requestedDate) return;

    if (typeof data.game_date === "string" && data.game_date) {
      picksSession.lastExplicitGameDate = data.game_date;
      if (
        els.gameDateInput &&
        els.gameDateInput.value?.trim() !== data.game_date
      ) {
        els.gameDateInput.value = data.game_date;
      }
    }

    if (typeof data.odds_api_warning === "string" && data.odds_api_warning) {
      if (els.oddsApiBanner) {
        els.oddsApiBanner.textContent = data.odds_api_warning;
        els.oddsApiBanner.classList.remove("hidden");
      }
    } else if (els.oddsApiBanner) {
      els.oddsApiBanner.classList.add("hidden");
    }

    if (data.source === "demo") {
      els.demoBanner.textContent =
        "Demo data — add THE_ODDS_API_KEY in a .env file (get a key at the-odds-api.com) for live odds.";
      els.demoBanner.classList.remove("hidden");
    } else {
      els.demoBanner.classList.add("hidden");
    }

    if (els.relaxedBanner) {
      if (
        data.used_relaxed_implied_fallback &&
        (data.games || []).length > 0
      ) {
        const t = data.target_implied_probability ?? 0.52;
        const u = data.min_implied_probability ?? 0.5;
        els.relaxedBanner.textContent = `Nothing met ${Math.round(t * 100)}%+ implied — showing lines at ${Math.round(u * 100)}%+ instead (still need two books).`;
        els.relaxedBanner.classList.remove("hidden");
      } else {
        els.relaxedBanner.classList.add("hidden");
      }
    }

    els.statusPill.textContent = data.source === "live" ? "Live odds" : "Demo";
    els.statusPill.className =
      data.source === "live" ? "pill pill-live" : "pill pill-muted";

    const games = data.games || [];
    const responseDate = data.game_date || requestedDate;

    if (games.length > 0) {
      const prevSnap = picksSession.lastGoodSlateByDate.get(responseDate);
      const nextTotal = slatePickTotal(games);
      const prevTotal = prevSnap ? slatePickTotal(prevSnap) : 0;
      if (!prevSnap || nextTotal >= prevTotal) {
        picksSession.lastGoodSlateByDate.set(responseDate, cloneGames(games));
      }
      const cachedBetter =
        prevSnap && prevTotal > 0 && prevTotal > nextTotal;
      const toRender = cachedBetter ? cloneGames(prevSnap) : games;
      if (els.staleSlateBanner) els.staleSlateBanner.classList.add("hidden");
      if (cachedBetter) els.relaxedBanner?.classList.add("hidden");
      els.empty.classList.add("hidden");
      renderGameList(toRender);
      maybeBackfillEmptyPropGames(data.source, backfillGen);
      return;
    }

    const cached =
      picksSession.lastGoodSlateByDate.get(responseDate) ||
      picksSession.lastGoodSlateByDate.get(requestedDate);
    if (cached && cached.length > 0) {
      els.empty.classList.add("hidden");
      if (els.staleSlateBanner) {
        els.staleSlateBanner.textContent =
          "Latest reload returned no qualifying lines for this day. Showing your previous slate for this game day — try again in a moment, or use Update odds on a game.";
        els.staleSlateBanner.classList.remove("hidden");
      }
      els.relaxedBanner?.classList.add("hidden");
      renderGameList(cloneGames(cached));
      maybeBackfillEmptyPropGames(data.source, backfillGen);
      return;
    }

    if (els.staleSlateBanner) els.staleSlateBanner.classList.add("hidden");
    els.cards.innerHTML = "";
    const gd = data.game_date || "";
    const tgt = data.target_implied_probability ?? 0.52;
    const rel = data.relaxed_implied_probability ?? 0.5;
    let msg = "No games with picks for this day.";
    if (gd) {
      msg = `No games with picks for ${gd} (${data.timezone || browserTz}).`;
    }
    msg += ` Each line needs ~${Math.round(tgt * 100)}%+ implied (we also try ${Math.round(rel * 100)}%+) and the same outcome priced at two of your books (DK, FD, Fanatics). Try another date or a busier sports day.`;
    els.empty.textContent = msg;
    els.empty.classList.remove("hidden");
  } catch (e) {
    if (e?.name === "AbortError") return;
    els.error.textContent =
      e instanceof Error ? e.message : "Could not load picks.";
    els.error.classList.remove("hidden");
    els.statusPill.textContent = "Error";
    els.statusPill.className = "pill pill-muted";
  }
}
