import json
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal as D

import pytest

from altarb.mapping.pairs import MarketPair
from altarb.pricing.fees import KalshiFeeOverride, KalshiFeeStructure
from altarb.scanner import MAX_LEG_SKEW_SECONDS, POLYMARKET_PACE_SECONDS, Scanner
from altarb.storage import Store
from altarb.venues import kalshi, polymarket
from altarb.venues.models import ONE, OrderBook, PriceLevel

NOW = datetime(2026, 10, 11, 12, 0, tzinfo=timezone.utc)
GAME_END = NOW + timedelta(days=1)


class Clock:
    """A settable clock. Fakes can advance it to simulate slow requests or a sleeping laptop."""

    def __init__(self, t=NOW):
        self.t = t

    def __call__(self):
        return self.t

    def advance(self, **kw):
        self.t += timedelta(**kw)


def book(ticker, yes_asks=(), no_asks=()):
    return OrderBook(
        ticker=ticker,
        yes_bids=[PriceLevel(ONE - D(p), D(s)) for p, s in no_asks],
        no_bids=[PriceLevel(ONE - D(p), D(s)) for p, s in yes_asks],
    )


def pair(ticker, slug, verified=True):
    return MarketPair(ticker, slug, False, "", "", date(2026, 10, 12), verified,
                      "Ian" if verified else "", date(2026, 10, 10) if verified else None)


class FakeKalshi:
    def __init__(self, books, log, resolves=GAME_END, fee=KalshiFeeStructure("quadratic_with_maker_fees", D(1)),
                 overrides=(), on_book=None):
        self.books, self.log, self.resolves, self.fee, self.overrides = books, log, resolves, fee, list(overrides)
        self.on_book = on_book
        self.calls = Counter()

    def get_market(self, ticker):
        self.calls["market"] += 1
        return kalshi.Market.from_api({
            "ticker": ticker, "event_ticker": ticker.rsplit("-", 1)[0],
            "expected_expiration_time": self.resolves.isoformat(),
        })

    def get_orderbook(self, ticker):
        self.calls["book"] += 1
        self.log.append(("kalshi book", ticker))
        if self.on_book:
            self.on_book(ticker)
        return self.books[ticker]

    def get_series_fee(self, series):
        self.calls["series"] += 1
        self.log.append(("kalshi fees", series))
        return self.fee

    def get_event_fee_overrides(self, event):
        self.calls["overrides"] += 1
        return self.overrides


class FakePolymarket:
    def __init__(self, books, log, fail=(), game_start=None):
        self.books, self.log, self.fail, self.game_start = books, log, set(fail), game_start
        self.calls = Counter()

    def get_market(self, slug):
        self.calls["market"] += 1
        self.log.append(("pm market", slug))
        data = {"id": "1", "slug": slug, "sportsMarketType": "football_team_full_game_winner"}
        if self.game_start:
            data["gameStartTime"] = self.game_start.isoformat()
        return polymarket.Market.from_api(data)

    def get_orderbook(self, slug):
        self.calls["book"] += 1
        self.log.append(("pm book", slug))
        if slug in self.fail:
            raise RuntimeError("429 Too Many Requests")
        return self.books[slug]


# Profitable: Kalshi YES 0.40 + PM NO 0.45 (see test_edge: 50 contracts -> $5.80 profit)
PROFITABLE_K = book("KXA-G1-A", yes_asks=[("0.40", "100")], no_asks=[("0.62", "100")])
PROFITABLE_P = book("pm-g1", yes_asks=[("0.56", "100")], no_asks=[("0.45", "50")])
# Not profitable: 0.50 + 0.52
FLAT_K = book("KXB-G2-B", yes_asks=[("0.50", "100")], no_asks=[("0.52", "100")])
FLAT_P = book("pm-g2", yes_asks=[("0.50", "100")], no_asks=[("0.52", "100")])

PAIRS = [pair("KXA-G1-A", "pm-g1"), pair("KXB-G2-B", "pm-g2")]


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "scan.db")
    yield s
    s.close()


def make(store, k_books=None, p_books=None, clock=None, kalshi=None, pm=None, **kw):
    log, clock = [], clock or Clock()
    k = FakeKalshi(k_books or {"KXA-G1-A": PROFITABLE_K, "KXB-G2-B": FLAT_K}, log, **(kalshi or {}))
    p = FakePolymarket(p_books or {"pm-g1": PROFITABLE_P, "pm-g2": FLAT_P}, log, **(pm or {}))
    sleeps = []
    return Scanner(k, p, store, sleep=sleeps.append, clock=clock, **kw), k, p, sleeps, log


def rows(store, sql):
    return store.conn.execute(sql).fetchall()


