from datetime import datetime, timezone
from decimal import Decimal

import httpx
import pytest

from altarb.venues.kalshi import BASE_URL, KalshiClient, PriceLevel

# Trimmed from a real response for KXNHLGAME-26OCT16BOSANA-BOS (2026-10-10).
MARKET = {
    "ticker": "KXNHLGAME-26OCT16BOSANA-BOS",
    "event_ticker": "KXNHLGAME-26OCT16BOSANA",
    "title": "Boston wins",
    "status": "active",
    "rules_primary": "If Boston wins the Boston vs Anaheim NHL game originally "
    "scheduled for Oct 16, 2026, then the market resolves to Yes.",
    "close_time": "2026-10-19T02:00:00Z",
    "expected_expiration_time": "2026-10-17T05:00:00Z",
    "yes_bid_dollars": "0.4500",
    "yes_ask_dollars": "0.5100",
    "no_bid_dollars": "0.4900",
    "no_ask_dollars": "0.5500",
}

# Real order book, in the API's own (worst-to-best) order.
ORDERBOOK = {
    "orderbook_fp": {
        "no_dollars": [
            ["0.4200", "10.00"],
            ["0.4400", "47.00"],
            ["0.4700", "830.00"],
            ["0.4800", "717.00"],
            ["0.4900", "831.97"],
        ],
        "yes_dollars": [
            ["0.3400", "25.00"],
            ["0.3600", "10.00"],
            ["0.3700", "87.00"],
            ["0.4400", "37.00"],
            ["0.4500", "50.00"],
        ],
    }
}


def make_client(handler) -> KalshiClient:
    http = httpx.Client(base_url=BASE_URL, transport=httpx.MockTransport(handler))
    return KalshiClient(http=http)


def test_get_market_parses_decimals_and_times():
    client = make_client(lambda req: httpx.Response(200, json={"market": MARKET}))

    market = client.get_market("KXNHLGAME-26OCT16BOSANA-BOS")

    assert market.title == "Boston wins"
    assert market.yes_bid == Decimal("0.45")
    assert market.yes_ask == Decimal("0.51")
    assert isinstance(market.yes_bid, Decimal)
    assert market.close_time == datetime(2026, 10, 19, 2, 0, tzinfo=timezone.utc)


def test_orderbook_bids_sorted_best_first():
    client = make_client(lambda req: httpx.Response(200, json=ORDERBOOK))

    book = client.get_orderbook("KXNHLGAME-26OCT16BOSANA-BOS")

    assert book.yes_bids[0] == PriceLevel(Decimal("0.45"), Decimal("50"))
    assert book.no_bids[0] == PriceLevel(Decimal("0.49"), Decimal("831.97"))
    assert [lvl.price for lvl in book.yes_bids] == sorted(
        (lvl.price for lvl in book.yes_bids), reverse=True
    )


def test_asks_are_complements_of_opposite_bids():
    # Hand check: best NO bid 0.49 -> YES ask 1 - 0.49 = 0.51, which matches the
    # market's own yes_ask_dollars. Best YES bid 0.45 -> NO ask 0.55 = no_ask_dollars.
    client = make_client(lambda req: httpx.Response(200, json=ORDERBOOK))

    book = client.get_orderbook("KXNHLGAME-26OCT16BOSANA-BOS")

    assert book.yes_asks[0] == PriceLevel(Decimal("0.51"), Decimal("831.97"))
    assert book.no_asks[0] == PriceLevel(Decimal("0.55"), Decimal("50"))
    assert [lvl.price for lvl in book.yes_asks] == [
        Decimal(p) for p in ("0.51", "0.52", "0.53", "0.56", "0.58")
    ]


def test_empty_orderbook():
    client = make_client(
        lambda req: httpx.Response(200, json={"orderbook_fp": {"yes_dollars": [], "no_dollars": []}})
    )

    book = client.get_orderbook("X")

    assert book.yes_bids == [] and book.yes_asks == []


def test_list_markets_follows_cursor_and_excludes_combos():
    seen_params = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen_params.append(dict(req.url.params))
        if "cursor" not in req.url.params:
            return httpx.Response(200, json={"markets": [MARKET], "cursor": "page2"})
        return httpx.Response(200, json={"markets": [{**MARKET, "ticker": "SECOND"}], "cursor": ""})

    client = make_client(handler)

    tickers = [m.ticker for m in client.list_markets(series_ticker="KXNHLGAME")]

    assert tickers == ["KXNHLGAME-26OCT16BOSANA-BOS", "SECOND"]
    assert seen_params[0]["mve_filter"] == "exclude"
    assert seen_params[0]["series_ticker"] == "KXNHLGAME"
    assert seen_params[1]["cursor"] == "page2"


def test_list_markets_respects_max_pages():
    client = make_client(lambda req: httpx.Response(200, json={"markets": [MARKET], "cursor": "more"}))

    assert len(list(client.list_markets(max_pages=3))) == 3


def test_http_error_raises():
    client = make_client(lambda req: httpx.Response(404, json={"error": "not found"}))

    with pytest.raises(httpx.HTTPStatusError):
        client.get_market("NOPE")


@pytest.mark.live
def test_live_fetch_markets_and_orderbook():
    with KalshiClient() as client:
        market = next(client.list_markets(max_pages=1, page_size=5))
        book = client.get_orderbook(market.ticker)

    assert market.ticker
    for lvl in book.yes_bids + book.no_bids:
        assert Decimal(0) < lvl.price < Decimal(1)


def test_retries_on_429_with_backoff():
    responses = iter([httpx.Response(429), httpx.Response(200, json={"market": MARKET})])
    sleeps = []
    http = httpx.Client(base_url=BASE_URL, transport=httpx.MockTransport(lambda req: next(responses)))

    market = KalshiClient(http=http, sleep=sleeps.append).get_market("KXNHLGAME-26OCT16BOSANA-BOS")

    assert sleeps == [1]
    assert market.title == "Boston wins"
