"""SQLite log of scanner runs: book snapshots, per-pair hedge costs, and opportunities.

Prices and sizes are stored as TEXT so they round-trip as exact Decimals.
Timestamps are ISO-8601 UTC strings.
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
    errors INTEGER NOT NULL DEFAULT 0
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
    polymarket_fills TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS opportunities_by_pair ON opportunities (kalshi_ticker, polymarket_slug, ts);
CREATE INDEX IF NOT EXISTS pair_snapshots_by_pair ON pair_snapshots (kalshi_ticker, polymarket_slug, ts);
"""


class Store:
    def __init__(self, path: Path | str = DEFAULT_PATH) -> None:
        self.conn = sqlite3.connect(path)
        self.conn.executescript(SCHEMA)

    def close(self) -> None:
        self.conn.close()

    def start_scan(self, at: datetime) -> int:
        cur = self.conn.execute("INSERT INTO scans (started_at) VALUES (?)", (at.isoformat(),))
        self.conn.commit()
        return cur.lastrowid

    def finish_scan(self, scan_id: int, at: datetime, pairs_checked: int, errors: int) -> None:
        self.conn.execute(
            "UPDATE scans SET finished_at = ?, pairs_checked = ?, errors = ? WHERE id = ?",
            (at.isoformat(), pairs_checked, errors, scan_id),
        )
        self.conn.commit()

    def add_book(self, scan_id: int, at: datetime, venue: str, book: OrderBook) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO book_snapshots VALUES (?, ?, ?, ?, ?, ?)",
            (scan_id, at.isoformat(), venue, book.ticker,
             _levels_json(book.yes_bids), _levels_json(book.no_bids)),
        )

    def add_pair_snapshot(
        self, scan_id: int, at: datetime, kalshi_ticker: str, polymarket_slug: str,
        kalshi_yes_cost: Decimal | None, kalshi_no_cost: Decimal | None,
    ) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO pair_snapshots VALUES (?, ?, ?, ?, ?, ?)",
            (scan_id, at.isoformat(), kalshi_ticker, polymarket_slug,
             _text(kalshi_yes_cost), _text(kalshi_no_cost)),
        )

    def add_opportunity(self, scan_id: int, at: datetime, o: Opportunity) -> None:
        self.conn.execute(
            """INSERT INTO opportunities (scan_id, ts, kalshi_ticker, polymarket_slug, kalshi_side,
               polymarket_side, contracts, kalshi_cost, polymarket_cost, profit, return_on_capital,
               annualized_return, days_to_resolution, kalshi_fills, polymarket_fills)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (scan_id, at.isoformat(), o.kalshi_ticker, o.polymarket_slug, o.kalshi_side, o.polymarket_side,
             str(o.contracts), str(o.kalshi_cost), str(o.polymarket_cost), str(o.profit),
             str(o.return_on_capital), str(o.annualized_return), str(o.days_to_resolution),
             _fills_json(o.kalshi_fills), _fills_json(o.polymarket_fills)),
        )

    def commit(self) -> None:
        self.conn.commit()


def _levels_json(levels) -> str:
    return json.dumps([[str(l.price), str(l.size)] for l in levels[:SNAPSHOT_DEPTH]])


def _fills_json(fills) -> str:
    return json.dumps([[str(c), str(p)] for c, p in fills])


def _text(value: Decimal | None) -> str | None:
    return None if value is None else str(value)
