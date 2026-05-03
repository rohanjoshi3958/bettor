import { els } from "./elements.js";
import { formatWhen, escapeHtml } from "./format.js";
import { leagueIconHtml } from "./league-icons.js";
import { fillPickCards } from "./pick-cards.js";
import { picksSession } from "./picks-session.js";
import { groupGamesByLeague } from "./slate-model.js";

/** Injected from picks-refresh.js to avoid circular imports. */
let refreshGameDataImpl = async () => {};
export function wireRefreshGame(fn) {
  refreshGameDataImpl = fn;
}

export function findGameInSlate(sportKey, eventId) {
  const eid = String(eventId ?? "");
  return picksSession.slateGames.find(
    (g) => g.sport_key === sportKey && String(g.event_id ?? "") === eid,
  );
}

export function mergeGameFromApiPayload(sportKey, eventId, payload) {
  const g = findGameInSlate(sportKey, eventId);
  if (!g) return;
  if (!payload) {
    g.picks = [];
    return;
  }
  g.picks = Array.isArray(payload.picks) ? payload.picks : [];
  if (payload.commence_time) g.commence_time = payload.commence_time;
  if (payload.matchup) g.matchup = payload.matchup;
  if (payload.home_team) g.home_team = payload.home_team;
  if (payload.away_team) g.away_team = payload.away_team;
}

function createViewHeader({ backLabel, onBack, title, subtitle, iconHtml }) {
  const wrap = document.createElement("div");
  wrap.className = "view-header";
  const back = document.createElement("button");
  back.type = "button";
  back.className = "btn btn-back";
  back.textContent = backLabel;
  back.addEventListener("click", onBack);
  const main = document.createElement("div");
  main.className = "view-header-main";
  if (iconHtml) {
    const ic = document.createElement("div");
    ic.className = "view-header-icon";
    ic.innerHTML = iconHtml;
    main.appendChild(ic);
  }
  const h = document.createElement("h2");
  h.className = "view-title";
  h.textContent = title;
  main.appendChild(h);
  if (subtitle) {
    const p = document.createElement("p");
    p.className = "view-subtitle";
    p.textContent = subtitle;
    main.appendChild(p);
  }
  wrap.appendChild(back);
  wrap.appendChild(main);
  return wrap;
}

function createLeagueTile(league) {
  const btn = document.createElement("button");
  btn.type = "button";
  btn.className = "league-tile";
  const n = league.games?.length ?? 0;
  btn.setAttribute(
    "aria-label",
    `${league.sport_title}, ${n} game${n === 1 ? "" : "s"}`,
  );
  btn.innerHTML = `
    <span class="league-tile-icon">${leagueIconHtml(league.sport_key)}</span>
    <span class="league-tile-label">${escapeHtml(league.sport_title)}</span>
  `;
  btn.addEventListener("click", () => {
    picksSession.navState = { view: "games", sportKey: league.sport_key };
    renderSlateUI();
  });
  return btn;
}

function createGameRow(game) {
  const picks = Array.isArray(game.picks) ? game.picks : [];
  const btn = document.createElement("button");
  btn.type = "button";
  btn.className = "game-row-tile";
  btn.setAttribute(
    "aria-label",
    `${game.matchup || "Game"}, ${picks.length} best bets`,
  );
  btn.innerHTML = `
    <div class="game-row-main">
      <span class="game-row-matchup">${escapeHtml(game.matchup || "")}</span>
      <span class="game-row-time">${escapeHtml(formatWhen(game.commence_time))}</span>
    </div>
    <span class="game-row-badge">${picks.length} bet${picks.length === 1 ? "" : "s"}</span>
  `;
  btn.addEventListener("click", () => {
    picksSession.navState = {
      view: "game",
      sportKey: game.sport_key,
      eventId: String(game.event_id ?? ""),
    };
    renderSlateUI();
  });
  return btn;
}

function createGamesListView(league) {
  const root = document.createElement("div");
  root.className = "drill-view drill-games";
  const ng = league.games?.length ?? 0;
  const head = createViewHeader({
    backLabel: "← Leagues",
    onBack: () => {
      picksSession.navState = { view: "leagues" };
      renderSlateUI();
    },
    title: league.sport_title,
    subtitle: `${ng} game${ng === 1 ? "" : "s"} — open a matchup for best lines`,
    iconHtml: leagueIconHtml(league.sport_key),
  });
  root.appendChild(head);
  const list = document.createElement("div");
  list.className = "game-row-list";
  for (const g of league.games || []) {
    list.appendChild(createGameRow(g));
  }
  root.appendChild(list);
  return root;
}

function createGameDetailView(game) {
  const root = document.createElement("div");
  root.className = "drill-view drill-game-detail";
  const sk = game.sport_key;
  const eid = String(game.event_id ?? "");
  const head = createViewHeader({
    backLabel: "← Games",
    onBack: () => {
      picksSession.navState = { view: "games", sportKey: sk };
      renderSlateUI();
    },
    title: game.matchup || "Game",
    subtitle: formatWhen(game.commence_time),
    iconHtml: leagueIconHtml(sk),
  });
  root.appendChild(head);
  const actions = document.createElement("div");
  actions.className = "game-detail-actions";
  const rb = document.createElement("button");
  rb.type = "button";
  rb.className = "btn btn-sm";
  rb.textContent = "Update odds";
  rb.addEventListener("click", async () => {
    await refreshGameDataImpl(sk, eid, { silent: false, button: rb });
  });
  actions.appendChild(rb);
  root.appendChild(actions);
  const picksWrap = document.createElement("div");
  picksWrap.className = "game-picks game-picks-detail";
  fillPickCards(picksWrap, Array.isArray(game.picks) ? game.picks : [], {
    sportKey: sk,
    eventId: eid,
  });
  root.appendChild(picksWrap);
  return root;
}

export function renderSlateUI() {
  els.cards.innerHTML = "";
  if (!picksSession.slateGames.length) return;
  const leagues = groupGamesByLeague(picksSession.slateGames);
  const { navState } = picksSession;
  if (navState.view === "leagues") {
    const grid = document.createElement("div");
    grid.className = "league-grid";
    grid.setAttribute("role", "navigation");
    grid.setAttribute("aria-label", "Sports leagues");
    for (const league of leagues) {
      grid.appendChild(createLeagueTile(league));
    }
    els.cards.appendChild(grid);
    return;
  }
  if (navState.view === "games") {
    const league = leagues.find((l) => l.sport_key === navState.sportKey);
    if (!league) {
      picksSession.navState = { view: "leagues" };
      renderSlateUI();
      return;
    }
    els.cards.appendChild(createGamesListView(league));
    return;
  }
  if (navState.view === "game") {
    const g = findGameInSlate(navState.sportKey, navState.eventId);
    if (!g) {
      picksSession.navState = { view: "games", sportKey: navState.sportKey };
      renderSlateUI();
      return;
    }
    els.cards.appendChild(createGameDetailView(g));
  }
}

export function renderGameList(games) {
  picksSession.slateGames = games;
  picksSession.navState = { view: "leagues" };
  renderSlateUI();
}
