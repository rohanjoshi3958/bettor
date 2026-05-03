import { decimalToAmerican, escapeHtml, pickNum } from "./format.js";

export function fillPickCards(container, picks, _gameKeys) {
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
    const pickStr = String(p.pick ?? "");
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
        <span class="pick-name">${escapeHtml(pickStr)}</span>
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
