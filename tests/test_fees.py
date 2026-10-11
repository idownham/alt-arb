"""Fee math, checked against hand calculations and the venues' own worked examples."""

from datetime import datetime, timezone
from decimal import Decimal as D

import httpx
import pytest

from altarb.pricing.fees import (
    KalshiFeeOverride,
    KalshiFeeStructure,
    UnsupportedFeeType,
    is_table_tennis,
    kalshi_effective_fee,
    kalshi_fee,
    kalshi_order_buy_cost,
    polymarket_maker_rebate,
    polymarket_order_taker_fee,
    polymarket_taker_fee,
)
from altarb.venues.kalshi import BASE_URL, KalshiClient

# --- Polymarket US: the six examples from docs.polymarket.us/fees ----------


@pytest.mark.parametrize(
    ("contracts", "price", "taker", "maker"),
    [
        # Ex 1: 0.0695 x 1000 x 0.10 x 0.90 = 6.255   -> 6.26 (half-even: 6 is even)
        #       0.0125 x 1000 x 0.09        = 1.125   -> 1.12 (half-even: 2 is even)
        ("1000", "0.10", "6.26", "1.12"),
        # Ex 2: 0.0695 x 1000 x 0.65 x 0.35 = 15.81125 -> 15.81 ; maker 2.84375 -> 2.84
        ("1000", "0.65", "15.81", "2.84"),
        # Ex 3: 0.0695 x 1000 x 0.30 x 0.70 = 14.595   -> 14.60 ; maker 2.625 -> 2.62
        ("1000", "0.30", "14.60", "2.62"),
        # Ex 4: same p(1-p) as Ex 1
        ("1000", "0.90", "6.26", "1.12"),
        # Ex 5: 0.0695 x 1000 x 0.25 = 17.375 -> 17.38 ; maker 3.125 -> 3.12
        ("1000", "0.50", "17.38", "3.12"),
    ],
)
def test_polymarket_doc_examples(contracts, price, taker, maker):
    assert polymarket_taker_fee(D(contracts), D(price)) == D(taker)
    assert polymarket_maker_rebate(D(contracts), D(price)) == D(maker)


def test_polymarket_table_tennis_doc_example():
    # Ex 6: 0.10 x 1000 x 0.25 = 25.00; maker rebate unchanged at 3.12
    assert polymarket_taker_fee(D(1000), D("0.50"), table_tennis=True) == D("25.00")
    assert is_table_tennis("table_tennis_match_winner")
    assert not is_table_tennis("tennis_match_winner")


def test_polymarket_fee_table_row():
    # Fee table, 100-lot at $0.49: 0.0695 x 100 x 0.49 x 0.51 = 1.736805 -> 1.74
    assert polymarket_taker_fee(D(100), D("0.49")) == D("1.74")


def test_polymarket_small_fee_rounds_to_zero():
    # 0.0695 x 1 x 0.01 x 0.99 = 0.000688 -> 0.00 (docs: "Can fees ever be zero? Yes")
    assert polymarket_taker_fee(D(1), D("0.01")) == D("0.00")


def test_polymarket_order_fee_capped_at_cumulative_rounding():
    # Three 1-contract fills at 0.50: each 0.017375 -> 0.02, per-fill sum 0.06.
    # Cumulative exact 0.052125 -> 0.05. The exchange charges the lower 0.05.
    fills = [(D(1), D("0.50"))] * 3
    assert polymarket_order_taker_fee(fills) == D("0.05")


def test_polymarket_order_fee_uses_per_fill_sum_when_lower():
    # Two 1-contract fills at 0.50 and 0.10: 0.017375 -> 0.02 and 0.006255 -> 0.01, sum 0.03.
    # Cumulative 0.02363 -> 0.02. Lower wins: 0.02.
    assert polymarket_order_taker_fee([(D(1), D("0.50")), (D(1), D("0.10"))]) == D("0.02")
    # A single fill is just its own rounded fee.
    assert polymarket_order_taker_fee([(D(1000), D("0.10"))]) == D("6.26")


def test_float_would_get_banker_rounding_wrong():
    # Why Decimal: 6.255 as a float is 6.25499999..., which rounds to 6.25, not 6.26.
    assert round(0.0695 * 1000 * 0.10 * 0.90, 2) == 6.25
    assert polymarket_taker_fee(D(1000), D("0.10")) == D("6.26")


# --- Kalshi ---------------------------------------------------------------

STANDARD = KalshiFeeStructure("quadratic_with_maker_fees", D(1))


def test_kalshi_taker_fee_peaks_at_half():
    # 0.07 x 1 x 0.50 x 0.50 = 0.0175 per contract (CLAUDE.md: "peaks ~1.75c at P = 0.50")
    assert kalshi_fee(D(1), D("0.50"), STANDARD) == D("0.017500")
    # 100 contracts: 1.75 exactly
    assert kalshi_fee(D(100), D("0.50"), STANDARD) == D("1.750000")


def test_kalshi_fee_rounds_up_to_micro_dollar():
    # 0.07 x 1 x 0.33 x 0.67 = 0.015477 exactly
    assert kalshi_fee(D(1), D("0.33"), STANDARD) == D("0.015477")
    # 0.07 x 0.37 x 0.45 x 0.55 = 0.006410250 -> 0.006411 (rounded up, not to nearest)
    assert kalshi_fee(D("0.37"), D("0.45"), STANDARD) == D("0.006411")


