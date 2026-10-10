"""Venue-neutral market-data types shared by every client.

Every binary market is modeled as YES bids and NO bids. Buying YES means
taking the best NO bid (YES ask = 1 - NO bid), and vice versa, so both
venues' books reduce to the same shape for edge calculation.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

ONE = Decimal("1")


@dataclass(frozen=True)
class PriceLevel:
    price: Decimal  # dollars per contract, 0 < price < 1
    size: Decimal  # contracts available at this price


@dataclass(frozen=True)
class OrderBook:
    ticker: str  # the venue's market identifier (Kalshi ticker, Polymarket slug)
    yes_bids: list[PriceLevel]  # best (highest) first
    no_bids: list[PriceLevel]  # best (highest) first

    @property
    def yes_asks(self) -> list[PriceLevel]:
        """Prices to buy YES, cheapest first (each is the complement of a NO bid)."""
        return [PriceLevel(ONE - lvl.price, lvl.size) for lvl in self.no_bids]

    @property
    def no_asks(self) -> list[PriceLevel]:
        """Prices to buy NO, cheapest first (each is the complement of a YES bid)."""
        return [PriceLevel(ONE - lvl.price, lvl.size) for lvl in self.yes_bids]
