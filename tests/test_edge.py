"""Edge math, with every expected number worked out by hand in the comments."""

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal as D

import pytest

from altarb.mapping.pairs import MarketPair
from altarb.pricing.edge import find_edges
from altarb.pricing.fees import KalshiFeeStructure
from altarb.venues.models import ONE, OrderBook, PriceLevel

FEE = KalshiFeeStructure("quadratic_with_maker_fees", D(1))
NOW = datetime(2026, 10, 10, tzinfo=timezone.utc)
IN_10_DAYS = NOW + timedelta(days=10)


def pair(inverted=False, verified=True) -> MarketPair:
    return MarketPair(
        kalshi_ticker="KX-TEST", polymarket_slug="pm-test", inverted=inverted, description="",
        resolution_notes="", expiry=date(2026, 10, 20), verified=verified,
        verified_by="Ian" if verified else "", verified_on=date(2026, 10, 10) if verified else None,
    )


def lv(price: str, size: str) -> PriceLevel:
    return PriceLevel(D(price), D(size))


def book_with_asks(yes_asks=(), no_asks=()) -> OrderBook:
    """Build a book from the asks we want (asks are complements of the opposite side's bids)."""
    return OrderBook(
        ticker="t",
        yes_bids=[PriceLevel(ONE - l.price, l.size) for l in no_asks],
        no_bids=[PriceLevel(ONE - l.price, l.size) for l in yes_asks],
    )


def edges(kalshi, pm, **kw):
    return find_edges(kw.pop("p", pair()), kalshi, pm, FEE, resolves_at=kw.pop("at", IN_10_DAYS), now=NOW, **kw)


def test_simple_edge_limited_by_shallower_book():
    # Kalshi YES ask 0.40 x 100, Polymarket NO ask 0.45 x 50 (i.e. PM YES bid 0.55).
    # Marginal: 0.40 + 0.45 + 0.07*0.4*0.6 (0.0168) + 0.0695*0.45*0.55 (0.01720125) = 0.88400125 < 1
    # Size = 50 (PM depth).
    # Kalshi: 50*0.40 = 20.00 + fee 0.84 = 20.84
    # PM:     50*0.45 = 22.50 + fee 0.0695*50*0.2475 = 0.8600625 -> 0.86  => 23.36
    # Profit: 50 - 20.84 - 23.36 = 5.80
    [opp] = edges(book_with_asks(yes_asks=[lv("0.40", "100")]), book_with_asks(no_asks=[lv("0.45", "50")]))

    assert (opp.kalshi_side, opp.polymarket_side) == ("yes", "no")
    assert opp.contracts == D(50)
    assert opp.kalshi_cost == D("20.84")
    assert opp.polymarket_cost == D("23.36")
    assert opp.profit == D("5.80")
    assert opp.capital == D("44.20")
    # Return 5.80 / 44.20; annualized over 10 days = x 36.5
    assert opp.return_on_capital == D("5.80") / D("44.20")
    assert opp.annualized_return == D("5.80") / D("44.20") * 365 / 10


def test_walks_depth_and_stops_at_unprofitable_level():
    # Kalshi YES asks: 0.40 x 30, then 0.53 x 100. PM NO ask: 0.45 x 100.
    # Level 1: 0.40 + 0.45 + fees = 0.884 -> take 30.
    # Level 2: 0.53 + 0.45 + 0.07*0.53*0.47 (0.017437) + 0.01720125 = 1.01463825 -> stop.
    # Kalshi: 12.00 + 0.504 = 12.504 -> 12.51 ; PM: 13.50 + 0.0695*30*0.2475 (0.5160375) -> 0.52 => 14.02
    # Profit: 30 - 12.51 - 14.02 = 3.47
    [opp] = edges(
        book_with_asks(yes_asks=[lv("0.40", "30"), lv("0.53", "100")]),
        book_with_asks(no_asks=[lv("0.45", "100")]),
    )

    assert opp.contracts == D(30)
    assert opp.kalshi_fills == [(D(30), D("0.40"))]
    assert opp.profit == D("3.47")


