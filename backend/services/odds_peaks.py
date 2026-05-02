"""
SQLite-backed line tracking per game outcome:

- `outcome_peaks`: best decimal seen (high water mark) + book.
- `outcome_odds_snapshots`: append-only best price among allowed books on each fetch,
  for line-history charts.
"""

from __future__ import annotations

import asyncio
import os
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from services.odds.service import (
    _collect_h2h_prices,
    _collect_prop_prices,
    _format_prop_pick,
    _normalize_event_id,
)

_lock = asyncio.Lock()
_db_path: Path | None = None


def _default_db_path() -> Path:
    raw = os.environ.get("BETTOR_PEAK_DB", "").strip()
    if raw:
        return Path(raw).expanduser()
    return Path(__file__).resolve().parent.parent / "data" / "odds_peaks.sqlite"


def get_db_path() -> Path:
    global _db_path
    if _db_path is None:
        _db_path = _default_db_path()
        _db_path.parent.mkdir(parents=True, exist_ok=True)
    return _db_path


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(str(get_db_path()), timeout=30.0)
    conn.execute("PRAGMA journal_mode=WAL;")
    return conn


def init_schema_sync() -> None:
    with _connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS outcome_peaks (
                sport_key TEXT NOT NULL,
                event_id TEXT NOT NULL,
                outcome_key TEXT NOT NULL,
                pick_label TEXT NOT NULL,
                market_key TEXT,
                peak_decimal REAL NOT NULL,
                peak_book TEXT NOT NULL,
                peak_at TEXT NOT NULL,
                PRIMARY KEY (sport_key, event_id, outcome_key)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS outcome_odds_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                sport_key TEXT NOT NULL,
                event_id TEXT NOT NULL,
                outcome_key TEXT NOT NULL,
                pick_label TEXT NOT NULL,
                market_key TEXT,
                best_decimal REAL NOT NULL,
                best_book TEXT NOT NULL,
                observed_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_snapshots_line
            ON outcome_odds_snapshots (sport_key, event_id, pick_label, observed_at)
            """
        )
        conn.commit()


def _outcome_key_prop(mk: str, desc: str, oname: str, pt: float | None) -> str:
    pt_s = "" if pt is None else str(pt)
    return f"{mk}\x1f{desc}\x1f{oname}\x1f{pt_s}"


def _outcome_key_h2h(team_name: str) -> str:
    return f"h2h\x1f{team_name}"


def record_from_prop_event_sync(ev: dict[str, Any], sport_key: str, allowed_markets: set[str]) -> None:
    eid = _normalize_event_id(ev.get("id"))
    if not eid:
        return
    grouped = _collect_prop_prices(ev, allowed_markets)
    now = datetime.now(timezone.utc).isoformat()
    init_schema_sync()
    rows: list[tuple[str, str, str, str, str | None, float, str, str]] = []
    for key, book_prices in grouped.items():
        if not book_prices:
            continue
        mk, desc, oname, pt = key
        pick_label = _format_prop_pick(mk, desc, oname, pt)
        ok = _outcome_key_prop(mk, desc, oname, pt)
        best_book, best_dec = max(book_prices, key=lambda x: x[1])
        rows.append(
            (sport_key, eid, ok, pick_label, mk, float(best_dec), str(best_book), now)
        )
    if not rows:
        return
    sk = sport_key
    with _connect() as conn:
        for _, eid, ok, pick_label, mk, cand_dec, cand_book, ts in rows:
            conn.execute(
                """
                INSERT INTO outcome_peaks (sport_key, event_id, outcome_key, pick_label, market_key, peak_decimal, peak_book, peak_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(sport_key, event_id, outcome_key) DO UPDATE SET
                    peak_decimal = excluded.peak_decimal,
                    peak_book = excluded.peak_book,
                    peak_at = excluded.peak_at,
                    pick_label = excluded.pick_label,
                    market_key = excluded.market_key
                WHERE excluded.peak_decimal > outcome_peaks.peak_decimal
                """,
                (sk, eid, ok, pick_label, mk, cand_dec, cand_book, ts),
            )
            conn.execute(
                """
                INSERT INTO outcome_odds_snapshots
                (sport_key, event_id, outcome_key, pick_label, market_key, best_decimal, best_book, observed_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (sk, eid, ok, pick_label, mk, cand_dec, cand_book, ts),
            )
        _prune_old_snapshots(conn)
        conn.commit()


def record_from_h2h_event_sync(ev: dict[str, Any], sport_key: str) -> None:
    eid = _normalize_event_id(ev.get("id"))
    if not eid:
        return
    prices = _collect_h2h_prices(ev)
    now = datetime.now(timezone.utc).isoformat()
    init_schema_sync()
    rows: list[tuple[str, str, str, str, str, float, str, str]] = []
    for outcome_name, book_prices in prices.items():
        if not book_prices or not outcome_name:
            continue
        ok = _outcome_key_h2h(str(outcome_name).strip())
        best_book, best_dec = max(book_prices, key=lambda x: x[1])
        rows.append(
            (sport_key, eid, ok, str(outcome_name).strip(), "h2h", float(best_dec), str(best_book), now)
        )
    if not rows:
        return
    sk = sport_key
    with _connect() as conn:
        for _, eid, ok, pick_label, mk, cand_dec, cand_book, ts in rows:
            conn.execute(
                """
                INSERT INTO outcome_peaks (sport_key, event_id, outcome_key, pick_label, market_key, peak_decimal, peak_book, peak_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(sport_key, event_id, outcome_key) DO UPDATE SET
                    peak_decimal = excluded.peak_decimal,
                    peak_book = excluded.peak_book,
                    peak_at = excluded.peak_at,
                    pick_label = excluded.pick_label,
                    market_key = excluded.market_key
                WHERE excluded.peak_decimal > outcome_peaks.peak_decimal
                """,
                (sk, eid, ok, pick_label, mk, cand_dec, cand_book, ts),
            )
            conn.execute(
                """
                INSERT INTO outcome_odds_snapshots
                (sport_key, event_id, outcome_key, pick_label, market_key, best_decimal, best_book, observed_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (sk, eid, ok, pick_label, mk, cand_dec, cand_book, ts),
            )
        _prune_old_snapshots(conn)
        conn.commit()


def record_from_h2h_events_bulk_sync(events: list[dict[str, Any]], sport_key: str) -> None:
    """Write peaks + snapshots for many H2H events in one DB transaction (used after bulk odds fetch)."""
    sk = sport_key
    now = datetime.now(timezone.utc).isoformat()
    merged: list[tuple[str, str, str, str, str, float, str, str]] = []
    for ev in events:
        eid = _normalize_event_id(ev.get("id"))
        if not eid:
            continue
        prices = _collect_h2h_prices(ev)
        for outcome_name, book_prices in prices.items():
            if not book_prices or not outcome_name:
                continue
            ok = _outcome_key_h2h(str(outcome_name).strip())
            best_book, best_dec = max(book_prices, key=lambda x: x[1])
            merged.append(
                (sk, eid, ok, str(outcome_name).strip(), "h2h", float(best_dec), str(best_book), now)
            )
    if not merged:
        return
    init_schema_sync()
    with _connect() as conn:
        for row in merged:
            r_sk, eid, ok, pick_label, mk, cand_dec, cand_book, ts = row
            conn.execute(
                """
                INSERT INTO outcome_peaks (sport_key, event_id, outcome_key, pick_label, market_key, peak_decimal, peak_book, peak_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(sport_key, event_id, outcome_key) DO UPDATE SET
                    peak_decimal = excluded.peak_decimal,
                    peak_book = excluded.peak_book,
                    peak_at = excluded.peak_at,
                    pick_label = excluded.pick_label,
                    market_key = excluded.market_key
                WHERE excluded.peak_decimal > outcome_peaks.peak_decimal
                """,
                (r_sk, eid, ok, pick_label, mk, cand_dec, cand_book, ts),
            )
            conn.execute(
                """
                INSERT INTO outcome_odds_snapshots
                (sport_key, event_id, outcome_key, pick_label, market_key, best_decimal, best_book, observed_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (r_sk, eid, ok, pick_label, mk, cand_dec, cand_book, ts),
            )
        _prune_old_snapshots(conn)
        conn.commit()


def _prune_old_snapshots(conn: sqlite3.Connection, days: int = 45) -> None:
    """Drop very old points to cap DB growth."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=int(days))).isoformat()
    try:
        conn.execute(
            "DELETE FROM outcome_odds_snapshots WHERE observed_at < ?",
            (cutoff,),
        )
    except sqlite3.Error:
        pass


async def record_from_prop_event(ev: dict[str, Any], sport_key: str, allowed_markets: set[str]) -> None:
    async with _lock:
        await asyncio.to_thread(record_from_prop_event_sync, ev, sport_key, allowed_markets)


async def record_from_h2h_event(ev: dict[str, Any], sport_key: str) -> None:
    async with _lock:
        await asyncio.to_thread(record_from_h2h_event_sync, ev, sport_key)


async def _locked_record_h2h_bulk(events: list[dict[str, Any]], sport_key: str) -> None:
    async with _lock:
        await asyncio.to_thread(record_from_h2h_events_bulk_sync, events, sport_key)


def defer_record_h2h_slate_events(events: list[dict[str, Any]], sport_key: str) -> None:
    """Queue bulk H2H snapshot writes so `/api/picks` is not blocked on SQLite."""
    if not events:
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    loop.create_task(_locked_record_h2h_bulk(list(events), sport_key))


async def _locked_record_prop(ev: dict[str, Any], sport_key: str, allowed_markets: set[str]) -> None:
    async with _lock:
        await asyncio.to_thread(record_from_prop_event_sync, ev, sport_key, allowed_markets)


def defer_record_prop_event(ev: dict[str, Any], sport_key: str, allowed_markets: set[str]) -> None:
    """Queue prop snapshot writes so per-event odds fetches return immediately."""
    try:
        asyncio.get_running_loop().create_task(
            _locked_record_prop(ev, sport_key, allowed_markets)
        )
    except RuntimeError:
        return


def fetch_line_history_sync(
    sport_key: str,
    event_id: str,
    pick_label: str,
    limit: int = 400,
) -> list[dict[str, Any]]:
    """Time-ordered snapshots of best decimal among allowed books for this pick."""
    eid = _normalize_event_id(event_id)
    if not sport_key or not eid or not pick_label:
        return []
    path = get_db_path()
    if not path.exists():
        return []
    init_schema_sync()
    lim = max(10, min(int(limit), 2000))
    with _connect() as conn:
        cur = conn.execute(
            """
            SELECT observed_at, best_decimal, best_book
            FROM outcome_odds_snapshots
            WHERE sport_key = ? AND event_id = ? AND pick_label = ?
            ORDER BY observed_at ASC, id ASC
            LIMIT ?
            """,
            (sport_key, eid, pick_label, lim),
        )
        rows = cur.fetchall()
    return [
        {
            "t": r[0],
            "decimal_odds": round(float(r[1]), 4),
            "book": r[2],
            "source": "local",
        }
        for r in rows
    ]


async def fetch_line_history(
    sport_key: str,
    event_id: str,
    pick_label: str,
    limit: int = 400,
) -> list[dict[str, Any]]:
    async with _lock:
        return await asyncio.to_thread(
            fetch_line_history_sync, sport_key, event_id, pick_label, limit
        )
