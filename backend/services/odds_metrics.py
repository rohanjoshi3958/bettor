"""In-process metrics for Odds API dependency health (BET-9).

Process-local counters, gauges, and latency histograms with a Prometheus text
exposition. No credentials or request payloads are stored — only endpoint,
market, sport_key, outcome, and numeric status/latency fields.
"""

from __future__ import annotations

import threading
import time
from collections import defaultdict
from dataclasses import dataclass
from typing import Any

# Fixed latency buckets (seconds) — coarse enough for quota/latency triage.
_LATENCY_BUCKETS = (0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0, 60.0)

_LabelKey = tuple[tuple[str, str], ...]

_lock = threading.Lock()

# Counters keyed by sorted label tuples → int
_counters: defaultdict[str, defaultdict[_LabelKey, int]] = defaultdict(lambda: defaultdict(int))
# Histograms: name → labels → {bucket_le: count, "_sum": float, "_count": float}
_histograms: defaultdict[str, defaultdict[_LabelKey, dict[str, float]]] = defaultdict(
    lambda: defaultdict(dict)
)
# Gauges: name → float (last observed)
_gauges: dict[str, float] = {}
_started_at = time.time()


def reset_metrics() -> None:
    """Clear all series (tests / process restart simulation)."""
    with _lock:
        _counters.clear()
        _histograms.clear()
        _gauges.clear()
        global _started_at
        _started_at = time.time()


def _labels(**kwargs: str | int | None) -> _LabelKey:
    items: list[tuple[str, str]] = []
    for key, value in sorted(kwargs.items()):
        if value is None:
            continue
        items.append((key, str(value)))
    return tuple(items)


def _inc(name: str, labels: _LabelKey, amount: int = 1) -> None:
    with _lock:
        _counters[name][labels] += amount


def _observe(name: str, labels: _LabelKey, value: float) -> None:
    with _lock:
        series = _histograms[name][labels]
        if not series:
            for le in _LATENCY_BUCKETS:
                series[str(le)] = 0.0
            series["+Inf"] = 0.0
            series["_sum"] = 0.0
            series["_count"] = 0.0
        series["_sum"] += value
        series["_count"] += 1.0
        for le in _LATENCY_BUCKETS:
            if value <= le:
                series[str(le)] += 1.0
        series["+Inf"] += 1.0


def _set_gauge(name: str, value: float) -> None:
    with _lock:
        _gauges[name] = value


# ---------------------------------------------------------------------------
# Public record helpers
# ---------------------------------------------------------------------------

def record_odds_api_request(
    *,
    endpoint: str,
    market: str,
    sport_key: str,
    outcome: str,
    duration_seconds: float,
    status_code: int | None = None,
) -> None:
    """Record one outbound Odds API attempt (including retries)."""
    base = _labels(endpoint=endpoint, market=market, sport_key=sport_key)
    _inc(
        "bettor_odds_api_requests_total",
        _labels(endpoint=endpoint, market=market, sport_key=sport_key, outcome=outcome),
    )
    if outcome == "success":
        _inc("bettor_odds_api_success_total", base)
    else:
        _inc(
            "bettor_odds_api_errors_total",
            _labels(endpoint=endpoint, market=market, sport_key=sport_key, outcome=outcome),
        )
    if status_code is not None and status_code > 0:
        _inc(
            "bettor_odds_api_http_status_total",
            _labels(endpoint=endpoint, market=market, status_code=status_code),
        )
        if status_code == 429:
            _inc("bettor_odds_api_rate_limit_total", base)
        elif status_code == 402:
            _inc("bettor_odds_api_quota_exhausted_total", base)
    if outcome == "timeout":
        _inc("bettor_odds_api_timeouts_total", base)
    _observe("bettor_odds_api_request_duration_seconds", base, duration_seconds)


def note_odds_api_quota_headers(headers: Any) -> None:
    """Update quota gauges from Odds API response headers when present."""
    if headers is None:
        return
    remaining = headers.get("x-requests-remaining")
    used = headers.get("x-requests-used")
    try:
        if remaining is not None and str(remaining).strip() != "":
            _set_gauge("bettor_odds_api_quota_remaining", float(remaining))
    except (TypeError, ValueError):
        pass
    try:
        if used is not None and str(used).strip() != "":
            _set_gauge("bettor_odds_api_quota_used", float(used))
    except (TypeError, ValueError):
        pass


def record_cache_result(result: str) -> None:
    """result: hit | miss | bypass"""
    _inc("bettor_picks_cache_requests_total", _labels(result=result))


def record_odds_warning(*, kind: str, has_usable_response: bool) -> None:
    """kind: rate_limit | payment_required"""
    _inc(
        "bettor_odds_api_warnings_total",
        _labels(kind=kind, partial="true" if has_usable_response else "false"),
    )


def record_relaxed_fallback() -> None:
    _inc("bettor_odds_relaxed_fallback_total", ())


def record_http_request(*, method: str, path: str, status_code: int, duration_seconds: float) -> None:
    labels = _labels(method=method, path=path, status_code=status_code)
    _inc("bettor_http_requests_total", labels)
    _observe(
        "bettor_http_request_duration_seconds",
        _labels(method=method, path=path),
        duration_seconds,
    )


