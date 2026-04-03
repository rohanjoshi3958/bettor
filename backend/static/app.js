const statusPill = document.getElementById("status-pill");
const refreshBtn = document.getElementById("refresh");
const gameDateInput = document.getElementById("game-date");
const cardsEl = document.getElementById("cards");
const errorEl = document.getElementById("error");
const oddsApiBanner = document.getElementById("odds-api-banner");
const demoBanner = document.getElementById("demo-banner");
const relaxedBanner = document.getElementById("relaxed-banner");
const staleSlateBanner = document.getElementById("stale-slate-banner");
const emptyEl = document.getElementById("empty");

const browserTz = Intl.DateTimeFormat().resolvedOptions().timeZone;

/** Must match backend `PICKS_FUTURE_END_OFFSET` (last allowed = today + this in `browserTz`). */
const PICKS_FUTURE_END_OFFSET = 5;

function civilDateInTimeZone(d, timeZone) {
  return new Intl.DateTimeFormat("en-CA", {
    timeZone,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).format(d);
}

/** Earliest selectable: today's date in `timeZone`. */
function minPickDateString(timeZone) {
  return civilDateInTimeZone(new Date(), timeZone);
}

/** Latest selectable: today + PICKS_FUTURE_END_OFFSET in `timeZone`. */
function maxPickDateString(timeZone) {
  const today = civilDateInTimeZone(new Date(), timeZone);
  const [y, m, d] = today.split("-").map((x) => parseInt(x, 10, 10));
  const anchor = new Date(
    Date.UTC(y, m - 1, d + PICKS_FUTURE_END_OFFSET, 12, 0, 0),
  );
  return civilDateInTimeZone(anchor, timeZone);
}

/** Set `<input type="date">` min/max and clamp to today … today+N in `browserTz`. */
function clampGameDateToBounds() {
  if (!gameDateInput) return;
  const min = minPickDateString(browserTz);
  const max = maxPickDateString(browserTz);
  gameDateInput.min = min;
  gameDateInput.max = max;
  const v = gameDateInput.value?.trim();
  if (v && (v < min || v > max)) {
    gameDateInput.value = min;
    lastExplicitGameDate = min;
  }
}

/** Flat list from last /api/picks; drill-down: leagues → games → game detail */
let slateGames = [];
let navState = { view: "leagues" };

/** Prop leagues first, then soccer / others A–Z by title */
const LEAGUE_SORT_ORDER = [
  "basketball_nba",
  "americanfootball_nfl",
  "baseball_mlb",
];

const PROP_SPORT_KEYS = new Set(LEAGUE_SORT_ORDER);

function groupGamesByLeague(games) {
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
/** Last calendar day the user chose (or server confirmed). Never cleared on Reload — avoids empty date inputs resetting the request to “today”. */
let lastExplicitGameDate = null;
/** Per YYYY-MM-DD, best snapshot of `games` for that day (session only; not downgraded by worse API responses). */
const lastGoodSlateByDate = new Map();

/** Abort in-flight slate fetch when the user changes date or hits Reload again. */
let picksFetchController = null;
/** Invalidate background prop backfill when the slate is replaced. */
let picksBackfillGeneration = 0;

/** Total pick rows across all games (for comparing slate quality). */
function slatePickTotal(games) {
  if (!Array.isArray(games)) return 0;
  return games.reduce(
    (acc, g) => acc + (Array.isArray(g.picks) ? g.picks.length : 0),
    0,
  );
}

/**
 * YYYY-MM-DD for API calls. Does not overwrite the date control when it is briefly empty (e.g. focus quirks on Reload).
 */
function getGameDateString() {
  const min = minPickDateString(browserTz);
  const max = maxPickDateString(browserTz);
  if (!gameDateInput) {
    let v = lastExplicitGameDate;
    if (!v || v < min || v > max) v = min;
    lastExplicitGameDate = v;
    return v;
  }
  const v = gameDateInput.value?.trim();
  if (v) {
    if (v < min || v > max) {
      gameDateInput.value = min;
      lastExplicitGameDate = min;
      return min;
    }
    lastExplicitGameDate = v;
    return v;
  }
  if (lastExplicitGameDate && lastExplicitGameDate >= min && lastExplicitGameDate <= max) {
    return lastExplicitGameDate;
  }
  gameDateInput.value = min;
  lastExplicitGameDate = min;
  return min;
}

function initGameDateDefault() {
  if (gameDateInput) {
    const min = minPickDateString(browserTz);
    const max = maxPickDateString(browserTz);
    gameDateInput.min = min;
    gameDateInput.max = max;
    const cur = gameDateInput.value?.trim();
    if (!cur || cur < min || cur > max) {
      gameDateInput.value = min;
    }
    lastExplicitGameDate = gameDateInput.value.trim();
    clampGameDateToBounds();
  } else {
    lastExplicitGameDate = minPickDateString(browserTz);
  }
}

function formatWhen(iso) {
  if (!iso) return "";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString(undefined, {
    weekday: "short",
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
  });
}

function decimalToAmerican(d) {
  const dec = Number(d);
  if (!Number.isFinite(dec) || dec <= 1) return "—";
  if (dec >= 2) {
    const x = Math.round((dec - 1) * 100);
    return `+${x}`;
  }
  const x = Math.round(-100 / (dec - 1));
  return String(x);
}

function picksUrl() {
  const date = getGameDateString();
  const params = new URLSearchParams({
    date,
    timezone: browserTz,
    picks_per_game: "5",
  });
  return `/api/picks?${params.toString()}`;
}

function singleGamePicksUrl(sportKey, eventId) {
  const date = getGameDateString();
  const params = new URLSearchParams({
    sport_key: sportKey,
    event_id: eventId,
    date,
    timezone: browserTz,
    picks_per_game: "5",
  });
  return `/api/picks/game?${params.toString()}`;
}

function escapeHtml(s) {
  return String(s)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function pickNum(x) {
  const n = Number(x);
  return Number.isFinite(n) ? n : NaN;
}

function fillPickCards(container, picks) {
  container.innerHTML = "";
  if (!picks.length) {
    const p = document.createElement("p");
    p.className = "game-picks-empty";
    p.textContent =
      "No qualifying lines right now (thresholds + two books). Try Reload slate or another time.";
    container.appendChild(p);
    return;
  }
  picks.forEach((p, i) => {
    const card = document.createElement("article");
    card.className = "card card-nested";
    const impl =
      p.implied_pct != null
        ? p.implied_pct
        : Number.isFinite(pickNum(p.implied_probability))
          ? (pickNum(p.implied_probability) * 100).toFixed(1)
          : "—";
    const avgDec = pickNum(p.avg_decimal_odds);
    const avgStr = Number.isFinite(avgDec) ? avgDec.toFixed(2) : "—";
    const edge = p.edge_pct;
    const edgeStr =
      edge != null && Number.isFinite(Number(edge)) ? String(edge) : "—";
    card.innerHTML = `
      <div class="card-top">
        <div>
          <div class="pick-rank">#${i + 1}</div>
        </div>
        <div class="edge-badge" title="Blend of implied % and line edge (higher = better)">Score ${escapeHtml(
          String(p.rank_score ?? "")
        )}</div>
      </div>
      <div class="pick-row">
        <span class="pick-label">Pick</span>
        <span class="pick-name">${escapeHtml(String(p.pick ?? ""))}</span>
      </div>
      <div class="meta">
        <span><strong>${escapeHtml(decimalToAmerican(p.best_decimal_odds))}</strong> best @ ${escapeHtml(
      String(p.best_book ?? "")
    )}</span>
        <span>Impl <strong>${impl}%</strong></span>
        <span>Edge <strong>+${escapeHtml(edgeStr)}%</strong></span>
        <span>Avg <strong>${avgStr}</strong></span>
      </div>
    `;
    container.appendChild(card);
  });
}

const LEAGUE_ICONS = {
  basketball_nba: `<svg class="league-tile-svg" viewBox="0 0 64 64" aria-hidden="true"><circle cx="32" cy="32" r="30" fill="#c45c1a"/><path fill="none" stroke="#1a140f" stroke-width="2" d="M32 2v60M2 32h60"/><ellipse cx="32" cy="32" rx="12" ry="30" fill="none" stroke="#1a140f" stroke-width="1.5"/></svg>`,
  americanfootball_nfl: `<svg class="league-tile-svg" viewBox="0 0 64 64" aria-hidden="true"><ellipse cx="32" cy="32" rx="28" ry="17" fill="#5c3d2e" stroke="#2a1a12" stroke-width="2"/><path fill="none" stroke="#e8ddd0" stroke-width="1.5" d="M14 32h36"/><path fill="#e8ddd0" d="M20 29h3v6h-3zm8 0h3v6h-3zm8 0h3v6h-3zm8 0h3v6h-3z" opacity=".85"/></svg>`,
  baseball_mlb: `<svg class="league-tile-svg" viewBox="0 0 64 64" aria-hidden="true"><circle cx="32" cy="32" r="28" fill="#f2f2f2" stroke="#1a1a1a" stroke-width="2"/><path fill="none" stroke="#c42d1a" stroke-width="1.8" d="M10 32c10-8 34-8 44 0M10 32c10 8 34 8 44 0"/></svg>`,
};

const SOCCER_LEAGUE_ICON = `<svg class="league-tile-svg" viewBox="0 0 64 64" aria-hidden="true"><circle cx="32" cy="32" r="28" fill="#fafafa" stroke="#1a1a1a" stroke-width="2"/><path fill="#1a1a1a" d="M32 6l5.5 9.5L32 22l-5.5-6.5L32 6zm0 52l-5.5-9.5L32 42l5.5 6.5L32 58zm-22-26l9.5 5.5L22 32l-9.5-5.5zm44 0L44.5 37.5 42 32l2.5-5.5L54 32zM32 18l-8 5 5 9h6l5-9-8-5z"/></svg>`;

function leagueIconHtml(sportKey) {
  if (LEAGUE_ICONS[sportKey]) return LEAGUE_ICONS[sportKey];
  if (String(sportKey || "").startsWith("soccer_")) return SOCCER_LEAGUE_ICON;
  return `<svg class="league-tile-svg" viewBox="0 0 64 64" aria-hidden="true"><circle cx="32" cy="32" r="26" fill="rgba(255,255,255,.08)" stroke="rgba(62,224,168,.45)" stroke-width="2"/><text x="32" y="41" text-anchor="middle" fill="#8b98a8" font-size="22" font-weight="700" font-family="system-ui,sans-serif">?</text></svg>`;
}

function findGameInSlate(sportKey, eventId) {
  const eid = String(eventId ?? "");
  return slateGames.find(
    (g) => g.sport_key === sportKey && String(g.event_id ?? "") === eid,
  );
}

function mergeGameFromApiPayload(sportKey, eventId, payload) {
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
    navState = { view: "games", sportKey: league.sport_key };
    renderSlateUI();
  });
  return btn;
}

function createGameRow(game) {
  const picks = Array.isArray(game.picks) ? game.picks : [];
  const btn = document.createElement("button");
  btn.type = "button";
  btn.className = "game-row-tile";
  btn.setAttribute("aria-label", `${game.matchup || "Game"}, ${picks.length} best bets`);
  btn.innerHTML = `
    <div class="game-row-main">
      <span class="game-row-matchup">${escapeHtml(game.matchup || "")}</span>
      <span class="game-row-time">${escapeHtml(formatWhen(game.commence_time))}</span>
    </div>
    <span class="game-row-badge">${picks.length} bet${picks.length === 1 ? "" : "s"}</span>
  `;
  btn.addEventListener("click", () => {
    navState = {
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
      navState = { view: "leagues" };
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
      navState = { view: "games", sportKey: sk };
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
    await refreshGameData(sk, eid, { silent: false, button: rb });
  });
  actions.appendChild(rb);
  root.appendChild(actions);
  const picksWrap = document.createElement("div");
  picksWrap.className = "game-picks game-picks-detail";
  fillPickCards(picksWrap, Array.isArray(game.picks) ? game.picks : []);
  root.appendChild(picksWrap);
  return root;
}

function renderSlateUI() {
  cardsEl.innerHTML = "";
  if (!slateGames.length) return;
  const leagues = groupGamesByLeague(slateGames);
  if (navState.view === "leagues") {
    const grid = document.createElement("div");
    grid.className = "league-grid";
    grid.setAttribute("role", "navigation");
    grid.setAttribute("aria-label", "Sports leagues");
    for (const league of leagues) {
      grid.appendChild(createLeagueTile(league));
    }
    cardsEl.appendChild(grid);
    return;
  }
  if (navState.view === "games") {
    const league = leagues.find((l) => l.sport_key === navState.sportKey);
    if (!league) {
      navState = { view: "leagues" };
      renderSlateUI();
      return;
    }
    cardsEl.appendChild(createGamesListView(league));
    return;
  }
  if (navState.view === "game") {
    const g = findGameInSlate(navState.sportKey, navState.eventId);
    if (!g) {
      navState = { view: "games", sportKey: navState.sportKey };
      renderSlateUI();
      return;
    }
    cardsEl.appendChild(createGameDetailView(g));
  }
}

function cloneGames(games) {
  return JSON.parse(JSON.stringify(games));
}

function renderGameList(games) {
  slateGames = games;
  navState = { view: "leagues" };
  renderSlateUI();
}

async function refreshGameData(sportKey, eventId, opts = {}) {
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
    if (!res.ok) {
      let detail = `HTTP ${res.status}`;
      try {
        const err = await res.json();
        const d = err.detail;
        if (typeof d === "string") detail = d;
        else if (Array.isArray(d) && d[0]?.msg) detail = d[0].msg;
      } catch (_) {
        /* ignore */
      }
      throw new Error(detail);
    }
    const data = await res.json();
    mergeGameFromApiPayload(sportKey, eventId, data.game);
    renderSlateUI();

    if (!silent) {
      if (typeof data.odds_api_warning === "string" && data.odds_api_warning) {
        if (oddsApiBanner) {
          oddsApiBanner.textContent = data.odds_api_warning;
          oddsApiBanner.classList.remove("hidden");
        }
      } else if (oddsApiBanner) {
        oddsApiBanner.classList.add("hidden");
      }

      if (data.source === "live") {
        statusPill.textContent = "Live odds";
        statusPill.className = "pill pill-live";
      }
    }
  } catch (e) {
    if (!silent) {
      errorEl.textContent =
        e instanceof Error ? e.message : "Could not refresh this game.";
      errorEl.classList.remove("hidden");
    }
  } finally {
    if (btn && !silent) {
      btn.disabled = false;
      btn.textContent = prev;
    }
  }
}

/**
 * NBA/NFL/MLB: re-fetch prop games that returned empty while siblings in the same league have picks.
 */
function maybeBackfillEmptyPropGames(source, generation) {
  if (source !== "live") return;
  const run = () => {
    if (generation !== picksBackfillGeneration) return;
    const leagues = groupGamesByLeague(slateGames);
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
        if (generation !== picksBackfillGeneration) return;
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

async function loadPicks() {
  clampGameDateToBounds();
  const requestedDate = getGameDateString();
  picksBackfillGeneration += 1;
  const backfillGen = picksBackfillGeneration;
  if (picksFetchController) {
    picksFetchController.abort();
  }
  picksFetchController = new AbortController();
  const { signal } = picksFetchController;

  errorEl.classList.add("hidden");
  statusPill.textContent = "Loading…";
  statusPill.className = "pill pill-muted";

  try {
    const res = await fetch(picksUrl(), {
      cache: "no-store",
      signal,
    });
    if (!res.ok) {
      let detail = `HTTP ${res.status}`;
      try {
        const err = await res.json();
        const d = err.detail;
        if (typeof d === "string") detail = d;
        else if (Array.isArray(d) && d[0]?.msg) detail = d[0].msg;
      } catch (_) {
        /* ignore */
      }
      throw new Error(detail);
    }
    const data = await res.json();

    if (signal.aborted) return;
    if (getGameDateString() !== requestedDate) return;

    if (typeof data.game_date === "string" && data.game_date) {
      lastExplicitGameDate = data.game_date;
      if (gameDateInput && gameDateInput.value?.trim() !== data.game_date) {
        gameDateInput.value = data.game_date;
      }
    }

    if (typeof data.odds_api_warning === "string" && data.odds_api_warning) {
      if (oddsApiBanner) {
        oddsApiBanner.textContent = data.odds_api_warning;
        oddsApiBanner.classList.remove("hidden");
      }
    } else if (oddsApiBanner) {
      oddsApiBanner.classList.add("hidden");
    }

    if (data.source === "demo") {
      demoBanner.textContent =
        "Demo data — add THE_ODDS_API_KEY in a .env file (get a key at the-odds-api.com) for live odds.";
      demoBanner.classList.remove("hidden");
    } else {
      demoBanner.classList.add("hidden");
    }

    if (relaxedBanner) {
      if (
        data.used_relaxed_implied_fallback &&
        (data.games || []).length > 0
      ) {
        const t = data.target_implied_probability ?? 0.52;
        const u = data.min_implied_probability ?? 0.5;
        relaxedBanner.textContent = `Nothing met ${Math.round(t * 100)}%+ implied — showing lines at ${Math.round(u * 100)}%+ instead (still need two books).`;
        relaxedBanner.classList.remove("hidden");
      } else {
        relaxedBanner.classList.add("hidden");
      }
    }

    statusPill.textContent = data.source === "live" ? "Live odds" : "Demo";
    statusPill.className =
      data.source === "live" ? "pill pill-live" : "pill pill-muted";

    const games = data.games || [];
    const responseDate = data.game_date || requestedDate;

    if (games.length > 0) {
      const prevSnap = lastGoodSlateByDate.get(responseDate);
      const nextTotal = slatePickTotal(games);
      const prevTotal = prevSnap ? slatePickTotal(prevSnap) : 0;
      if (!prevSnap || nextTotal >= prevTotal) {
        lastGoodSlateByDate.set(responseDate, cloneGames(games));
      }
      const cachedBetter =
        prevSnap && prevTotal > 0 && prevTotal > nextTotal;
      const toRender = cachedBetter ? cloneGames(prevSnap) : games;
      if (staleSlateBanner) staleSlateBanner.classList.add("hidden");
      if (cachedBetter) relaxedBanner?.classList.add("hidden");
      emptyEl.classList.add("hidden");
      renderGameList(toRender);
      maybeBackfillEmptyPropGames(data.source, backfillGen);
      return;
    }

    const cached =
      lastGoodSlateByDate.get(responseDate) ||
      lastGoodSlateByDate.get(requestedDate);
    if (cached && cached.length > 0) {
      emptyEl.classList.add("hidden");
      if (staleSlateBanner) {
        staleSlateBanner.textContent =
          "Latest reload returned no qualifying lines for this day. Showing your previous slate for this game day — try again in a moment, or use Update odds on a game.";
        staleSlateBanner.classList.remove("hidden");
      }
      relaxedBanner?.classList.add("hidden");
      renderGameList(cloneGames(cached));
      maybeBackfillEmptyPropGames(data.source, backfillGen);
      return;
    }

    if (staleSlateBanner) staleSlateBanner.classList.add("hidden");
    cardsEl.innerHTML = "";
    const gd = data.game_date || "";
    const tgt = data.target_implied_probability ?? 0.52;
    const rel = data.relaxed_implied_probability ?? 0.5;
    let msg = "No games with picks for this day.";
    if (gd) msg = `No games with picks for ${gd} (${data.timezone || browserTz}).`;
    msg += ` Each line needs ~${Math.round(tgt * 100)}%+ implied (we also try ${Math.round(rel * 100)}%+) and the same outcome priced at two of your books (DK, FD, Fanatics). Try another date or a busier sports day.`;
    emptyEl.textContent = msg;
    emptyEl.classList.remove("hidden");
  } catch (e) {
    if (e?.name === "AbortError") return;
    errorEl.textContent =
      e instanceof Error ? e.message : "Could not load picks.";
    errorEl.classList.remove("hidden");
    statusPill.textContent = "Error";
    statusPill.className = "pill pill-muted";
  }
}

initGameDateDefault();
refreshBtn.addEventListener("click", (e) => {
  e.preventDefault();
  loadPicks();
});
if (gameDateInput) {
  gameDateInput.addEventListener("change", () => {
    clampGameDateToBounds();
    const v = gameDateInput.value?.trim();
    if (v) lastExplicitGameDate = v;
    loadPicks();
  });
}
loadPicks();
