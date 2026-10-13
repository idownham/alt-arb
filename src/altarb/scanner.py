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
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from altarb.mapping.pairs import MarketPair, load_pairs, verified_pairs
from altarb.pricing.edge import Opportunity, find_edges, hedge_legs
from altarb.pricing.fees import KalshiFeeOverride, KalshiFeeStructure, is_table_tennis, kalshi_effective_fee
from altarb.storage import Store
from altarb.venues.kalshi import KalshiClient
from altarb.venues.polymarket import PolymarketClient

log = logging.getLogger("altarb.scanner")

# Measured 2026-10-10: the public gateway allows ~30 requests/minute, then blocks for 10s
# (Retry-After: 10). 1.1s and 2.0s pacing both got blocked; 2.5s ran clean.
POLYMARKET_PACE_SECONDS = 2.5
METADATA_TTL = timedelta(hours=1)


MAX_LEG_SKEW_SECONDS = 5.0  # both venues' books must be fetched within this window to compare them


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
class _PolymarketMeta:
    table_tennis: bool
    game_start_time: datetime | None
    fetched_at: datetime


@dataclass
class _Streak:
    scan_id: int
    count: int
    first_seen: datetime


@dataclass
class ScanResult:
    pairs_checked: int = 0
    errors: int = 0
    skipped_stale: int = 0
    opportunities: list[Opportunity] = field(default_factory=list)


