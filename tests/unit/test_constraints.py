from __future__ import annotations

from decimal import Decimal

from fundpilot.core.portfolio import OfferView, PortfolioState, RateType
from fundpilot.strategies.base import Decision, OfferRequest
from fundpilot.strategies.constraints import FundingConstraints, enforce, split_amount
from tests.conftest import FIXED_NOW

D = Decimal
C = FundingConstraints()


def _pf(available: str, offers: tuple[OfferView, ...] = ()) -> PortfolioState:
    return PortfolioState(FIXED_NOW, "UST", D(available), offers)


def _req(amount: str, period: int = 2, rate: str | None = "0.0002") -> OfferRequest:
    rt = RateType.FIXED if rate is not None else RateType.FRR
    return OfferRequest(rt, None if rate is None else D(rate), period, D(amount))


def test_caps_to_available_and_rounds_down() -> None:
    out, rej = enforce(Decision((_req("1000.123456789"),)), _pf("500.999999999"), C)
    assert out.place[0].amount == D("500.99999999")
    assert rej == []


def test_rejects_below_minimum_and_bad_period() -> None:
    out, rej = enforce(
        Decision((_req("100"), _req("200", period=1), _req("200", 121))), _pf("1000"), C
    )
    assert out.place == ()
    assert len(rej) == 3


def test_sequential_capital_budget() -> None:
    out, rej = enforce(Decision((_req("800"), _req("800"))), _pf("1000"), C)
    assert [p.amount for p in out.place] == [D(800), D(200)]
    assert rej == []


def test_rate_type_consistency() -> None:
    bad_fixed = OfferRequest(RateType.FIXED, None, 2, D(200))
    bad_frr = OfferRequest(RateType.FRR, D("0.0002"), 2, D(200))
    neg = _req("200", rate="-0.0001")
    out, rej = enforce(Decision((bad_fixed, bad_frr, neg)), _pf("1000"), C)
    assert out.place == () and len(rej) == 3


def test_cancel_releases_capital_and_unknown_ignored() -> None:
    offer = OfferView("o1", "fUST", RateType.FIXED, D("0.0002"), 2, D(400), D(400), FIXED_NOW)
    out, rej = enforce(Decision((_req("500"),), ("o1", "nope", "o1")), _pf("100", (offer,)), C)
    assert out.cancel == ("o1",)
    assert out.place[0].amount == D(500)
    assert [r.reason for r in rej] == ["cancel of unknown offer nope"]


def test_max_offer_amount() -> None:
    c = FundingConstraints(max_offer_amount=D(300))
    out, _ = enforce(Decision((_req("1000"),)), _pf("1000"), c)
    assert out.place[0].amount == D(300)


def test_split_amount() -> None:
    assert split_amount(D(1000), D(300), D(150)) == [D(300), D(300), D(400)]
    assert split_amount(D(1000), None, D(150)) == [D(1000)]
    assert split_amount(D(0), D(300), D(150)) == []
    assert sum(split_amount(D("1234.5"), D(200), D(150))) == D("1234.5")