@pytest.mark.parametrize(
    ("fee_type", "maker_fee"),
    [
        ("quadratic", "0.000000"),  # no maker fee
        ("quadratic_with_maker_fees", "0.437500"),  # 0.25 x 1.75
        ("quadratic_with_combo_maker_fees", "0.875000"),  # 0.5 x 1.75
    ],
)
def test_kalshi_maker_fee_by_type(fee_type, maker_fee):
    structure = KalshiFeeStructure(fee_type, D(1))
    assert kalshi_fee(D(100), D("0.50"), structure, maker=True) == D(maker_fee)
    assert kalshi_fee(D(100), D("0.50"), structure) == D("1.750000")  # taker unchanged


def test_kalshi_multiplier_scales_fee():
    # MLB series uses multiplier 0.5: 0.07 x 0.5 x 100 x 0.25 = 0.875
    assert kalshi_fee(D(100), D("0.50"), KalshiFeeStructure("quadratic", D("0.5"))) == D("0.875000")


def test_kalshi_flat_fee_type_is_refused():
    with pytest.raises(UnsupportedFeeType):
        kalshi_fee(D(1), D("0.50"), KalshiFeeStructure("flat", D(1)))


def test_kalshi_order_cost_rounds_up_to_cent():
    # Buy 1 at 0.50: 0.50 + 0.0175 = 0.5175 -> $0.52
    assert kalshi_order_buy_cost([(D(1), D("0.50"))], STANDARD) == D("0.52")
    # Buy 100 at 0.50: 50 + 1.75 = $51.75 exactly
    assert kalshi_order_buy_cost([(D(100), D("0.50"))], STANDARD) == D("51.75")
    # Sweep two levels: 10 @ 0.40 (fee 0.168) + 5 @ 0.41 (fee 0.0846650 -> 0.084665)
    # = 4.00 + 2.05 + 0.168 + 0.084665 = 6.302665 -> $6.31
    assert kalshi_order_buy_cost([(D(10), D("0.40")), (D(5), D("0.41"))], STANDARD) == D("6.31")


def test_kalshi_rounding_doc_example():
    # docs.kalshi.com fee_rounding: revenue -$0.055, model fee $0.00363825
    # -> trade fee ceil_6dp = $0.003639, balance moves -$0.06.
    trade_fee = D("0.00363825").quantize(D("0.000001"), "ROUND_CEILING")
    assert trade_fee == D("0.003639")
    assert (D("0.055") + trade_fee).quantize(D("0.01"), "ROUND_CEILING") == D("0.06")


def test_invalid_inputs_rejected():
    for price in (D(0), D(1), D("1.2")):
        with pytest.raises(ValueError):
            polymarket_taker_fee(D(1), price)
    with pytest.raises(ValueError):
        kalshi_fee(D(-1), D("0.5"), STANDARD)


# --- Kalshi fee overrides -------------------------------------------------

SERIES = KalshiFeeStructure("quadratic_with_maker_fees", D("0.5"))


def ts(text: str) -> datetime:
    return datetime.fromisoformat(text).replace(tzinfo=timezone.utc)


def test_override_applies_only_after_scheduled_time():
    # Real case: KXMLBGAME-26OCT152000MILLAD switches from multiplier 0.5 to 1.
    overrides = [KalshiFeeOverride(ts("2026-10-16T01:00:00"), "quadratic_with_maker_fees", D(1))]

    assert kalshi_effective_fee(SERIES, overrides, ts("2026-10-16T00:59:59")) == SERIES
    assert kalshi_effective_fee(SERIES, overrides, ts("2026-10-16T01:00:00")).multiplier == D(1)


def test_latest_override_wins_and_clear_restores_series():
    overrides = [
        KalshiFeeOverride(ts("2026-10-16T01:00:00"), "quadratic", D(2)),
        KalshiFeeOverride(ts("2026-10-17T01:00:00"), None, None),  # cleared
    ]

    assert kalshi_effective_fee(SERIES, overrides, ts("2026-10-16T12:00:00")) == KalshiFeeStructure("quadratic", D(2))
    assert kalshi_effective_fee(SERIES, overrides, ts("2026-10-17T12:00:00")) == SERIES


def test_client_parses_series_fee_and_overrides():
    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path.endswith("/series/KXMLBGAME"):
            return httpx.Response(200, json={"series": {"fee_type": "quadratic_with_maker_fees", "fee_multiplier": 0.5}})
        return httpx.Response(200, json={"cursor": "", "event_fee_changes": [{
            "event_ticker": "KXMLBGAME-26OCT152000MILLAD", "fee_multiplier_override": 1,
            "fee_type_override": "quadratic_with_maker_fees", "scheduled_ts": "2026-10-16T01:00:00Z",
        }]})

    client = KalshiClient(http=httpx.Client(base_url=BASE_URL, transport=httpx.MockTransport(handler)))

    assert client.get_series_fee("KXMLBGAME") == SERIES
    [override] = client.get_event_fee_overrides("KXMLBGAME-26OCT152000MILLAD")
    assert override.multiplier == D(1) and override.scheduled == ts("2026-10-16T01:00:00")