# ---------------------------------------------------------------------------
# Snapshots / exposition
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class MetricsSnapshot:
    counters: dict[str, list[dict[str, Any]]]
    histograms: dict[str, list[dict[str, Any]]]
    gauges: dict[str, float]
    uptime_seconds: float


def snapshot() -> MetricsSnapshot:
    with _lock:
        counters: dict[str, list[dict[str, Any]]] = {}
        for counter_name, counter_series in _counters.items():
            rows = []
            for labels, value in counter_series.items():
                rows.append({"labels": dict(labels), "value": value})
            counters[counter_name] = sorted(rows, key=lambda r: sorted(r["labels"].items()))

        histograms: dict[str, list[dict[str, Any]]] = {}
        for hist_name, hist_series in _histograms.items():
            rows = []
            for labels, data in hist_series.items():
                buckets = {
                    k: int(v)
                    for k, v in data.items()
                    if k not in ("_sum", "_count")
                }
                rows.append(
                    {
                        "labels": dict(labels),
                        "buckets": buckets,
                        "sum": data.get("_sum", 0.0),
                        "count": int(data.get("_count", 0.0)),
                    }
                )
            histograms[hist_name] = sorted(rows, key=lambda r: sorted(r["labels"].items()))

        gauges = dict(_gauges)
        uptime = time.time() - _started_at

    return MetricsSnapshot(
        counters=counters,
        histograms=histograms,
        gauges=gauges,
        uptime_seconds=round(uptime, 3),
    )


def _prom_labels(labels: dict[str, str]) -> str:
    if not labels:
        return ""
    parts = ",".join(f'{k}="{v}"' for k, v in sorted(labels.items()))
    return "{" + parts + "}"


def render_prometheus() -> str:
    snap = snapshot()
    lines: list[str] = [
        "# HELP bettor_uptime_seconds Process uptime since metrics reset.",
        "# TYPE bettor_uptime_seconds gauge",
        f"bettor_uptime_seconds {snap.uptime_seconds}",
    ]

    help_text = {
        "bettor_odds_api_requests_total": "Outbound Odds API HTTP attempts by outcome.",
        "bettor_odds_api_success_total": "Successful Odds API responses (2xx).",
        "bettor_odds_api_errors_total": "Failed Odds API attempts by outcome.",
        "bettor_odds_api_http_status_total": "Odds API HTTP status code distribution.",
        "bettor_odds_api_rate_limit_total": "Odds API HTTP 429 rate-limit responses.",
        "bettor_odds_api_quota_exhausted_total": "Odds API HTTP 402 quota/billing responses.",
        "bettor_odds_api_timeouts_total": "Odds API request timeouts.",
        "bettor_odds_api_request_duration_seconds": "Odds API upstream request latency.",
        "bettor_odds_api_quota_remaining": "Last observed x-requests-remaining from Odds API.",
        "bettor_odds_api_quota_used": "Last observed x-requests-used from Odds API.",
        "bettor_picks_cache_requests_total": "Picks cache lookups by result (hit/miss/bypass).",
        "bettor_odds_api_warnings_total": "User-facing Odds API warning frequency.",
        "bettor_odds_relaxed_fallback_total": "Times ranking used the relaxed implied-probability floor.",
        "bettor_http_requests_total": "Inbound HTTP requests (excludes static assets and /api/metrics).",
        "bettor_http_request_duration_seconds": "Inbound HTTP request latency (total handler time).",
    }

    for name, rows in sorted(snap.counters.items()):
        lines.append(f"# HELP {name} {help_text.get(name, name)}")
        lines.append(f"# TYPE {name} counter")
        for row in rows:
            lines.append(f"{name}{_prom_labels(row['labels'])} {row['value']}")

    for name, rows in sorted(snap.histograms.items()):
        lines.append(f"# HELP {name} {help_text.get(name, name)}")
        lines.append(f"# TYPE {name} histogram")
        for row in rows:
            base = row["labels"]
            # Emit buckets in ascending order with +Inf last.
            ordered = sorted(
                ((k, v) for k, v in row["buckets"].items() if k != "+Inf"),
                key=lambda kv: float(kv[0]),
            )
            for le, count in ordered:
                labels = {**base, "le": le}
                lines.append(f"{name}_bucket{_prom_labels(labels)} {count}")
            labels_inf = {**base, "le": "+Inf"}
            lines.append(
                f"{name}_bucket{_prom_labels(labels_inf)} {row['buckets'].get('+Inf', 0)}"
            )
            lines.append(f"{name}_sum{_prom_labels(base)} {row['sum']}")
            lines.append(f"{name}_count{_prom_labels(base)} {row['count']}")

    for name, value in sorted(snap.gauges.items()):
        lines.append(f"# HELP {name} {help_text.get(name, name)}")
        lines.append(f"# TYPE {name} gauge")
        lines.append(f"{name} {value}")

    lines.append("")
    return "\n".join(lines)


def snapshot_dict() -> dict[str, Any]:
    snap = snapshot()
    return {
        "uptime_seconds": snap.uptime_seconds,
        "counters": snap.counters,
        "histograms": snap.histograms,
        "gauges": snap.gauges,
    }
