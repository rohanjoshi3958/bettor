export async function readHttpErrorDetail(res) {
  let detail = `HTTP ${res.status}`;
  try {
    const err = await res.json();
    const d = err.detail;
    if (typeof d === "string") detail = d;
    else if (Array.isArray(d) && d[0]?.msg) detail = d[0].msg;
  } catch (_) {
    /* ignore */
  }
  return detail;
}
