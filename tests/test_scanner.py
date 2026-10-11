import json
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal as D

import pytest

from altarb.mapping.pairs import MarketPair
from altarb.pricing.fees import KalshiFeeOverride, KalshiFeeStructure
from altarb.scanner import POLYMARKET_PACE_SECONDS, Scanner
from altarb.storage import Store
from altarb.venues import kalshi, polymarket
from altarb.venues.models import ONE, OrderBook, PriceLevel

NOW = datetime(2026, 10, 11, 12, 0, tzinfo=timezone.utc)
GAME_END = NOW + timedelta(days=1)


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
    def __init__(self, books, resolves=GAME_END, fee=KalshiFeeStructure("quadratic_with_maker_fees", D(1)), overrides=()):
        self.books, self.resolves, self.fee, self.overrides = books, resolves, fee, list(overrides)
        self.calls = Counter()

    def get_market(self, ticker):
        self.calls["market"] += 1
        return kalshi.Market.from_api({
            "ticker": ticker, "event_ticker": ticker.rsplit("-", 1)[0],
            "expected_expiration_time": self.resolves.isoformat(),
        })

    def get_orderbook(self, ticker):
        self.calls["book"] += 1
        return self.books[ticker]

    def get_series_fee(self, series):
        self.calls["series"] += 1
        return self.fee

    def get_event_fee_overrides(self, event):
        self.calls["overrides"] += 1
        return self.overrides


class FakePolymarket:
    def __init__(self, books, fail=()):
        self.books, self.fail = books, set(fail)
        self.calls = Counter()

    def get_market(self, slug):
        self.calls["market"] += 1
        return polymarket.Market.from_api({"id": "1", "slug": slug, "sportsMarketType": "football_team_full_game_winner"})

    def get_orderbook(self, slug):
        self.calls["book"] += 1
        if slug in self.fail:
            raise RuntimeError("429 Too Many Requests")
        return self.books[slug]


# Profitable: Kalshi YES 0.40 + PM NO 0.45 (see test_edge: 50 contracts -> $5.80 profit)
PROFITABLE_K = book("KXA-G1-A", yes_asks=[("0.40", "100")], no_asks=[("0.62", "100")])
PROFITABLE_P = book("pm-g1", yes_asks=[("0.56", "100")], no_asks=[("0.45", "50")])
# Not profitable: 0.50 + 0.52
FLAT_K = book("KXB-G2-B", yes_asks=[("0.50", "100")], no_asks=[("0.52", "100")])
FLAT_P = book("pm-g2", yes_asks=[("0.50", "100")], no_asks=[("0.52", "100")])


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "scan.db")
    yield s
    s.close()


def make(store, k_books=None, p_books=None, **kw):
    k = FakeKalshi(k_books or {"KXA-G1-A": PROFITABLE_K, "KXB-G2-B": FLAT_K}, **kw.pop("kalshi", {}))
    p = FakePolymarket(p_books or {"pm-g1": PROFITABLE_P, "pm-g2": FLAT_P}, **kw.pop("polymarket", {}))
    sleeps = []
    clock = kw.pop("clock", lambda: NOW)
    return Scanner(k, p, store, sleep=sleeps.append, clock=clock, **kw), k, p, sleeps


PAIRS = [pair("KXA-G1-A", "pm-g1"), pair("KXB-G2-B", "pm-g2")]


def rows(store, sql):
    return store.conn.execute(sql).fetchall()


