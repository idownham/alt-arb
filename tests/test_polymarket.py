from datetime import datetime, timezone
from decimal import Decimal

import httpx
import pytest

from altarb.venues.models import PriceLevel
from altarb.venues.polymarket import BASE_URL, PolymarketClient

SLUG = "aec-boxing-canalv-chrmbi-2026-10-31-canalv-chrmbi"

# Trimmed from a real /v1/markets response (2026-10-10).
MARKET = {
    "id": "8123",
    "slug": SLUG,
    "question": "Canelo Alvarez vs Christian Mbilli",
    "description": "This market will settle to the winner of the Canelo Alvarez vs. "
    "Christian Mbilli boxing match originally scheduled for Sep 12, 2026.",
    "category": "sports",
    "endDate": "2026-11-15T04:00:00Z",
    "gameStartTime": "2026-10-31T23:00:00Z",
    "active": True,
    "closed": False,
    "orderPriceMinTickSize": 0.001,
    "marketSides": [
        {"description": "Canelo Alvarez", "long": True, "price": "0.7500"},
        {"description": "Christian Mbilli", "long": False, "price": "0.26"},
    ],
}


def entry(px: str, qty: str) -> dict:
    return {"px": {"value": px, "currency": "USD"}, "qty": qty}


# Real book for SLUG: bids and offers on the YES instrument, best first.
BOOK = {
    "marketData": {
        "marketSlug": SLUG,
        "state": "MARKET_STATE_OPEN",
        "bids": [
            entry("0.7400", "200.0000"),
            entry("0.7300", "24415.0200"),
            entry("0.7200", "20125.0000"),
        ],
        "offers": [
            entry("0.7500", "207.1700"),
            entry("0.7600", "20958.5300"),
            entry("0.7700", "20164.4000"),
        ],
    }
}


def make_client(handler, **kwargs) -> PolymarketClient:
    http = httpx.Client(base_url=BASE_URL, transport=httpx.MockTransport(handler))
    return PolymarketClient(http=http, **kwargs)


def test_get_market_parses_fields():
    paths = []

    def handler(req: httpx.Request) -> httpx.Response:
        paths.append(req.url.path)
        return httpx.Response(200, json={"market": MARKET})

    market = make_client(handler).get_market(SLUG)

    assert paths == [f"/v1/market/slug/{SLUG}"]  # singular "market"
    assert market.yes_label == "Canelo Alvarez"
    assert market.no_label == "Christian Mbilli"
    assert market.tick_size == Decimal("0.001")  # exact, not 0.001000000000000000020816...
    assert market.end_date == datetime(2026, 11, 15, 4, 0, tzinfo=timezone.utc)
    assert "originally scheduled" in market.description


def test_orderbook_maps_offers_to_no_bids():
    # Hand check: shorting YES into the best bid (0.74) costs 1 - 0.74 = 0.26 to buy NO,
    # matching the market's displayed "No" price. Buying YES takes the best offer, 0.75.
    book = make_client(lambda req: httpx.Response(200, json=BOOK)).get_orderbook(SLUG)

    assert book.ticker == SLUG
    assert book.yes_bids[0] == PriceLevel(Decimal("0.74"), Decimal("200"))
    assert book.no_bids[0] == PriceLevel(Decimal("0.25"), Decimal("207.17"))
    assert book.yes_asks[0] == PriceLevel(Decimal("0.75"), Decimal("207.17"))
    assert book.no_asks[0] == PriceLevel(Decimal("0.26"), Decimal("200"))
    assert [lvl.price for lvl in book.yes_asks] == [Decimal(p) for p in ("0.75", "0.76", "0.77")]


def test_orderbook_sorts_unordered_levels():
    data = BOOK["marketData"]
    shuffled = {"marketData": {**data, "bids": data["bids"][::-1], "offers": data["offers"][::-1]}}

    book = make_client(lambda req: httpx.Response(200, json=shuffled)).get_orderbook(SLUG)

    assert book.yes_bids[0].price == Decimal("0.74")
    assert book.yes_asks[0].price == Decimal("0.75")


def test_empty_orderbook():
    empty = {"marketData": {"marketSlug": SLUG, "bids": [], "offers": []}}

    book = make_client(lambda req: httpx.Response(200, json=empty)).get_orderbook(SLUG)

    assert book.yes_bids == [] and book.no_bids == []