def test_logs_scan_books_snapshots_and_opportunity(store):
    scanner, *_ = make(store)

    result = scanner.scan_once(PAIRS)

    assert (result.pairs_checked, result.errors, result.skipped_stale, len(result.opportunities)) == (2, 0, 0, 1)
    [(pairs_checked, errors, stale, finished)] = rows(store, "SELECT pairs_checked, errors, skipped_stale, finished_at FROM scans")
    assert (pairs_checked, errors, stale) == (2, 0, 0) and finished
    assert sorted(rows(store, "SELECT venue, market FROM book_snapshots")) == [
        ("kalshi", "KXA-G1-A"), ("kalshi", "KXB-G2-B"), ("polymarket", "pm-g1"), ("polymarket", "pm-g2")]
    # Top-of-book hedge costs before fees: K-YES 0.40 + PM-NO 0.45 = 0.85 ; K-NO 0.62 + PM-YES 0.56 = 1.18
    assert rows(store, "SELECT kalshi_yes_cost, kalshi_no_cost, leg_skew_seconds FROM pair_snapshots WHERE kalshi_ticker='KXA-G1-A'") == [
        ("0.85", "1.18", 0.0)]
    [(side, pm_side, contracts, profit, fills, skew)] = rows(
        store, "SELECT kalshi_side, polymarket_side, contracts, profit, kalshi_fills, leg_skew_seconds FROM opportunities")
    assert (side, pm_side, D(contracts), D(profit), skew) == ("yes", "no", D(50), D("5.80"), 0.0)
    assert json.loads(fills) == [["50", "0.40"]]


def test_each_games_books_are_fetched_back_to_back(store):
    # The bug this guards against: all Polymarket books first, Kalshi books minutes later.
    scanner, *_, log = make(store)

    scanner.scan_once(PAIRS)

    books = [entry for entry in log if entry[0].endswith("book")]
    assert books == [
        ("pm book", "pm-g1"), ("kalshi book", "KXA-G1-A"),
        ("pm book", "pm-g2"), ("kalshi book", "KXB-G2-B"),
    ]
    # Slow metadata is fetched before a game's PM book, never between its two legs.
    for i, entry in enumerate(log):
        if entry[0] == "pm book":
            assert log[i + 1][0] == "kalshi book"


def test_skips_pair_when_legs_fetched_too_far_apart(store):
    clock = Clock()
    # Simulate the laptop sleeping between the Polymarket and Kalshi fetches.
    scanner, *_ = make(store, clock=clock, kalshi={"on_book": lambda t: clock.advance(seconds=MAX_LEG_SKEW_SECONDS + 1)})

    result = scanner.scan_once(PAIRS)

    assert (result.pairs_checked, result.skipped_stale, len(result.opportunities)) == (0, 2, 0)
    assert rows(store, "SELECT skipped_stale FROM scans") == [(2,)]
    assert rows(store, "SELECT COUNT(*) FROM opportunities") == [(0,)]
    # The snapshot is still recorded, with its skew, for later analysis.
    assert rows(store, "SELECT leg_skew_seconds FROM pair_snapshots WHERE kalshi_ticker='KXA-G1-A'") == [(6.0,)]


def test_small_skew_is_allowed_and_recorded(store):
    clock = Clock()
    scanner, *_ = make(store, clock=clock, kalshi={"on_book": lambda t: clock.advance(seconds=1.5)})

    result = scanner.scan_once(PAIRS)

    assert result.skipped_stale == 0 and len(result.opportunities) == 1
    assert rows(store, "SELECT leg_skew_seconds FROM opportunities") == [(1.5,)]


def test_book_timestamps_are_fetch_times_not_scan_start(store):
    clock = Clock()
    scanner, *_ = make(store, clock=clock, kalshi={"on_book": lambda t: clock.advance(seconds=1)})

    scanner.scan_once(PAIRS)

    stamps = dict(((v, m), ts) for v, m, ts in rows(store, "SELECT venue, market, ts FROM book_snapshots"))
    assert stamps[("polymarket", "pm-g1")] == NOW.isoformat()
    assert stamps[("kalshi", "KXA-G1-A")] == (NOW + timedelta(seconds=1)).isoformat()
    assert stamps[("kalshi", "KXB-G2-B")] == (NOW + timedelta(seconds=2)).isoformat()


@pytest.mark.parametrize(("start", "expected"), [(NOW - timedelta(minutes=30), 1), (NOW + timedelta(hours=2), 0)])
def test_records_whether_game_was_in_play(store, start, expected):
    scanner, *_ = make(store, pm={"game_start": start})

    scanner.scan_once(PAIRS)

    assert rows(store, "SELECT in_play, game_start_time FROM opportunities") == [(expected, start.isoformat())]


def test_book_snapshot_keeps_exact_decimals(store):
    scanner, *_ = make(store)
    scanner.scan_once(PAIRS)

    [(yes_bids,)] = rows(store, "SELECT yes_bids FROM book_snapshots WHERE market='pm-g1'")
    assert json.loads(yes_bids) == [["0.55", "50"]]  # NO ask 0.45 == YES bid 0.55


