import { els } from "./elements.js";
import { initGameDateDefault, clampGameDateToBounds, picksSession } from "./picks-session.js";
import { loadPicks } from "./picks-load.js";
import { refreshGameData } from "./picks-refresh.js";
import { wireRefreshGame } from "./slate-views.js";

wireRefreshGame(refreshGameData);

initGameDateDefault();

els.refreshBtn?.addEventListener("click", (e) => {
  e.preventDefault();
  loadPicks();
});

if (els.gameDateInput) {
  els.gameDateInput.addEventListener("change", () => {
    clampGameDateToBounds();
    const v = els.gameDateInput.value?.trim();
    if (v) picksSession.lastExplicitGameDate = v;
    loadPicks();
  });
}

loadPicks();