def test_takes_multiple_profitable_levels():
    # Kalshi YES: 0.40 x 30, 0.42 x 30. PM NO: 0.45 x 100. Both levels profitable.
    # Kalshi: 30*0.40 + 30*0.42 = 24.60; fees 0.504 + 0.07*30*0.2436 (0.51156) = 1.01556 -> 25.61556 -> 25.62
    # PM: 60*0.45 = 27.00; fee 0.0695*60*0.2475 = 1.032075 -> 1.03 => 28.03
    # Profit: 60 - 25.62 - 28.03 = 6.35
    [opp] = edges(
        book_with_asks(yes_asks=[lv("0.40", "30"), lv("0.42", "30")]),
        book_with_asks(no_asks=[lv("0.45", "100")]),
    )

    assert opp.contracts == D(60)
    assert opp.kalshi_fills == [(D(30), D("0.40")), (D(30), D("0.42"))]
    assert opp.polymarket_fills == [(D(60), D("0.45"))]
    assert opp.profit == D("6.35")


def test_rounding_can_erase_a_tiny_edge():
    # 1 contract: Kalshi YES 0.47, PM NO 0.49.
    # Marginal at exact fees: 0.47 + 0.49 + 0.017437 + 0.017375 = 0.994812 < 1 (looks profitable)
    # Rounded: Kalshi 0.487437 -> 0.49 ; PM 0.49 + 0.017375 -> 0.02 = 0.51. Total 1.00 -> profit 0 -> none.
    assert edges(book_with_asks(yes_asks=[lv("0.47", "1")]), book_with_asks(no_asks=[lv("0.49", "1")])) == []


def test_no_edge_when_prices_sum_too_high():
    # 0.50 + 0.49 = 0.99 before fees; fees push it over 1.
    assert edges(book_with_asks(yes_asks=[lv("0.50", "100")]), book_with_asks(no_asks=[lv("0.49", "100")])) == []


def test_checks_both_directions():
    # Only the Kalshi NO + PM YES direction is cheap.
    kalshi = book_with_asks(yes_asks=[lv("0.60", "10")], no_asks=[lv("0.40", "10")])
    pm = book_with_asks(yes_asks=[lv("0.45", "10")], no_asks=[lv("0.60", "10")])

    [opp] = edges(kalshi, pm)

    assert (opp.kalshi_side, opp.polymarket_side) == ("no", "yes")


def test_inverted_pair_hedges_yes_with_yes():
    # inverted: Kalshi YES == PM NO, so the hedge for Kalshi YES is PM YES.
    kalshi = book_with_asks(yes_asks=[lv("0.40", "10")])
    pm = book_with_asks(yes_asks=[lv("0.45", "10")], no_asks=[lv("0.45", "10")])

    [opp] = edges(kalshi, pm, p=pair(inverted=True))

    assert (opp.kalshi_side, opp.polymarket_side) == ("yes", "yes")


def test_max_contracts_caps_size():
    [opp] = edges(
        book_with_asks(yes_asks=[lv("0.40", "100")]), book_with_asks(no_asks=[lv("0.45", "100")]),
        max_contracts=D(10),
    )

    assert opp.contracts == D(10)


def test_ranks_by_annualized_return():
    # Same books, both directions profitable; the cheaper direction has the better return.
    kalshi = book_with_asks(yes_asks=[lv("0.40", "10")], no_asks=[lv("0.45", "10")])
    pm = book_with_asks(yes_asks=[lv("0.45", "10")], no_asks=[lv("0.45", "10")])

    first, second = edges(kalshi, pm)

    assert first.annualized_return > second.annualized_return
    assert first.kalshi_side == "yes"  # 0.40 + 0.45 beats 0.45 + 0.45


def test_annualization_floors_days_at_one():
    soon = NOW + timedelta(hours=3)
    [opp] = edges(book_with_asks(yes_asks=[lv("0.40", "50")]), book_with_asks(no_asks=[lv("0.45", "50")]), at=soon)

    assert opp.days_to_resolution == D("0.125")
    assert opp.annualized_return == opp.return_on_capital * 365  # not x 2920


def test_empty_books_have_no_edge():
    assert edges(book_with_asks(), book_with_asks(no_asks=[lv("0.45", "50")])) == []


def test_refuses_unverified_pair():
    with pytest.raises(ValueError, match="not verified"):
        edges(book_with_asks(yes_asks=[lv("0.40", "1")]), book_with_asks(no_asks=[lv("0.45", "1")]),
              p=pair(verified=False))