def test_logs_scan_books_snapshots_and_opportunity(store):
    scanner, *_ = make(store)

    result = scanner.scan_once(PAIRS)

    assert (result.pairs_checked, result.errors, len(result.opportunities)) == (2, 0, 1)
    [(pairs_checked, errors, finished)] = rows(store, "SELECT pairs_checked, errors, finished_at FROM scans")
    assert (pairs_checked, errors) == (2, 0) and finished
    assert sorted(rows(store, "SELECT venue, market FROM book_snapshots")) == [
        ("kalshi", "KXA-G1-A"), ("kalshi", "KXB-G2-B"), ("polymarket", "pm-g1"), ("polymarket", "pm-g2")]
    # Top-of-book hedge costs before fees: K-YES 0.40 + PM-NO 0.45 = 0.85 ; K-NO 0.62 + PM-YES 0.56 = 1.18
    assert rows(store, "SELECT kalshi_yes_cost, kalshi_no_cost FROM pair_snapshots WHERE kalshi_ticker='KXA-G1-A'") == [
        ("0.85", "1.18")]
    [(side, pm_side, contracts, profit, fills)] = rows(
        store, "SELECT kalshi_side, polymarket_side, contracts, profit, kalshi_fills FROM opportunities")
    assert (side, pm_side, D(contracts), D(profit)) == ("yes", "no", D(50), D("5.80"))
    assert json.loads(fills) == [["50", "0.40"]]


def test_book_snapshot_keeps_exact_decimals(store):
    scanner, *_ = make(store)
    scanner.scan_once(PAIRS)

    [(yes_bids,)] = rows(store, "SELECT yes_bids FROM book_snapshots WHERE market='pm-g1'")
    assert json.loads(yes_bids) == [["0.55", "50"]]  # NO ask 0.45 == YES bid 0.55


def test_skips_unverified_and_resolved_pairs(store):
    scanner, k, p, _ = make(store, kalshi={"resolves": NOW - timedelta(hours=1)})

    result = scanner.scan_once([pair("KXA-G1-A", "pm-g1"), pair("KXB-G2-B", "pm-g2", verified=False)])

    assert result.pairs_checked == 0
    assert k.calls["market"] == 1  # only the verified pair was looked up
    assert k.calls["book"] == 0 and p.calls["book"] == 0


def test_one_failing_book_does_not_stop_the_scan(store):
    scanner, *_ = make(store, polymarket={"fail": {"pm-g2"}})

    result = scanner.scan_once(PAIRS)

    assert (result.pairs_checked, result.errors, len(result.opportunities)) == (1, 1, 1)
    assert rows(store, "SELECT errors FROM scans") == [(1,)]


def test_paces_polymarket_requests(store):
    scanner, _, p, sleeps = make(store)
    scanner.scan_once(PAIRS)

    # First scan: market + book per slug (4 requests), a pause before each request after the first.
    assert p.calls == Counter(market=2, book=2)
    assert sleeps == [POLYMARKET_PACE_SECONDS] * 3

    sleeps.clear()
    scanner.scan_once(PAIRS)
    assert p.calls["market"] == 2  # market type cached; only books refetched
    assert sleeps == [POLYMARKET_PACE_SECONDS]


def test_caches_kalshi_metadata_for_an_hour(store):
    times = iter([NOW, NOW, NOW + timedelta(minutes=30), NOW + timedelta(minutes=30),
                  NOW + timedelta(minutes=61), NOW + timedelta(minutes=61)])
    scanner, k, *_ = make(store, clock=lambda: next(times))

    for _ in range(3):
        scanner.scan_once(PAIRS)

    assert k.calls["market"] == 4  # 2 tickers x (first scan + after expiry)
    assert k.calls["series"] == 4 and k.calls["overrides"] == 4
    assert k.calls["book"] == 6  # books every scan


def test_scheduled_fee_override_applies_mid_run(store):
    # Kalshi YES 0.48 + PM NO 0.45 = 0.93 before fees. An override 10 minutes in raises the
    # Kalshi multiplier from 1 to 5.
    k_book = book("KXA-G1-A", yes_asks=[("0.48", "100")])
    overrides = [KalshiFeeOverride(NOW + timedelta(minutes=10), "quadratic", D(5))]
    times = iter([NOW, NOW, NOW + timedelta(minutes=20), NOW + timedelta(minutes=20)])
    scanner, *_ = make(store, k_books={"KXA-G1-A": k_book}, p_books={"pm-g1": PROFITABLE_P},
                       kalshi={"overrides": overrides}, clock=lambda: next(times))

    before = scanner.scan_once([PAIRS[0]])
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
