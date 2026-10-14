"""Trading fee models for Kalshi and Polymarket US.

All math is in Decimal. Prices are dollars per contract (0 < p < 1) and
contract counts may be fractional. A fill is (contracts, price).

Polymarket US (docs.polymarket.us/fees, effective 2026-10-07):
    fee = Θ × C × p × (1 - p), banker's-rounded to the cent.
    Θ = 0.0695 taker (0.10 for table tennis); makers get a 0.0125 rebate.
    Across one order's fills, total taker fee never exceeds the rounded
    cumulative exact fee.

Kalshi (docs.kalshi.com/getting_started/fee_rounding, series fee_type):
    fee = 0.07 × M × C × p × (1 - p) for takers, M = series fee_multiplier.
    Makers pay 0.25× that under quadratic_with_maker_fees, 0.5× under
    quadratic_with_combo_maker_fees, nothing under quadratic.
    Each fill's fee is rounded up to $0.000001; a retail (non-direct) account's
    balance moves in whole cents, so an order's total cash out rounds up to the cent.
    The 0.07 base and the 0.0175 (= 0.25 x 0.07) maker rate match Kalshi's fee schedule
    PDF (kalshi.com/docs/kalshi-fee-schedule.pdf, effective 2026-07-07; checked 2026-10-13).
    Its fee table is pinned in tests/test_fees.py. The PDF rounds fee + position cost up to
    a centicent; retail cash moves in cents, so rounding the order total up to the cent is
    never optimistic.
    `flat` fees are in that PDF only and are deliberately unsupported.

Polymarket's monthly taker-volume rebates are ignored (conservative).
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from decimal import ROUND_CEILING, ROUND_HALF_EVEN, Decimal

Fill = tuple[Decimal, Decimal]  # (contracts, price)

CENT = Decimal("0.01")
MICRO = Decimal("0.000001")

POLYMARKET_TAKER = Decimal("0.0695")
POLYMARKET_TAKER_TABLE_TENNIS = Decimal("0.10")
POLYMARKET_MAKER_REBATE = Decimal("0.0125")

KALSHI_TAKER = Decimal("0.07")  # Kalshi fee schedule PDF, effective 2026-07-07
KALSHI_MAKER_RATIO = {
    "quadratic": Decimal("0"),
    "quadratic_with_maker_fees": Decimal("0.25"),
    "quadratic_with_combo_maker_fees": Decimal("0.5"),
}


class UnsupportedFeeType(ValueError):
    """A fee structure this model can't price. Skip the market rather than guess."""


def _variance(contracts: Decimal, price: Decimal) -> Decimal:
    if not Decimal(0) < price < Decimal(1):
        raise ValueError(f"price must be strictly between 0 and 1, got {price}")
    if contracts < 0:
        raise ValueError(f"contracts must be non-negative, got {contracts}")
    return contracts * price * (1 - price)


# --- Polymarket US ---------------------------------------------------------


def is_table_tennis(sports_market_type: str) -> bool:
    return sports_market_type.startswith("table_tennis")


def polymarket_taker_fee(contracts: Decimal, price: Decimal, table_tennis: bool = False) -> Decimal:
    """Fee for one taker fill. Same for buying and selling (p and 1-p give the same p(1-p))."""
    theta = POLYMARKET_TAKER_TABLE_TENNIS if table_tennis else POLYMARKET_TAKER
    return (theta * _variance(contracts, price)).quantize(CENT, ROUND_HALF_EVEN)


def polymarket_maker_rebate(contracts: Decimal, price: Decimal) -> Decimal:
    """Rebate credited for one maker fill (a positive amount you receive)."""
    return (POLYMARKET_MAKER_REBATE * _variance(contracts, price)).quantize(CENT, ROUND_HALF_EVEN)


def polymarket_order_taker_fee(fills: Iterable[Fill], table_tennis: bool = False) -> Decimal:
    """Total taker fee for one order that sweeps several price levels.

    Each fill is banker's-rounded, but the total is capped at the banker's
    rounding of the cumulative exact fee (the exchange only adjusts down).
    """
    theta = POLYMARKET_TAKER_TABLE_TENNIS if table_tennis else POLYMARKET_TAKER
    fills = list(fills)
    per_fill = sum((polymarket_taker_fee(c, p, table_tennis) for c, p in fills), Decimal(0))
    cumulative = (theta * sum((_variance(c, p) for c, p in fills), Decimal(0))).quantize(CENT, ROUND_HALF_EVEN)
    return min(per_fill, cumulative)


# --- Kalshi ----------------------------------------------------------------


@dataclass(frozen=True)
class KalshiFeeStructure:
    fee_type: str
    multiplier: Decimal

    def taker_coefficient(self) -> Decimal:
        self._check()
        return KALSHI_TAKER * self.multiplier

    def maker_coefficient(self) -> Decimal:
        self._check()
        return KALSHI_TAKER * self.multiplier * KALSHI_MAKER_RATIO[self.fee_type]

    def _check(self) -> None:
        if self.fee_type not in KALSHI_MAKER_RATIO:
            raise UnsupportedFeeType(f"Kalshi fee_type {self.fee_type!r} is not modeled")


@dataclass(frozen=True)
class KalshiFeeOverride:
    scheduled: datetime
    fee_type: str | None  # None on both fields means the override was cleared
    multiplier: Decimal | None


def kalshi_effective_fee(
    series: KalshiFeeStructure, overrides: Iterable[KalshiFeeOverride], at: datetime
) -> KalshiFeeStructure:
    """The fee structure in force at `at`: the latest override already scheduled, else the series'."""
    active = [o for o in overrides if o.scheduled <= at]
    if not active:
        return series
    latest = max(active, key=lambda o: o.scheduled)
    if latest.fee_type is None and latest.multiplier is None:
        return series  # override cleared
    return KalshiFeeStructure(
        fee_type=latest.fee_type or series.fee_type,
        multiplier=latest.multiplier if latest.multiplier is not None else series.multiplier,
    )


def kalshi_fee(contracts: Decimal, price: Decimal, fee: KalshiFeeStructure, maker: bool = False) -> Decimal:
    """Trade fee for one fill, rounded up to $0.000001."""
    coefficient = fee.maker_coefficient() if maker else fee.taker_coefficient()
    return (coefficient * _variance(contracts, price)).quantize(MICRO, ROUND_CEILING)


def kalshi_order_buy_cost(fills: Iterable[Fill], fee: KalshiFeeStructure, maker: bool = False) -> Decimal:
    """Total cash a retail account pays to buy these fills: price + fees, rounded up to the cent."""
    total = sum((c * p + kalshi_fee(c, p, fee, maker) for c, p in fills), Decimal(0))
    return total.quantize(CENT, ROUND_CEILING)
