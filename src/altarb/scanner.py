"""Scanner: pull books for every verified pair, compute net edge, log everything to SQLite.

Read-only. Nothing here can place an order (CLAUDE.md rule 1).

Run:  python -m altarb.scanner              # every 90s until Ctrl+C
      python -m altarb.scanner --once       # a single cycle
"""

from __future__ import annotations

import argparse
import logging
import signal
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from altarb.mapping.pairs import MarketPair, load_pairs, verified_pairs
from altarb.pricing.edge import Opportunity, find_edges, hedge_legs
from altarb.pricing.fees import KalshiFeeOverride, KalshiFeeStructure, is_table_tennis, kalshi_effective_fee
from altarb.storage import Store
from altarb.venues.kalshi import KalshiClient
from altarb.venues.models import OrderBook
from altarb.venues.polymarket import PolymarketClient

log = logging.getLogger("altarb.scanner")

# Measured 2026-10-10: the public gateway allows ~30 requests/minute, then blocks for 10s
# (Retry-After: 10). 1.1s and 2.0s pacing both got blocked; 2.5s ran clean.
POLYMARKET_PACE_SECONDS = 2.5
METADATA_TTL = timedelta(hours=1)


@dataclass
class _KalshiMeta:
    event_ticker: str
    resolves_at: datetime
    fetched_at: datetime


@dataclass
class _EventFees:
    series: KalshiFeeStructure
    overrides: list[KalshiFeeOverride]
    fetched_at: datetime


@dataclass
class ScanResult:
    pairs_checked: int = 0
    errors: int = 0
    opportunities: list[Opportunity] = field(default_factory=list)


