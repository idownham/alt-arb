"""SQLite log of scanner runs: book snapshots, per-pair hedge costs, and opportunities.

Prices and sizes are stored as TEXT so they round-trip as exact Decimals.
Timestamps are ISO-8601 UTC strings.

Schema version 2 (2026-10-13): `ts` on books, pair snapshots and opportunities is the time
the book was actually fetched, and both legs of a pair are fetched back to back. Version 1
fetched every Polymarket book first and every Kalshi book minutes later, stamping all of them
with the scan's start time, so its cross-venue comparisons are unreliable (most visibly during
live games and when the Mac slept mid-scan). Version-1 rows are the ones with
leg_skew_seconds IS NULL.

Schema version 3 (2026-10-13) adds opportunities.streak / first_seen: how many consecutive scans
the same pair and direction stayed profitable. Gaps that last a single scan are hard to trade.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from decimal import Decimal
from pathlib import Path

from altarb.pricing.edge import Opportunity
from altarb.venues.models import OrderBook

DEFAULT_PATH = Path(__file__).resolve().parents[2] / "data" / "scanner.db"
SNAPSHOT_DEPTH = 10  # book levels stored per side; the edge math always uses the full book

SCHEMA = """
CREATE TABLE IF NOT EXISTS scans (
    id INTEGER PRIMARY KEY,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    pairs_checked INTEGER NOT NULL DEFAULT 0,
    errors INTEGER NOT NULL DEFAULT 0,
    skipped_stale INTEGER NOT NULL DEFAULT 0  -- pairs whose two books were fetched too far apart
);
CREATE TABLE IF NOT EXISTS book_snapshots (
    scan_id INTEGER NOT NULL REFERENCES scans(id),
    ts TEXT NOT NULL,
    venue TEXT NOT NULL,            -- 'kalshi' or 'polymarket'
    market TEXT NOT NULL,           -- Kalshi ticker or Polymarket slug
    yes_bids TEXT NOT NULL,         -- JSON [[price, size], ...] best first
    no_bids TEXT NOT NULL,
    PRIMARY KEY (scan_id, venue, market)
);
CREATE TABLE IF NOT EXISTS pair_snapshots (
    scan_id INTEGER NOT NULL REFERENCES scans(id),
    ts TEXT NOT NULL,
    kalshi_ticker TEXT NOT NULL,
    polymarket_slug TEXT NOT NULL,
    kalshi_yes_cost TEXT,           -- top-of-book price sum, before fees, for the hedge
    kalshi_no_cost TEXT,            -- that buys Kalshi YES / Kalshi NO (NULL if a side is empty)
    leg_skew_seconds REAL,          -- Kalshi fetch time minus Polymarket fetch time
    PRIMARY KEY (scan_id, kalshi_ticker, polymarket_slug)
);
CREATE TABLE IF NOT EXISTS opportunities (
    id INTEGER PRIMARY KEY,
    scan_id INTEGER NOT NULL REFERENCES scans(id),
    ts TEXT NOT NULL,
    kalshi_ticker TEXT NOT NULL,
    polymarket_slug TEXT NOT NULL,
    kalshi_side TEXT NOT NULL,
    polymarket_side TEXT NOT NULL,
    contracts TEXT NOT NULL,
    kalshi_cost TEXT NOT NULL,
    polymarket_cost TEXT NOT NULL,
    profit TEXT NOT NULL,
    return_on_capital TEXT NOT NULL,
    annualized_return TEXT NOT NULL,
    days_to_resolution TEXT NOT NULL,
    kalshi_fills TEXT NOT NULL,     -- JSON [[contracts, price], ...]
    polymarket_fills TEXT NOT NULL,
    leg_skew_seconds REAL,          -- Kalshi fetch time minus Polymarket fetch time
    in_play INTEGER,                -- 1 if the game had started (per Polymarket gameStartTime)
    game_start_time TEXT,
    streak INTEGER,                 -- consecutive scans this pair+direction has been profitable (1 = new)
    first_seen TEXT                 -- when the current streak started
);
CREATE INDEX IF NOT EXISTS opportunities_by_pair ON opportunities (kalshi_ticker, polymarket_slug, ts);
CREATE INDEX IF NOT EXISTS pair_snapshots_by_pair ON pair_snapshots (kalshi_ticker, polymarket_slug, ts);
"""

SCHEMA_VERSION = 3
# Columns added after version 1, so older databases can be upgraded in place.
_ADDED_COLUMNS = {
    "scans": [("skipped_stale", "INTEGER NOT NULL DEFAULT 0")],
    "pair_snapshots": [("leg_skew_seconds", "REAL")],
    "opportunities": [("leg_skew_seconds", "REAL"), ("in_play", "INTEGER"), ("game_start_time", "TEXT"),
                      ("streak", "INTEGER"), ("first_seen", "TEXT")],
}


class Store:
    def __init__(self, path: Path | str = DEFAULT_PATH) -> None:
        self.conn = sqlite3.connect(path)
        # WAL lets readers (altarb.status, the refresh's backup) read while a scan's long write
        # transaction is open; with the default rollback journal they hit "database is locked" (2026-10-15).
        self.conn.execute("PRAGMA journal_mode = WAL")
        self.conn.executescript(SCHEMA)
        self._migrate()

    def _migrate(self) -> None:
        for table, columns in _ADDED_COLUMNS.items():
            existing = {row[1] for row in self.conn.execute(f"PRAGMA table_info({table})")}
            for name, decl in columns:
                if name not in existing:
                    self.conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {decl}")
        self.conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    def start_scan(self, at: datetime) -> int:
        cur = self.conn.execute("INSERT INTO scans (started_at) VALUES (?)", (at.isoformat(),))
        self.conn.commit()
        return cur.lastrowid

    def finish_scan(self, scan_id: int, at: datetime, pairs_checked: int, errors: int, skipped_stale: int = 0) -> None:
        self.conn.execute(
            "UPDATE scans SET finished_at = ?, pairs_checked = ?, errors = ?, skipped_stale = ? WHERE id = ?",
            (at.isoformat(), pairs_checked, errors, skipped_stale, scan_id),
        )
        self.conn.commit()

    def add_book(self, scan_id: int, at: datetime, venue: str, book: OrderBook) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO book_snapshots (scan_id, ts, venue, market, yes_bids, no_bids) VALUES (?, ?, ?, ?, ?, ?)",
            (scan_id, at.isoformat(), venue, book.ticker,
             _levels_json(book.yes_bids), _levels_json(book.no_bids)),
        )

    def add_pair_snapshot(
        self, scan_id: int, at: datetime, kalshi_ticker: str, polymarket_slug: str,
        kalshi_yes_cost: Decimal | None, kalshi_no_cost: Decimal | None, leg_skew_seconds: float | None = None,
    ) -> None:
        self.conn.execute(
            """INSERT OR REPLACE INTO pair_snapshots (scan_id, ts, kalshi_ticker, polymarket_slug,
               kalshi_yes_cost, kalshi_no_cost, leg_skew_seconds) VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (scan_id, at.isoformat(), kalshi_ticker, polymarket_slug,
             _text(kalshi_yes_cost), _text(kalshi_no_cost), leg_skew_seconds),
        )

    def add_opportunity(
        self, scan_id: int, at: datetime, o: Opportunity, leg_skew_seconds: float | None = None,
        in_play: bool | None = None, game_start_time: datetime | None = None,
        streak: int | None = None, first_seen: datetime | None = None,
    ) -> None:
        self.conn.execute(
            """INSERT INTO opportunities (scan_id, ts, kalshi_ticker, polymarket_slug, kalshi_side,
               polymarket_side, contracts, kalshi_cost, polymarket_cost, profit, return_on_capital,
               annualized_return, days_to_resolution, kalshi_fills, polymarket_fills,
               leg_skew_seconds, in_play, game_start_time, streak, first_seen)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (scan_id, at.isoformat(), o.kalshi_ticker, o.polymarket_slug, o.kalshi_side, o.polymarket_side,
             str(o.contracts), str(o.kalshi_cost), str(o.polymarket_cost), str(o.profit),
             str(o.return_on_capital), str(o.annualized_return), str(o.days_to_resolution),
             _fills_json(o.kalshi_fills), _fills_json(o.polymarket_fills),
             leg_skew_seconds, None if in_play is None else int(in_play),
             game_start_time.isoformat() if game_start_time else None,
             streak, first_seen.isoformat() if first_seen else None),
        )

    def commit(self) -> None:
        self.conn.commit()


def _levels_json(levels) -> str:
    return json.dumps([[str(l.price), str(l.size)] for l in levels[:SNAPSHOT_DEPTH]])


def _fills_json(fills) -> str:
    return json.dumps([[str(c), str(p)] for c, p in fills])


def _text(value: Decimal | None) -> str | None:
    return None if value is None else str(value)
