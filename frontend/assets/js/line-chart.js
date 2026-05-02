import { lineHistoryUrl } from "./api-urls.js";
import { readHttpErrorDetail } from "./fetch-utils.js";
import { decimalToAmerican } from "./format.js";

const DEC_EPS = 1e-4;

function escapeAttr(s) {
  return String(s)
    .replace(/&/g, "&amp;")
    .replace(/"/g, "&quot;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");
}

function timeMs(t) {
  const ms = Date.parse(t);
  return Number.isFinite(ms) ? ms : 0;
}

function dec(p) {
  return Number(p.decimal_odds);
}

function sortByTime(pts) {
  return [...pts].sort((a, b) => timeMs(a.t) - timeMs(b.t));
}

/**
 * Vertices where odds level changes (or range extends), not every 5‑minute sample.
 */
function buildOddsStepCorners(sorted) {
  if (!sorted.length) return [];
  const c = [sorted[0]];
  for (let i = 1; i < sorted.length; i++) {
    if (Math.abs(dec(sorted[i]) - dec(sorted[i - 1])) >= DEC_EPS) {
      const prev = sorted[i - 1];
      const last = c[c.length - 1];
      if (prev.t !== last.t || Math.abs(dec(prev) - dec(last)) >= DEC_EPS) {
        c.push(prev);
      }
      c.push(sorted[i]);
    }
  }
  const lr = sorted[sorted.length - 1];
  const lc = c[c.length - 1];
  if (lr && lc && lr.t !== lc.t && Math.abs(dec(lr) - dec(lc)) < DEC_EPS) {
    c.push(lr);
  }
  return c;
}

/**
 * Step path: horizontal through plateaus, vertical at price changes.
 */
function expandStepPolyline(corners) {
  const out = [];
  for (let i = 0; i < corners.length; i++) {
    out.push(corners[i]);
    if (i < corners.length - 1) {
      const a = corners[i];
      const b = corners[i + 1];
      if (Math.abs(dec(b) - dec(a)) >= DEC_EPS) {
        out.push({ ...a, t: b.t, decimal_odds: dec(a), book: a.book });
      }
    }
  }
  return out;
}

function linDecimals(a, b, count) {
  if (count < 2) return [a, b];
  const out = [];
  for (let i = 0; i < count; i++) {
    out.push(a + ((b - a) * i) / (count - 1));
  }
  return out;
}

function xTickMillis(tMin, tMax, count) {
  if (count < 2) return [tMin, tMax];
  const span = Math.max(tMax - tMin, 1);
  const out = [];
  for (let i = 0; i < count; i++) {
    out.push(Math.round(tMin + (span * i) / (count - 1)));
  }
  return out;
}

/** Shorter time on x-axis when same calendar day as ref. */
function formatXTick(ms, refMs) {
  try {
    const d = new Date(ms);
    if (Number.isNaN(d.getTime())) return "";
    const ref = new Date(refMs);
    const sameDay =
      ref.getTime() &&
      d.getFullYear() === ref.getFullYear() &&
      d.getMonth() === ref.getMonth() &&
      d.getDate() === ref.getDate();
    if (sameDay) {
      return d.toLocaleTimeString(undefined, {
        hour: "numeric",
        minute: "2-digit",
      });
    }
    return d.toLocaleString(undefined, {
      month: "short",
      day: "numeric",
      hour: "numeric",
      minute: "2-digit",
    });
  } catch (_) {
    return "";
  }
}

/**
 * @returns {string | null} Full chart HTML for mountEl, or null if not enough data / flat line.
 */
function tryBuildChartMarkup(data) {
  const raw = Array.isArray(data.points) ? data.points : [];
  const sorted = sortByTime(raw);
  if (sorted.length < 2) return null;

  const corners = buildOddsStepCorners(sorted);
  const linePts = expandStepPolyline(corners);
  if (linePts.length < 2) return null;

  const allDec = sorted.map((p) => dec(p));
  const minD = Math.min(...allDec);
  const maxD = Math.max(...allDec);
  const span = Math.max(maxD - minD, 0.02);

  const tMin = timeMs(sorted[0].t);
  const tMax = timeMs(sorted[sorted.length - 1].t);
  const tSpan = Math.max(tMax - tMin, 1);

  const w = 480;
  const h = 132;
  const padL = 54;
  const padR = 12;
  const padT = 20;
  const padB = 42;
  const innerW = w - padL - padR;
  const innerH = h - padT - padB;

  const xAtTime = (t) => padL + innerW * ((timeMs(t) - tMin) / tSpan);
  const yAt = (d) => padT + innerH - ((d - minD) / span) * innerH;

  const yTickCount = span < 0.04 ? 4 : 5;
  const yTicks = linDecimals(minD, maxD, yTickCount);
  const xTickCount = Math.min(6, Math.max(3, Math.ceil(tSpan / (45 * 60 * 1000)) + 1));
  const xTicksMs = xTickMillis(tMin, tMax, xTickCount);

  const gridH = yTicks
    .map((dv) => {
      const y = yAt(dv).toFixed(1);
      return `<line class="line-chart-grid" x1="${padL}" y1="${y}" x2="${padL + innerW}" y2="${y}" />`;
    })
    .join("");

  const gridV = xTicksMs
    .map((ms) => {
      const x = (padL + innerW * ((ms - tMin) / tSpan)).toFixed(1);
      return `<line class="line-chart-grid" x1="${x}" y1="${padT}" x2="${x}" y2="${padT + innerH}" />`;
    })
    .join("");

  const frame = `<rect class="line-chart-plot-frame" x="${padL}" y="${padT}" width="${innerW}" height="${innerH}" />`;

  const yLabels = yTicks
    .map((dv) => {
      const y = yAt(dv).toFixed(1);
      const lab = decimalToAmerican(dv);
      return `<text class="line-chart-y-tick" x="${padL - 8}" y="${y}" text-anchor="end" dominant-baseline="middle">${escapeAttr(
        String(lab)
      )}</text>`;
    })
    .join("");

  const xLabels = xTicksMs
    .map((ms, i) => {
      const x = padL + innerW * ((ms - tMin) / tSpan);
      const lab = formatXTick(ms, tMin);
      const anchor = i === 0 ? "start" : i === xTicksMs.length - 1 ? "end" : "middle";
      const xAdj =
        i === 0 ? Math.min(x + 2, padL + innerW - 2) : i === xTicksMs.length - 1 ? Math.max(x - 2, padL + 2) : x;
      return `<text class="line-chart-x-tick" x="${xAdj.toFixed(1)}" y="${h - 10}" text-anchor="${anchor}">${escapeAttr(
        lab
      )}</text>`;
    })
    .join("");

  const yTitleY = padT + innerH / 2;
  const yTitle = `<text class="line-chart-y-title" transform="translate(10,${yTitleY}) rotate(-90)" text-anchor="middle">American odds</text>`;

  const pathD = linePts
    .map((p, i) => {
      const x = xAtTime(p.t).toFixed(1);
      const y = yAt(dec(p)).toFixed(1);
      return `${i === 0 ? "M" : "L"} ${x} ${y}`;
    })
    .join(" ");

  return `
    <div class="line-chart-wrap" role="img" aria-label="Best decimal odds over time among tracked books">
      <svg class="line-chart-svg" viewBox="0 0 ${w} ${h}" preserveAspectRatio="xMidYMid meet">
        ${gridH}
        ${gridV}
        ${frame}
        ${yTitle}
        ${yLabels}
        ${xLabels}
        <path class="line-chart-path" d="${pathD}" fill="none" />
      </svg>
    </div>
  `;
}

function emptyChartMessage(historyMeta) {
  const hm = historyMeta;
  const extra =
    hm && typeof hm.upstream_error === "string" && hm.upstream_error
      ? ` (${escapeAttr(hm.upstream_error)})`
      : "";
  return `<p class="chart-empty">Not enough samples yet — reload the slate a few times, or check Odds API historical access.${extra}</p>`;
}

/**
 * @param {HTMLElement} mountEl
 * @param {{ sportKey: string, eventId: string, pick: string }} keys
 */
export async function mountLineHistoryChart(mountEl, keys) {
  const { sportKey, eventId, pick, marketKey, commenceTime } = keys;
  const baseOpts = { marketKey, commenceTime };
  const urlLocal = lineHistoryUrl(sportKey, eventId, pick, { ...baseOpts, backfill: 0 });
  const urlMerged = lineHistoryUrl(sportKey, eventId, pick, { ...baseOpts, backfill: 1 });

  mountEl.innerHTML = `<p class="chart-loading">Loading chart…</p>`;

  /** Full history (local + upstream); starts immediately so upstream runs in parallel with local-only fetch. */
  const mergedPromise = fetch(urlMerged).then(async (res) => {
    if (!res.ok) throw new Error(await readHttpErrorDetail(res));
    return res.json();
  });

  let dataLocal;
  try {
    const res0 = await fetch(urlLocal);
    if (!res0.ok) {
      const detail = await readHttpErrorDetail(res0);
      mountEl.innerHTML = `<p class="chart-empty">${escapeAttr(detail)}</p>`;
      return;
    }
    dataLocal = await res0.json();
  } catch (_) {
    mountEl.innerHTML = `<p class="chart-empty">Could not load history (network).</p>`;
    return;
  }

  if (!mountEl.isConnected) return;

  const quick = tryBuildChartMarkup(dataLocal);
  if (quick) {
    mountEl.innerHTML = quick;
  }

  let dataMerged;
  try {
    dataMerged = await mergedPromise;
  } catch (_) {
    if (!mountEl.isConnected) return;
    if (quick) {
      mountEl.innerHTML = quick;
      return;
    }
    mountEl.innerHTML = `<p class="chart-empty">Could not load history (network).</p>`;
    return;
  }

  if (!mountEl.isConnected) return;

  const finalMarkup = tryBuildChartMarkup(dataMerged);
  if (finalMarkup) {
    mountEl.innerHTML = finalMarkup;
    return;
  }
  if (quick) {
    mountEl.innerHTML = quick;
    return;
  }
  mountEl.innerHTML = emptyChartMessage(dataMerged.history_meta);
}