class Scanner:
    """Checks every verified pair, one game at a time.

    For each game the Polymarket book is fetched and then, immediately, that game's Kalshi
    books, so the two legs are compared at (nearly) the same moment. Anything slow (fees,
    market metadata) is fetched and cached before the books. If the legs still end up more
    than MAX_LEG_SKEW_SECONDS apart, the pair is skipped rather than compared.
    """

    def __init__(
        self,
        kalshi: KalshiClient,
        polymarket: PolymarketClient,
        store: Store,
        min_profit: Decimal = Decimal(0),
        polymarket_pace: float = POLYMARKET_PACE_SECONDS,
        max_leg_skew: float = MAX_LEG_SKEW_SECONDS,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ) -> None:
        self.kalshi = kalshi
        self.polymarket = polymarket
        self.store = store
        self.min_profit = min_profit
        self.polymarket_pace = polymarket_pace
        self.max_leg_skew = max_leg_skew
        self.sleep = sleep
        self.clock = clock
        self._kalshi_meta: dict[str, _KalshiMeta] = {}
        self._event_fees: dict[str, _EventFees] = {}
        self._pm_meta: dict[str, _PolymarketMeta] = {}
        self._pm_requests = 0  # for pacing: every Polymarket request after the first waits
        # Opportunities seen in the previous scan, by (kalshi ticker, polymarket slug, kalshi side).
        # In memory only: a restart starts every streak again at 1.
        self._streaks: dict[tuple[str, str, str], _Streak] = {}
        self._last_scan_id: int | None = None

    def scan_once(self, pairs: list[MarketPair]) -> ScanResult:
        started = self.clock()
        result = ScanResult()
        scan_id = self.store.start_scan(started)
        self._pm_requests = 0

        games: dict[str, list[tuple[MarketPair, _KalshiMeta]]] = defaultdict(list)
        for pair in verified_pairs(pairs):
            try:
                meta = self._meta(pair.kalshi_ticker, started)
            except Exception as e:  # noqa: BLE001 - one bad pair must not stop the scan
                result.errors += 1
                log.warning("kalshi market %s: %s", pair.kalshi_ticker, e)
                continue
            if meta.resolves_at > started:
                games[pair.polymarket_slug].append((pair, meta))

        for slug, game_pairs in sorted(games.items()):
            self._scan_game(scan_id, slug, game_pairs, result)

        self.store.finish_scan(scan_id, self.clock(), result.pairs_checked, result.errors, result.skipped_stale)
        # Keep only this scan's opportunities: anything not seen again has ended its streak.
        self._streaks = {k: v for k, v in self._streaks.items() if v.scan_id == scan_id}
        self._last_scan_id = scan_id
        return result

    def _scan_game(self, scan_id: int, slug: str, game_pairs: list[tuple[MarketPair, _KalshiMeta]], result: ScanResult) -> None:
        # 1. Everything slow first, so nothing sits between the two venues' book fetches.
        try:
            pm_meta = self._polymarket_meta(slug)
            for _, meta in game_pairs:
                self._event_fee_data(meta.event_ticker)
        except Exception as e:  # noqa: BLE001
            result.errors += len(game_pairs)
            log.warning("metadata for %s: %s", slug, e)
            return

        # 2. Polymarket book, then 3. this game's Kalshi books straight after.
        try:
            self._pace_polymarket()
            pm_book = self.polymarket.get_orderbook(slug)
            pm_at = self.clock()
            self.store.add_book(scan_id, pm_at, "polymarket", pm_book)
        except Exception as e:  # noqa: BLE001
            result.errors += len(game_pairs)
            log.warning("polymarket book %s: %s", slug, e)
            return

        for pair, meta in game_pairs:
            try:
                kalshi_book = self.kalshi.get_orderbook(pair.kalshi_ticker)
                k_at = self.clock()
                self.store.add_book(scan_id, k_at, "kalshi", kalshi_book)
                skew = (k_at - pm_at).total_seconds()
                self._record_pair_snapshot(scan_id, k_at, pair, kalshi_book, pm_book, skew)
                if abs(skew) > self.max_leg_skew:
                    result.skipped_stale += 1
                    log.warning("skipped %s: books fetched %.1fs apart", pair.kalshi_ticker, skew)
                    continue
                if meta.resolves_at <= k_at:
                    continue  # resolved while we were scanning
                edges = find_edges(
                    pair, kalshi_book, pm_book, self._fee(meta.event_ticker, k_at),
                    resolves_at=meta.resolves_at, now=k_at, table_tennis=pm_meta.table_tennis,
                )
            except Exception as e:  # noqa: BLE001
                result.errors += 1
                log.warning("pair %s <-> %s: %s", pair.kalshi_ticker, pair.polymarket_slug, e)
                continue
            result.pairs_checked += 1
            in_play = pm_meta.game_start_time is not None and k_at >= pm_meta.game_start_time
            for opp in edges:
                if opp.profit > self.min_profit:
                    streak = self._extend_streak(scan_id, opp, k_at)
                    self.store.add_opportunity(scan_id, k_at, opp, skew, in_play, pm_meta.game_start_time,
                                               streak.count, streak.first_seen)
                    result.opportunities.append(opp)

    def _extend_streak(self, scan_id: int, opp: Opportunity, at: datetime) -> _Streak:
        key = (opp.kalshi_ticker, opp.polymarket_slug, opp.kalshi_side)
        previous = self._streaks.get(key)
        if previous is not None and previous.scan_id == self._last_scan_id:
            streak = _Streak(scan_id, previous.count + 1, previous.first_seen)
        else:
            streak = _Streak(scan_id, 1, at)
        self._streaks[key] = streak
        return streak

    def _pace_polymarket(self) -> None:
        if self._pm_requests:
            self.sleep(self.polymarket_pace)
        self._pm_requests += 1

    def _record_pair_snapshot(self, scan_id, at, pair, kalshi_book, pm_book, skew) -> None:
        costs = {}
        for kalshi_side, kalshi_asks, _, pm_asks in hedge_legs(pair, kalshi_book, pm_book):
            costs[kalshi_side] = kalshi_asks[0].price + pm_asks[0].price if kalshi_asks and pm_asks else None
        self.store.add_pair_snapshot(scan_id, at, pair.kalshi_ticker, pair.polymarket_slug, costs["yes"], costs["no"], skew)

    def _meta(self, ticker: str, now: datetime) -> _KalshiMeta:
        cached = self._kalshi_meta.get(ticker)
        if cached is None or now - cached.fetched_at > METADATA_TTL:
            market = self.kalshi.get_market(ticker)
            resolves_at = market.expected_expiration_time or market.close_time
            if resolves_at is None:
                raise ValueError("market has no expected expiration or close time")
            cached = self._kalshi_meta[ticker] = _KalshiMeta(market.event_ticker, resolves_at, now)
        return cached

    def _polymarket_meta(self, slug: str) -> _PolymarketMeta:
        now = self.clock()
        cached = self._pm_meta.get(slug)
        if cached is None or now - cached.fetched_at > METADATA_TTL:
            self._pace_polymarket()
            market = self.polymarket.get_market(slug)
            cached = self._pm_meta[slug] = _PolymarketMeta(
                is_table_tennis(market.sports_market_type), market.game_start_time, now)
        return cached

    def _event_fee_data(self, event_ticker: str) -> _EventFees:
        now = self.clock()
        cached = self._event_fees.get(event_ticker)
        if cached is None or now - cached.fetched_at > METADATA_TTL:
            series = self.kalshi.get_series_fee(event_ticker.split("-")[0])
            overrides = self.kalshi.get_event_fee_overrides(event_ticker)
            cached = self._event_fees[event_ticker] = _EventFees(series, overrides, now)
        return cached

    def _fee(self, event_ticker: str, at: datetime) -> KalshiFeeStructure:
        cached = self._event_fee_data(event_ticker)
        # Re-evaluated at every fetch so a scheduled override takes effect on time.
        return kalshi_effective_fee(cached.series, cached.overrides, at)


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
                    "scan: %d pairs, %d errors, %d skipped stale, %d opportunities%s", result.pairs_checked,
                    result.errors, result.skipped_stale, len(result.opportunities),
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