def test_list_markets_pages_by_offset_until_empty():
    seen = []

    def handler(req: httpx.Request) -> httpx.Response:
        params = req.url.params
        seen.append(params)
        pages = {"0": [MARKET, MARKET], "2": [{**MARKET, "slug": "third"}], "3": []}
        return httpx.Response(200, json={"markets": pages[params["offset"]]})

    slugs = [m.slug for m in make_client(handler).list_markets(
        categories=["sports"], sports_market_types=["SPORTS_MARKET_TYPE_MONEYLINE"], page_size=1000
    )]

    assert slugs == [SLUG, SLUG, "third"]
    assert [p["offset"] for p in seen] == ["0", "2", "3"]
    assert seen[0]["limit"] == "500"  # capped
    assert seen[0]["active"] == "true" and seen[0]["closed"] == "false"
    assert seen[0].get_list("categories") == ["sports"]
    assert seen[0].get_list("sportsMarketTypes") == ["SPORTS_MARKET_TYPE_MONEYLINE"]


def test_list_markets_respects_max_pages():
    client = make_client(lambda req: httpx.Response(200, json={"markets": [MARKET]}))

    assert len(list(client.list_markets(max_pages=2))) == 2


def test_retries_on_429_with_backoff():
    responses = iter([httpx.Response(429), httpx.Response(429), httpx.Response(200, json=BOOK)])
    sleeps = []

    client = make_client(lambda req: next(responses), sleep=sleeps.append)
    book = client.get_orderbook(SLUG)

    assert sleeps == [1, 2]
    assert book.yes_bids


def test_gives_up_after_max_retries():
    sleeps = []
    client = make_client(lambda req: httpx.Response(429), sleep=sleeps.append, max_retries=2)

    with pytest.raises(httpx.HTTPStatusError):
        client.get_orderbook(SLUG)
    assert sleeps == [1, 2]


def test_not_found_raises_without_retry():
    sleeps = []
    client = make_client(lambda req: httpx.Response(404), sleep=sleeps.append)

    with pytest.raises(httpx.HTTPStatusError):
        client.get_market("nope")
    assert sleeps == []


@pytest.mark.live
def test_live_fetch_markets_and_orderbook():
    with PolymarketClient() as client:
        market = next(client.list_markets(max_pages=1, page_size=5))
        fetched = client.get_market(market.slug)
        book = client.get_orderbook(market.slug)

    assert fetched.slug == market.slug
    for lvl in book.yes_bids + book.no_bids:
        assert Decimal(0) < lvl.price < Decimal(1)


def test_honors_retry_after_header():
    responses = iter([httpx.Response(429, headers={"Retry-After": "10"}), httpx.Response(200, json=BOOK)])
    sleeps = []

    make_client(lambda req: next(responses), sleep=sleeps.append).get_orderbook(SLUG)

    assert sleeps == [10.0]


def test_caps_retry_after_and_falls_back_on_bad_header():
    responses = iter([
        httpx.Response(429, headers={"Retry-After": "3600"}),
        httpx.Response(429, headers={"Retry-After": "Wed, 21 Oct 2026 07:28:00 GMT"}),
        httpx.Response(200, json=BOOK),
    ])
    sleeps = []

    make_client(lambda req: next(responses), sleep=sleeps.append).get_orderbook(SLUG)

    assert sleeps == [60.0, 2]  # capped at 60s; unparseable date -> exponential backoff (2nd attempt = 2s)


def test_list_league_markets_pages_until_short_page():
    seen = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append((req.url.path, req.url.params["offset"]))
        events = {"0": [{"markets": [MARKET]}, {"markets": [{**MARKET, "slug": "b"}]}],
                  "2": [{"markets": [{**MARKET, "slug": "c"}, {**MARKET, "slug": "d"}]}]}[req.url.params["offset"]]
        return httpx.Response(200, json={"type": "sport", "events": events})

    slugs = [m.slug for m in make_client(handler).list_league_markets("nba", page_size=2)]

    assert slugs == [SLUG, "b", "c", "d"]
    assert seen == [("/v2/leagues/nba/events", "0"), ("/v2/leagues/nba/events", "2")]  # page 2 was short: stop


def test_list_league_markets_empty_league():
    client = make_client(lambda req: httpx.Response(200, json={"type": "sport", "events": []}))

    assert list(client.list_league_markets("xyz")) == []
