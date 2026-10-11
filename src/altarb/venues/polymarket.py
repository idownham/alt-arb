"""Read-only Polymarket US client: markets and order books from the public gateway.

Polymarket US has one instrument per market. Buying it is the YES side;
shorting it is the NO side. Its book has `bids` and `offers` on that YES
instrument, which map onto the shared OrderBook like this:

- YES bids  = the book's bids
- NO bids   = the book's offers, complemented: a YES offer at p is a NO bid at 1 - p
              (shorting YES at p costs 1 - p, the same as buying NO at 1 - p)

So `OrderBook.yes_asks` recovers the original offers, and `OrderBook.no_asks`
is the cost of shorting into the bids. Prices and sizes are parsed to Decimal.
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
from altarb.venues.models import ONE, OrderBook, PriceLevel

BASE_URL = "https://gateway.polymarket.us"
MAX_PAGE_SIZE = 500  # the gateway silently caps larger requests at 500


@dataclass(frozen=True)
class Market:
    id: str
    slug: str
    question: str
    description: str  # full rules text, including postponement/cancellation handling
    category: str
    sports_market_type: str  # e.g. "moneyline", "hockey_team_full_game_winner"
    yes_label: str  # e.g. "Yes", or the team name in a two-team market
    no_label: str
    end_date: datetime | None
    game_start_time: datetime | None
    active: bool
    closed: bool
    tick_size: Decimal | None
    raw: dict[str, Any] = field(repr=False, compare=False)

    @classmethod
    def from_api(cls, data: dict[str, Any]) -> Market:
        sides = data.get("marketSides") or []
        yes = next((s for s in sides if s.get("long") is True), {})
        no = next((s for s in sides if s.get("long") is False), {})
        tick = data.get("orderPriceMinTickSize")
        return cls(
            id=str(data["id"]),
            slug=data["slug"],
            question=data.get("question", ""),
            description=data.get("description", ""),
            category=data.get("category", ""),
            sports_market_type=data.get("sportsMarketType") or "",
            yes_label=yes.get("description", ""),
            no_label=no.get("description", ""),
            end_date=_parse_time(data.get("endDate")),
            game_start_time=_parse_time(data.get("gameStartTime")),
            active=bool(data.get("active")),
            closed=bool(data.get("closed")),
            # Sent as a JSON number (0.001); go through str() so Decimal stays exact.
            tick_size=Decimal(str(tick)) if tick is not None else None,
            raw=data,
        )


class PolymarketClient:
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

    def __enter__(self) -> PolymarketClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def close(self) -> None:
        self._http.close()

    def list_markets(
        self,
        active: bool = True,
        closed: bool = False,
        categories: list[str] | None = None,
        sports_market_types: list[str] | None = None,
        page_size: int = MAX_PAGE_SIZE,
        max_pages: int | None = None,
    ) -> Iterator[Market]:
        """Yield markets, following offset pagination until an empty page.

        sports_market_types takes values like "SPORTS_MARKET_TYPE_MONEYLINE".
        """
        params: dict[str, Any] = {
            "active": str(active).lower(),
            "closed": str(closed).lower(),
            "limit": min(page_size, MAX_PAGE_SIZE),
            "offset": 0,
        }
        if categories:
            params["categories"] = categories
        if sports_market_types:
            params["sportsMarketTypes"] = sports_market_types

        pages = 0
        while True:
            items = self._get("/v1/markets", params).get("markets", [])
            if not items:
                return
            for item in items:
                yield Market.from_api(item)
            pages += 1
            if max_pages is not None and pages >= max_pages:
                return
            params["offset"] += len(items)

    def list_league_markets(self, league: str, page_size: int = 100) -> Iterator[Market]:
        """Yield every market in one league's events, e.g. league="nba".

        Much cheaper than list_markets for a single league: the whole NBA is one
        request, versus ~145 to list every sports market. `league` is the code that
        appears in market slugs (aec-<league>-...).
        """
        offset = 0
        while True:
            events = self._get(f"/v2/leagues/{league}/events", {"limit": page_size, "offset": offset}).get("events") or []
            for event in events:
                for item in event.get("markets") or []:
                    yield Market.from_api(item)
            if len(events) < page_size:
                return
            offset += len(events)

    def get_market(self, slug: str) -> Market:
        # Note the singular "market" here, unlike the list and book endpoints.
        return Market.from_api(self._get(f"/v1/market/slug/{slug}")["market"])

    def get_orderbook(self, slug: str) -> OrderBook:
        data = self._get(f"/v1/markets/{slug}/book")["marketData"]
        bids = _parse_levels(data.get("bids"))
        offers = _parse_levels(data.get("offers"))
        return OrderBook(
            ticker=slug,
            yes_bids=sorted(bids, key=lambda lvl: lvl.price, reverse=True),
            no_bids=sorted(
                (PriceLevel(ONE - lvl.price, lvl.size) for lvl in offers),
                key=lambda lvl: lvl.price,
                reverse=True,
            ),
        )

    def _get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        return get_json(self._http, path, params, self._max_retries, self._sleep)


def _parse_levels(raw: list[dict[str, Any]] | None) -> list[PriceLevel]:
    return [PriceLevel(Decimal(e["px"]["value"]), Decimal(e["qty"])) for e in raw or []]


def _parse_time(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value.replace("Z", "+00:00")) if value else None
