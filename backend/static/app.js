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

let gamePanelIdSeq = 0;
let leaguePanelIdSeq = 0;

/** Prop leagues first, then soccer / others A–Z by title */
const LEAGUE_SORT_ORDER = [
  "basketball_nba",
  "americanfootball_nfl",
  "baseball_mlb",
];

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
/** Per YYYY-MM-DD, last non-empty `games` from a successful /api/picks response (session only). */
const lastGoodSlateByDate = new Map();

function localDateString(d = new Date()) {
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, "0");
  const day = String(d.getDate()).padStart(2, "0");
  return `${y}-${m}-${day}`;
}

/**
 * YYYY-MM-DD for API calls. Does not overwrite the date control when it is briefly empty (e.g. focus quirks on Reload).
 */
function getGameDateString() {
  const fallback = localDateString();
  if (!gameDateInput) {
    lastExplicitGameDate = lastExplicitGameDate || fallback;
    return lastExplicitGameDate;
  }
  const v = gameDateInput.value?.trim();
  if (v) {
    lastExplicitGameDate = v;
    return v;
  }
  if (lastExplicitGameDate) return lastExplicitGameDate;
  gameDateInput.value = fallback;
  lastExplicitGameDate = fallback;
  return fallback;
}

function initGameDateDefault() {
  if (gameDateInput) {
    if (!gameDateInput.value?.trim()) {
      gameDateInput.value = localDateString();
    }
    lastExplicitGameDate = gameDateInput.value.trim();
  } else {
    lastExplicitGameDate = localDateString();
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

function updateGameSectionFromPayload(section, game) {
  const picksWrap = section.querySelector(".game-picks");
  const countEl = section.querySelector(".game-pick-count");
  const timeEl = section.querySelector(".game-time");
  if (!game) {
    fillPickCards(picksWrap, []);
    if (countEl) countEl.textContent = "0 lines";
    return;
  }
  const picks = game.picks || [];
  if (countEl) {
    countEl.textContent =
      picks.length === 1 ? "1 best bet" : `${picks.length} best bets`;
  }
  if (timeEl && game.commence_time) {
    timeEl.textContent = formatWhen(game.commence_time);
  }
  fillPickCards(picksWrap, picks);
}

function createGameSection(game, options = {}) {
  const { hideSportLabel = false } = options;
  const picks = Array.isArray(game.picks) ? game.picks : [];
  const expanded =
    options.expanded !== undefined ? options.expanded : picks.length > 0;

  const section = document.createElement("section");
  section.className = "game-section";
  section.dataset.sportKey = game.sport_key;
  section.dataset.eventId = game.event_id;

  const panelId = `game-picks-panel-${++gamePanelIdSeq}`;
  const sportLine = hideSportLabel
    ? ""
    : `<div class="sport">${escapeHtml(game.sport_title)}</div>`;
  section.innerHTML = `
    <div class="game-top">
      <button type="button" class="game-toggle" aria-expanded="${expanded}" aria-controls="${panelId}">
        <span class="game-chevron" aria-hidden="true"></span>
        <div class="game-label">
          ${sportLine}
          <div class="game-matchup">${escapeHtml(game.matchup)}</div>
          <p class="game-time">${escapeHtml(formatWhen(game.commence_time))}</p>
        </div>
        <span class="game-pick-count">${picks.length} best bet${picks.length === 1 ? "" : "s"}</span>
      </button>
      <button type="button" class="btn btn-sm game-refresh">Update odds</button>
    </div>
    <div class="game-panel ${expanded ? "" : "hidden"}" id="${panelId}" role="region">
      <div class="game-picks"></div>
    </div>
  `;

  const picksWrap = section.querySelector(".game-picks");
  fillPickCards(picksWrap, picks);

  const toggle = section.querySelector(".game-toggle");
  const panel = section.querySelector(".game-panel");
  const chevron = section.querySelector(".game-chevron");

  toggle.addEventListener("click", () => {
    const isOpen = !panel.classList.contains("hidden");
    if (isOpen) {
      panel.classList.add("hidden");
      toggle.setAttribute("aria-expanded", "false");
      chevron.classList.remove("game-chevron-open");
    } else {
      panel.classList.remove("hidden");
      toggle.setAttribute("aria-expanded", "true");
      chevron.classList.add("game-chevron-open");
    }
  });

  const refreshBtnGame = section.querySelector(".game-refresh");
  refreshBtnGame.addEventListener("click", async (e) => {
    e.stopPropagation();
    await refreshSingleGame(section);
  });

  if (expanded) {
    chevron.classList.add("game-chevron-open");
  }

  return section;
}

function createLeagueSection(league) {
  const leagueWrap = document.createElement("section");
  leagueWrap.className = "league-box";
  leagueWrap.dataset.sportKey = league.sport_key;

  const leagueGames = league.games || [];
  const hasAnyPicks = leagueGames.some((g) => (g.picks || []).length > 0);
  const leagueOpen = hasAnyPicks;

  const panelId = `league-panel-${++leaguePanelIdSeq}`;
  leagueWrap.innerHTML = `
    <button type="button" class="league-toggle" aria-expanded="${leagueOpen}" aria-controls="${panelId}">
      <span class="league-chevron" aria-hidden="true"></span>
      <div class="league-title-wrap">
        <h2 class="league-title">${escapeHtml(league.sport_title)}</h2>
        <p class="league-meta">${leagueGames.length} game${leagueGames.length === 1 ? "" : "s"} this day</p>
      </div>
      <span class="league-badge">${leagueGames.length}</span>
    </button>
    <div class="league-panel ${leagueOpen ? "" : "hidden"}" id="${panelId}" role="region">
      <div class="league-games"></div>
    </div>
  `;

  const gamesWrap = leagueWrap.querySelector(".league-games");
  for (const game of leagueGames) {
    gamesWrap.appendChild(createGameSection(game, { hideSportLabel: true }));
  }

  const leagueToggle = leagueWrap.querySelector(".league-toggle");
  const leaguePanel = leagueWrap.querySelector(".league-panel");
  const leagueChevron = leagueWrap.querySelector(".league-chevron");

  if (leagueOpen) {
    leagueChevron.classList.add("league-chevron-open");
  }

  leagueToggle.addEventListener("click", () => {
    const isOpen = !leaguePanel.classList.contains("hidden");
    if (isOpen) {
      leaguePanel.classList.add("hidden");
      leagueToggle.setAttribute("aria-expanded", "false");
      leagueChevron.classList.remove("league-chevron-open");
    } else {
      leaguePanel.classList.remove("hidden");
      leagueToggle.setAttribute("aria-expanded", "true");
      leagueChevron.classList.add("league-chevron-open");
    }
  });

  return leagueWrap;
}

function cloneGames(games) {
  return JSON.parse(JSON.stringify(games));
}

function renderGameList(games) {
  cardsEl.innerHTML = "";
  gamePanelIdSeq = 0;
  leaguePanelIdSeq = 0;
  const frag = document.createDocumentFragment();
  const leagues = groupGamesByLeague(games);
  for (const league of leagues) {
    frag.appendChild(createLeagueSection(league));
  }
  cardsEl.appendChild(frag);
}

async function refreshSingleGame(section) {
  const sportKey = section.dataset.sportKey;
  const eventId = section.dataset.eventId;
  const btn = section.querySelector(".game-refresh");
  if (!sportKey || !eventId || !btn) return;

  const prev = btn.textContent;
  btn.disabled = true;
  btn.textContent = "Updating…";

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
    updateGameSectionFromPayload(section, data.game);

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
  } catch (e) {
    errorEl.textContent =
      e instanceof Error ? e.message : "Could not refresh this game.";
    errorEl.classList.remove("hidden");
  } finally {
    btn.disabled = false;
    btn.textContent = prev;
  }
}

async function loadPicks() {
  const requestedDate = getGameDateString();
  errorEl.classList.add("hidden");
  statusPill.textContent = "Loading…";
  statusPill.className = "pill pill-muted";

  try {
    const res = await fetch(picksUrl(), {
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
      lastGoodSlateByDate.set(responseDate, cloneGames(games));
      if (staleSlateBanner) staleSlateBanner.classList.add("hidden");
      emptyEl.classList.add("hidden");
      renderGameList(games);
      return;
    }

    const cached = lastGoodSlateByDate.get(responseDate);
    if (cached && cached.length > 0) {
      emptyEl.classList.add("hidden");
      if (staleSlateBanner) {
        staleSlateBanner.textContent =
          "Latest reload returned no qualifying lines for this day. Showing your previous slate for this game day — try again in a moment, or use Update odds on a game.";
        staleSlateBanner.classList.remove("hidden");
      }
      relaxedBanner?.classList.add("hidden");
      renderGameList(cloneGames(cached));
      return;
    }

    if (staleSlateBanner) staleSlateBanner.classList.add("hidden");
    cardsEl.innerHTML = "";
    const gd = data.game_date || "";
    const tgt = data.target_implied_probability ?? 0.52;
    const rel = data.relaxed_implied_probability ?? 0.5;
    let msg = "No games with picks for this day.";
    if (gd) msg = `No games with picks for ${gd} (${data.timezone || browserTz}).`;
    msg += ` Each line needs ~${Math.round(tgt * 100)}%+ implied (we also try ${Math.round(rel * 100)}%+) and the same outcome priced at two of your books (DK, FD, Fanatics, theScore). Try another date or a busier sports day.`;
    emptyEl.textContent = msg;
    emptyEl.classList.remove("hidden");
  } catch (e) {
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
    const v = gameDateInput.value?.trim();
    if (v) lastExplicitGameDate = v;
    loadPicks();
  });
}
loadPicks();
