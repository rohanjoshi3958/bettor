export function formatWhen(iso) {
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

export function decimalToAmerican(d) {
  const dec = Number(d);
  if (!Number.isFinite(dec) || dec <= 1) return "—";
  if (dec >= 2) {
    const x = Math.round((dec - 1) * 100);
    return `+${x}`;
  }
  const x = Math.round(-100 / (dec - 1));
  return String(x);
}

export function escapeHtml(s) {
  return String(s)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

export function pickNum(x) {
  const n = Number(x);
  return Number.isFinite(n) ? n : NaN;
}