class Scanner:
    def __init__(
        self,
        kalshi: KalshiClient,
        polymarket: PolymarketClient,
        store: Store,
        min_profit: Decimal = Decimal(0),
        polymarket_pace: float = POLYMARKET_PACE_SECONDS,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ) -> None:
        self.kalshi = kalshi
        self.polymarket = polymarket
        self.store = store
        self.min_profit = min_profit
        self.polymarket_pace = polymarket_pace
        self.sleep = sleep
        self.clock = clock
        self._kalshi_meta: dict[str, _KalshiMeta] = {}
        self._event_fees: dict[str, _EventFees] = {}
        self._table_tennis: dict[str, bool] = {}

    def scan_once(self, pairs: list[MarketPair]) -> ScanResult:
        now = self.clock()
        result = ScanResult()
        scan_id = self.store.start_scan(now)

        live = []
        for pair in verified_pairs(pairs):
            try:
                meta = self._meta(pair.kalshi_ticker, now)
            except Exception as e:  # noqa: BLE001 - one bad pair must not stop the scan
                result.errors += 1
                log.warning("kalshi market %s: %s", pair.kalshi_ticker, e)
                continue
            if meta.resolves_at > now:
                live.append((pair, meta))

        pm_books = self._polymarket_books({p.polymarket_slug for p, _ in live}, scan_id, now, result)

        for pair, meta in live:
            pm_book = pm_books.get(pair.polymarket_slug)
            if pm_book is None:
                continue  # already counted as an error when the book failed
            try:
                fee = self._fee(meta.event_ticker, now)
                kalshi_book = self.kalshi.get_orderbook(pair.kalshi_ticker)
                self.store.add_book(scan_id, now, "kalshi", kalshi_book)
                self._record_pair_snapshot(scan_id, now, pair, kalshi_book, pm_book)
                edges = find_edges(
                    pair, kalshi_book, pm_book, fee, resolves_at=meta.resolves_at, now=now,
                    table_tennis=self._table_tennis.get(pair.polymarket_slug, False),
                )
            except Exception as e:  # noqa: BLE001
                result.errors += 1
                log.warning("pair %s <-> %s: %s", pair.kalshi_ticker, pair.polymarket_slug, e)
                continue
            result.pairs_checked += 1
            for opp in edges:
                if opp.profit > self.min_profit:
                    self.store.add_opportunity(scan_id, now, opp)
                    result.opportunities.append(opp)

        self.store.finish_scan(scan_id, self.clock(), result.pairs_checked, result.errors)
        return result

    def _polymarket_books(self, slugs: set[str], scan_id: int, now: datetime, result: ScanResult) -> dict[str, OrderBook]:
        books = {}
        for i, slug in enumerate(sorted(slugs)):
            if i:
                self.sleep(self.polymarket_pace)
            try:
                if slug not in self._table_tennis:
                    self._table_tennis[slug] = is_table_tennis(self.polymarket.get_market(slug).sports_market_type)
                    self.sleep(self.polymarket_pace)
                books[slug] = self.polymarket.get_orderbook(slug)
                self.store.add_book(scan_id, now, "polymarket", books[slug])
            except Exception as e:  # noqa: BLE001
                result.errors += 1
                log.warning("polymarket book %s: %s", slug, e)
        return books

    def _record_pair_snapshot(self, scan_id, now, pair, kalshi_book, pm_book) -> None:
        costs = {}
        for kalshi_side, kalshi_asks, _, pm_asks in hedge_legs(pair, kalshi_book, pm_book):
            costs[kalshi_side] = kalshi_asks[0].price + pm_asks[0].price if kalshi_asks and pm_asks else None
        self.store.add_pair_snapshot(scan_id, now, pair.kalshi_ticker, pair.polymarket_slug, costs["yes"], costs["no"])

    def _meta(self, ticker: str, now: datetime) -> _KalshiMeta:
        cached = self._kalshi_meta.get(ticker)
        if cached is None or now - cached.fetched_at > METADATA_TTL:
            market = self.kalshi.get_market(ticker)
            resolves_at = market.expected_expiration_time or market.close_time
            if resolves_at is None:
                raise ValueError("market has no expected expiration or close time")
            cached = self._kalshi_meta[ticker] = _KalshiMeta(market.event_ticker, resolves_at, now)
        return cached

    def _fee(self, event_ticker: str, now: datetime) -> KalshiFeeStructure:
        cached = self._event_fees.get(event_ticker)
        if cached is None or now - cached.fetched_at > METADATA_TTL:
            series = self.kalshi.get_series_fee(event_ticker.split("-")[0])
            overrides = self.kalshi.get_event_fee_overrides(event_ticker)
            cached = self._event_fees[event_ticker] = _EventFees(series, overrides, now)
        # Re-evaluated every cycle so a scheduled override takes effect on time.
        return kalshi_effective_fee(cached.series, cached.overrides, now)


def main() -> None:
    parser = argparse.ArgumentParser(description="Scan verified pairs and log edges to SQLite.")
    parser.add_argument("--interval", type=float, default=90, help="seconds between scan starts (default 90)")
    parser.add_argument("--once", action="store_true", help="run a single scan and exit")
    parser.add_argument("--db", default=None, help="SQLite path (default data/scanner.db)")
    parser.add_argument("--min-profit", type=Decimal, default=Decimal(0), help="only log opportunities above this $ profit")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)  # otherwise one line per HTTP request

    store = Store(args.db) if args.db else Store()
    # Treat SIGTERM (what `kill` and service managers send) like Ctrl+C: finish cleanly.
    signal.signal(signal.SIGTERM, signal.default_int_handler)
    with KalshiClient(max_retries=6) as k, PolymarketClient(max_retries=6) as p:
        scanner = Scanner(k, p, store, min_profit=args.min_profit)
        try:
            while True:
                started = time.monotonic()
                result = scanner.scan_once(load_pairs())
                best = max(result.opportunities, key=lambda o: o.annualized_return, default=None)
                log.info(
                    "scan: %d pairs, %d errors, %d opportunities%s", result.pairs_checked, result.errors,
                    len(result.opportunities),
                    f" (best {best.kalshi_ticker} ${best.profit} profit, {best.annualized_return:.1%} annualized)" if best else "",
                )
                if args.once:
                    break
                time.sleep(max(0.0, args.interval - (time.monotonic() - started)))
        except KeyboardInterrupt:
            log.info("stopped")
        finally:
            store.close()


if __name__ == "__main__":
    main()
