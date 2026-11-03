from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

from fundpilot.core.portfolio import CreditView, OfferView, PortfolioState, RateType
from fundpilot.strategies import (
    AdaptiveFundingLadder,
    AutoRenewStrategy,
    FixedRateStrategy,
    FRRStrategy,
    OrderBookAwareStrategy,
    Strategy,
)
from tests.conftest import FIXED_NOW
from tests.helpers import level, state, trade

D = Decimal


def _pf(available: str = "1000", offers=(), credits=()) -> PortfolioState:  # type: ignore[no-untyped-def]
    return PortfolioState(FIXED_NOW, "UST", D(available), tuple(offers), tuple(credits))


def test_all_satisfy_protocol() -> None:
    for s in (
        FixedRateStrategy(D("0.0002")),
        FRRStrategy(),
        AutoRenewStrategy(),
        AdaptiveFundingLadder(),
        OrderBookAwareStrategy(),
    ):
        assert isinstance(s, Strategy)
        assert s.name


def test_fixed_rate() -> None:
    d = FixedRateStrategy(D("0.0003"), period_days=7, max_offer_size=D(400)).decide(
        state(FIXED_NOW), _pf("1000")
    )
    assert [(p.rate, p.period_days, p.amount) for p in d.place] == [
        (D("0.0003"), 7, D(400)),
        (D("0.0003"), 7, D(400)),
        (D("0.0003"), 7, D(200)),
    ]
    assert FixedRateStrategy(D("0.0003")).decide(state(FIXED_NOW), _pf("100")).is_empty


def test_frr_and_auto_renew() -> None:
    d = FRRStrategy().decide(state(FIXED_NOW), _pf())
    assert d.place[0].rate_type is RateType.FRR and d.place[0].rate is None
    keep = AutoRenewStrategy(keep_amount=D(900)).decide(state(FIXED_NOW), _pf())
    assert keep.is_empty  # 100 left after keep is below the minimum offer
    fixed = AutoRenewStrategy(rate=D("0.0001"), keep_amount=D(200)).decide(state(FIXED_NOW), _pf())
    assert fixed.place[0].amount == D(800) and fixed.place[0].rate == D("0.0001")


def test_ladder_tilts_long_when_rates_high() -> None:
    ladder = AdaptiveFundingLadder(min_history=10)
    for i in range(50):
        ladder.decide(state(FIXED_NOW + timedelta(minutes=i), frr="0.0001"), _pf("0"))
    high = ladder.weights(ladder.percentile(D("0.001")))
    low = ladder.weights(ladder.percentile(D("0.00001")))
    assert high["long"] > low["long"]
    assert high["short"] < low["short"]
    assert sum(high.values()) == D(1)


def test_ladder_neutral_without_history_and_allocates() -> None:
    ladder = AdaptiveFundingLadder()
    d = ladder.decide(state(FIXED_NOW, frr="0.0002"), _pf("10000"))
    by_period = {p.period_days: p.amount for p in d.place}
    assert by_period[2] == D(5000) and by_period[30] == D(3000) and by_period[120] == D(2000)


def test_ladder_prices_from_trades_and_reprices_old_offers() -> None:
    ladder = AdaptiveFundingLadder()
    trades = [trade(FIXED_NOW, r, "100", 2) for r in ("0.0001", "0.0002", "0.0003")]
    market = state(FIXED_NOW, frr="0.0005", trades=trades)
    assert ladder.price(ladder.buckets[0], market) == D("0.0002")
    assert ladder.price(ladder.buckets[2], market) == D("0.0005")  # FRR fallback
    old = OfferView(
        "o1", "fUST", RateType.FIXED, D("0.0009"), 2, D(500), D(500), FIXED_NOW - timedelta(hours=2)
    )
    d = ladder.decide(market, _pf("0", [old]))
    assert d.cancel == ("o1",)


def test_ladder_counts_existing_credits() -> None:
    ladder = AdaptiveFundingLadder()
    credit = CreditView(
        "c1",
        "fUST",
        RateType.FIXED,
        D("0.0002"),
        2,
        D(5000),
        FIXED_NOW,
        FIXED_NOW + timedelta(days=2),
    )
    d = ladder.decide(state(FIXED_NOW), _pf("5000", credits=[credit]))
    assert 2 not in {p.period_days for p in d.place}


def test_orderbook_depth_and_bid_cross() -> None:
    s = OrderBookAwareStrategy(depth_ahead=D(1000), undercut=D("0.00000001"))
    levels = [
        level("ask", "0.00020", "600"),
        level("ask", "0.00021", "600"),
        level("ask", "0.00030", "5000"),
        level("bid", "0.00010", "100"),
    ]
    assert s.target_rate(state(FIXED_NOW, levels=levels)) == D("0.00020999")
    crossed = [*levels, level("bid", "0.00025", "100")]
    assert s.target_rate(state(FIXED_NOW, levels=crossed)) == D("0.00025")
    assert s.decide(state(FIXED_NOW), _pf()).is_empty  # no asks -> no decision


def test_orderbook_reprices_only_old_offers() -> None:
    s = OrderBookAwareStrategy(depth_ahead=D(1))
    levels = [level("ask", "0.0002", "10")]
    young = OfferView("y", "fUST", RateType.FIXED, D("0.0009"), 2, D(500), D(500), FIXED_NOW)
    old = OfferView(
        "o", "fUST", RateType.FIXED, D("0.0009"), 2, D(500), D(500), FIXED_NOW - timedelta(hours=1)
    )
    d = s.decide(state(FIXED_NOW, levels=levels), _pf("0", [young, old]))
    assert d.cancel == ("o",)
    assert d.place[0].amount == D(500)
