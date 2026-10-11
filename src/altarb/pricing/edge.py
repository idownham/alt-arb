"""Net-of-fee, depth-aware arbitrage edge for a verified Kalshi <-> Polymarket pair.

A hedged position holds one leg on each venue that pays out on complementary
outcomes, so N contracts pay exactly N x $1.00 at resolution (a tie pays $0.50
on each leg: still $1.00). Profit is that payout minus both legs' cost incl. fees.

For a pair with inverted = false (Kalshi YES == Polymarket YES), the hedges are
    Kalshi YES + Polymarket NO,   or   Kalshi NO + Polymarket YES.
For inverted = true (Kalshi YES == Polymarket NO), they are YES + YES or NO + NO.

Sizing walks both ask ladders together, cheapest first, while the next contract
is profitable at exact (unrounded) fees, then re-prices every stopping point
with each venue's real rounding and keeps the most profitable. Both legs are
assumed to be taker orders. Annualized return is simple (not compounded):
return x 365 / days, with days floored at 1.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from altarb.mapping.pairs import MarketPair
from altarb.pricing.fees import (
    POLYMARKET_TAKER,
    POLYMARKET_TAKER_TABLE_TENNIS,
    Fill,
    KalshiFeeStructure,
    kalshi_order_buy_cost,
    polymarket_order_taker_fee,
)
from altarb.venues.models import OrderBook, PriceLevel

ONE = Decimal(1)
SECONDS_PER_DAY = Decimal(86400)


@dataclass(frozen=True)
class Opportunity:
    kalshi_ticker: str
    polymarket_slug: str
    kalshi_side: str  # "yes" or "no": what to buy on Kalshi
    polymarket_side: str  # "yes" or "no": what to buy on Polymarket (no = short the instrument)
    contracts: Decimal
    kalshi_fills: list[Fill]
    polymarket_fills: list[Fill]
    kalshi_cost: Decimal  # price + fees, rounded as the venue charges
    polymarket_cost: Decimal
    profit: Decimal  # contracts x $1 - both costs
    return_on_capital: Decimal  # profit / (kalshi_cost + polymarket_cost)
    days_to_resolution: Decimal
    annualized_return: Decimal  # return_on_capital x 365 / max(days, 1)

    @property
    def capital(self) -> Decimal:
        return self.kalshi_cost + self.polymarket_cost


def find_edges(
    pair: MarketPair,
    kalshi_book: OrderBook,
    polymarket_book: OrderBook,
    kalshi_fee: KalshiFeeStructure,
    resolves_at: datetime,
    now: datetime,
    table_tennis: bool = False,
    max_contracts: Decimal | None = None,
) -> list[Opportunity]:
    """Profitable hedges for this pair (0, 1 or 2 directions), best annualized first."""
    if not pair.verified:
        raise ValueError(f"{pair.kalshi_ticker} <-> {pair.polymarket_slug} is not verified (CLAUDE.md rule 3)")

    days = Decimal((resolves_at - now).total_seconds()) / SECONDS_PER_DAY
    pm_theta = POLYMARKET_TAKER_TABLE_TENNIS if table_tennis else POLYMARKET_TAKER
    directions = [
        ("yes", kalshi_book.yes_asks, "yes" if pair.inverted else "no"),
        ("no", kalshi_book.no_asks, "no" if pair.inverted else "yes"),
    ]
    found = []
    for kalshi_side, kalshi_asks, pm_side in directions:
        pm_asks = polymarket_book.yes_asks if pm_side == "yes" else polymarket_book.no_asks
        best = _best_size(kalshi_asks, pm_asks, kalshi_fee, pm_theta, table_tennis, max_contracts)
        if best is None:
            continue
        contracts, k_fills, p_fills, k_cost, p_cost = best
        profit = contracts - k_cost - p_cost
        ret = profit / (k_cost + p_cost)
        found.append(Opportunity(
            kalshi_ticker=pair.kalshi_ticker,
            polymarket_slug=pair.polymarket_slug,
            kalshi_side=kalshi_side,
            polymarket_side=pm_side,
            contracts=contracts,
            kalshi_fills=k_fills,
            polymarket_fills=p_fills,
            kalshi_cost=k_cost,
            polymarket_cost=p_cost,
            profit=profit,
            return_on_capital=ret,
            days_to_resolution=days,
            annualized_return=ret * 365 / max(days, ONE),
        ))
    return sorted(found, key=lambda o: o.annualized_return, reverse=True)


def _best_size(
    kalshi_asks: list[PriceLevel],
    pm_asks: list[PriceLevel],
    kalshi_fee: KalshiFeeStructure,
    pm_theta: Decimal,
    table_tennis: bool,
    max_contracts: Decimal | None,
) -> tuple[Decimal, list[Fill], list[Fill], Decimal, Decimal] | None:
    k_coef = kalshi_fee.taker_coefficient()
    k_fills: list[Fill] = []
    p_fills: list[Fill] = []
    candidates = []
    i = j = 0
    k_left = kalshi_asks[0].size if kalshi_asks else Decimal(0)
    p_left = pm_asks[0].size if pm_asks else Decimal(0)
    total = Decimal(0)

    while i < len(kalshi_asks) and j < len(pm_asks):
        kp, pp = kalshi_asks[i].price, pm_asks[j].price
        marginal = kp + pp + k_coef * kp * (1 - kp) + pm_theta * pp * (1 - pp)
        if marginal >= ONE:
            break  # the next contract loses money even before rounding; deeper levels only cost more
        qty = min(k_left, p_left)
        if max_contracts is not None:
            qty = min(qty, max_contracts - total)
        if qty <= 0:
            break
        _add_fill(k_fills, kp, qty)
        _add_fill(p_fills, pp, qty)
        total += qty
        k_left -= qty
        p_left -= qty
        # Price this stopping point exactly as the venues would charge it.
        k_cost = kalshi_order_buy_cost(k_fills, kalshi_fee)
        p_cost = sum((c * p for c, p in p_fills), Decimal(0)) + polymarket_order_taker_fee(p_fills, table_tennis)
        candidates.append((total, list(k_fills), list(p_fills), k_cost, p_cost))
        if k_left == 0:
            i += 1
            k_left = kalshi_asks[i].size if i < len(kalshi_asks) else Decimal(0)
        if p_left == 0:
            j += 1
            p_left = pm_asks[j].size if j < len(pm_asks) else Decimal(0)

    profitable = [c for c in candidates if c[0] - c[3] - c[4] > 0]
    return max(profitable, key=lambda c: c[0] - c[3] - c[4], default=None)


def _add_fill(fills: list[Fill], price: Decimal, qty: Decimal) -> None:
    # One fill per price level: extend the last fill if we're still on the same level.
    if fills and fills[-1][1] == price:
        fills[-1] = (fills[-1][0] + qty, price)
    else:
        fills.append((qty, price))
