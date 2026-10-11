"""Read-only Kalshi client: markets and order books from the public REST API.

Kalshi sends prices as dollar strings ("0.4500") and contract counts as
fixed-point strings ("831.97"); both are parsed to Decimal, never float.

Order books contain bids only. Buying YES means taking the best NO bid, so the
YES ask at a level is 1 - (NO bid price), and vice versa.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any

import httpx

from altarb.venues.http import get_json
from altarb.venues.models import OrderBook, PriceLevel

BASE_URL = "https://api.elections.kalshi.com/trade-api/v2"


@dataclass(frozen=True)
class Market:
    ticker: str
    event_ticker: str
    title: str
    status: str
    rules_primary: str
    close_time: datetime | None
    expected_expiration_time: datetime | None
    yes_bid: Decimal | None
    yes_ask: Decimal | None
    no_bid: Decimal | None
    no_ask: Decimal | None
    raw: dict[str, Any] = field(repr=False, compare=False)

    @classmethod
    def from_api(cls, data: dict[str, Any]) -> Market:
        return cls(
            ticker=data["ticker"],
            event_ticker=data["event_ticker"],
            title=data.get("title", ""),
            status=data.get("status", ""),
            rules_primary=data.get("rules_primary", ""),
            close_time=_parse_time(data.get("close_time")),
            expected_expiration_time=_parse_time(data.get("expected_expiration_time")),
            yes_bid=_parse_decimal(data.get("yes_bid_dollars")),
            yes_ask=_parse_decimal(data.get("yes_ask_dollars")),
            no_bid=_parse_decimal(data.get("no_bid_dollars")),
            no_ask=_parse_decimal(data.get("no_ask_dollars")),
            raw=data,
        )


class KalshiClient:
    """Public market-data client. Holds no credentials and cannot place orders."""

    def __init__(
        self,
        base_url: str = BASE_URL,
        timeout: float = 10.0,
        http: httpx.Client | None = None,
        max_retries: int = 3,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._http = http or httpx.Client(base_url=base_url, timeout=timeout)
        self._max_retries = max_retries
        self._sleep = sleep

    def __enter__(self) -> KalshiClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def close(self) -> None:
        self._http.close()

    def list_markets(
        self,
        status: str = "open",
        series_ticker: str | None = None,
        event_ticker: str | None = None,
        include_combos: bool = False,
        page_size: int = 1000,
        max_pages: int | None = None,
    ) -> Iterator[Market]:
        """Yield markets, following Kalshi's cursor pagination.

        Multivariate "combo" markets are excluded by default: they never have a
        Polymarket counterpart and otherwise dominate the listing.
        """
        params: dict[str, Any] = {"status": status, "limit": page_size}
        if series_ticker:
            params["series_ticker"] = series_ticker
        if event_ticker:
            params["event_ticker"] = event_ticker
        if not include_combos:
            params["mve_filter"] = "exclude"

        pages = 0
        while True:
            data = self._get("/markets", params)
            for item in data.get("markets", []):
                yield Market.from_api(item)
            pages += 1
            cursor = data.get("cursor")
            if not cursor or (max_pages is not None and pages >= max_pages):
                return
            params["cursor"] = cursor

    def get_market(self, ticker: str) -> Market:
        return Market.from_api(self._get(f"/markets/{ticker}")["market"])

    def get_orderbook(self, ticker: str, depth: int = 0) -> OrderBook:
        """Fetch the order book. depth=0 returns every level."""
        data = self._get(f"/markets/{ticker}/orderbook", {"depth": depth})
        book = data["orderbook_fp"]
        return OrderBook(
            ticker=ticker,
            yes_bids=_parse_levels(book.get("yes_dollars")),
            no_bids=_parse_levels(book.get("no_dollars")),
        )

    def _get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        return get_json(self._http, path, params, self._max_retries, self._sleep)


def _parse_levels(raw: list[list[str]] | None) -> list[PriceLevel]:
    # The API returns levels worst-to-best; sort explicitly rather than rely on it.
    levels = [PriceLevel(Decimal(price), Decimal(size)) for price, size in raw or []]
    return sorted(levels, key=lambda lvl: lvl.price, reverse=True)


def _parse_decimal(value: str | None) -> Decimal | None:
    return Decimal(value) if value not in (None, "") else None


def _parse_time(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value.replace("Z", "+00:00")) if value else None
