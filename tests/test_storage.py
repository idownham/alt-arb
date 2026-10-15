import sqlite3
from datetime import datetime, timezone

from altarb.storage import Store
from altarb.venues.models import OrderBook

AT = datetime(2026, 10, 15, 16, 0, tzinfo=timezone.utc)


def test_store_uses_wal(tmp_path):
    store = Store(tmp_path / "scanner.db")
    assert store.conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    store.close()


def test_readers_are_not_blocked_by_an_open_scan(tmp_path):
    db = tmp_path / "scanner.db"
    store = Store(db)
    first = store.start_scan(AT)
    store.finish_scan(first, AT, pairs_checked=327, errors=0)
    second = store.start_scan(AT)
    store.conn.execute("PRAGMA cache_size = 1")  # spill to disk early, like a long scan does
    for i in range(500):  # a scan's uncommitted writes
        store.add_book(second, AT, "kalshi", OrderBook(f"T-{i}", [], []))
    reader = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=0)  # no waiting: fail fast if locked
    try:
        rows = reader.execute("SELECT id, pairs_checked FROM scans WHERE finished_at IS NOT NULL").fetchall()
    finally:
        reader.close()
        store.close()
    assert rows == [(first, 327)]
