export function civilDateInTimeZone(d, timeZone) {
  return new Intl.DateTimeFormat("en-CA", {
    timeZone,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).format(d);
}

/** Earliest selectable: today's date in `timeZone`. */
export function minPickDateString(timeZone) {
  return civilDateInTimeZone(new Date(), timeZone);
}

/** Latest selectable: today + offset in `timeZone`. */
export function maxPickDateString(timeZone, offsetDays) {
  const today = civilDateInTimeZone(new Date(), timeZone);
  const [y, m, d] = today.split("-").map((x) => parseInt(x, 10, 10));
  const anchor = new Date(
    Date.UTC(y, m - 1, d + offsetDays, 12, 0, 0),
  );
  return civilDateInTimeZone(anchor, timeZone);
}