def test_skips_unverified_and_resolved_pairs(store):
    scanner, k, p, *_ = make(store, kalshi={"resolves": NOW - timedelta(hours=1)})

    result = scanner.scan_once([pair("KXA-G1-A", "pm-g1"), pair("KXB-G2-B", "pm-g2", verified=False)])

    assert result.pairs_checked == 0
    assert k.calls["market"] == 1  # only the verified pair was looked up
    assert k.calls["book"] == 0 and p.calls["book"] == 0


def test_one_failing_book_does_not_stop_the_scan(store):
    scanner, *_ = make(store, pm={"fail": {"pm-g2"}})

    result = scanner.scan_once(PAIRS)

    assert (result.pairs_checked, result.errors, len(result.opportunities)) == (1, 1, 1)
    assert rows(store, "SELECT errors FROM scans") == [(1,)]


def test_paces_polymarket_requests(store):
    scanner, _, p, sleeps, _ = make(store)
    scanner.scan_once(PAIRS)

    # First scan: market + book per game (4 requests), a pause before each request after the first.
    assert p.calls == Counter(market=2, book=2)
    assert sleeps == [POLYMARKET_PACE_SECONDS] * 3

    sleeps.clear()
    scanner.scan_once(PAIRS)
    assert p.calls["market"] == 2  # metadata cached; only books refetched
    assert sleeps == [POLYMARKET_PACE_SECONDS]


def test_caches_metadata_for_an_hour(store):
    clock = Clock()
    scanner, k, p, *_ = make(store, clock=clock)

    scanner.scan_once(PAIRS)
    clock.advance(minutes=30)
    scanner.scan_once(PAIRS)
    clock.advance(minutes=31)
    scanner.scan_once(PAIRS)

    assert k.calls["market"] == 4  # 2 tickers x (first scan + after expiry)
    assert k.calls["series"] == 4 and k.calls["overrides"] == 4
    assert p.calls["market"] == 4
    assert k.calls["book"] == 6 and p.calls["book"] == 6  # books every scan


def test_scheduled_fee_override_applies_mid_run(store):
    # Kalshi YES 0.48 + PM NO 0.45 = 0.93 before fees. An override 10 minutes in raises the
    # Kalshi multiplier from 1 to 5.
    clock = Clock()
    k_book = book("KXA-G1-A", yes_asks=[("0.48", "100")])
    overrides = [KalshiFeeOverride(NOW + timedelta(minutes=10), "quadratic", D(5))]
    scanner, *_ = make(store, k_books={"KXA-G1-A": k_book}, p_books={"pm-g1": PROFITABLE_P},
                       kalshi={"overrides": overrides}, clock=clock)

    before = scanner.scan_once([PAIRS[0]])
    clock.advance(minutes=20)
    after = scanner.scan_once([PAIRS[0]])

    # Before (x1): 0.48 + 0.45 + 0.07*0.48*0.52 (0.017472) + 0.0695*0.45*0.55 (0.017201) = 0.9647 -> profitable.
    # After  (x5): 0.48 + 0.45 + 0.087360 + 0.017201 = 1.0346 -> loses money on the first contract.
    assert len(before.opportunities) == 1
    assert after.opportunities == []


def test_min_profit_filters_logged_opportunities(store):
    scanner, *_ = make(store, min_profit=D(10))

    result = scanner.scan_once(PAIRS)

    assert result.opportunities == []
    assert rows(store, "SELECT COUNT(*) FROM opportunities") == [(0,)]


def test_old_database_is_upgraded_in_place(tmp_path):
    import sqlite3

    path = tmp_path / "v1.db"
    conn = sqlite3.connect(path)
    conn.executescript("""
        CREATE TABLE scans (id INTEGER PRIMARY KEY, started_at TEXT NOT NULL, finished_at TEXT,
            pairs_checked INTEGER NOT NULL DEFAULT 0, errors INTEGER NOT NULL DEFAULT 0);
        CREATE TABLE opportunities (id INTEGER PRIMARY KEY, scan_id INTEGER, ts TEXT, kalshi_ticker TEXT,
            polymarket_slug TEXT, kalshi_side TEXT, polymarket_side TEXT, contracts TEXT, kalshi_cost TEXT,
            polymarket_cost TEXT, profit TEXT, return_on_capital TEXT, annualized_return TEXT,
            days_to_resolution TEXT, kalshi_fills TEXT, polymarket_fills TEXT);
        INSERT INTO opportunities (scan_id, profit) VALUES (1, '94677.34');
    """)
    conn.commit()
    conn.close()

    s = Store(path)

    assert s.conn.execute("PRAGMA user_version").fetchone() == (2,)
    # Pre-fix rows are kept and identifiable by their missing skew.
    assert s.conn.execute("SELECT profit, leg_skew_seconds FROM opportunities").fetchall() == [("94677.34", None)]
    s.close()
