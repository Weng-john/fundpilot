from __future__ import annotations

import math
from datetime import timedelta
from decimal import Decimal

import pytest
from sqlalchemy.orm import Session, sessionmaker

from fundpilot.backtesting.feed import FeedConfig, load_feed
from fundpilot.core.indicators import VolatilitySeries, realized_volatility
from fundpilot.core.models import DataOrigin, TradingCandle
from fundpilot.data import repository
from fundpilot.data.db import session_scope
from fundpilot.exchanges.bitfinex import parsers
from fundpilot.ml.features import FeatureBuilder
from fundpilot.paper.source import VolatilityProvider
from tests.conftest import FIXED_NOW
from tests.helpers import state


def _candles(n: int, growth: float = 1.01, pair: str = "tBTCUSD") -> list[TradingCandle]:
    out, price = [], 100.0
    for i in range(n):
        price *= growth if i % 2 else 1 / growth
        out.append(
            TradingCandle(
                exchange="bitfinex",
                symbol=pair,
                timeframe="1h",
                open_time=FIXED_NOW + timedelta(hours=i),
                open=Decimal(1),
                close=Decimal(str(round(price, 6))),
                high=Decimal(1),
                low=Decimal(1),
                volume=Decimal(1),
            )
        )
    return out


def test_realized_volatility_math() -> None:
    assert math.isnan(realized_volatility([1.0, 2.0], 8760))
    assert realized_volatility([1.0, 1.0, 1.0], 8760) == 0.0
    closes = [100.0, 101.0, 100.0, 101.0]
    r = [math.log(101 / 100), math.log(100 / 101), math.log(101 / 100)]
    mean = sum(r) / 3
    expected = math.sqrt(sum((x - mean) ** 2 for x in r) / 2 * 8760)
    assert realized_volatility(closes, 8760) == pytest.approx(expected)


def test_volatility_only_after_candle_close() -> None:
    series = VolatilitySeries(_candles(30))
    first_close = FIXED_NOW + timedelta(hours=1)
    assert math.isnan(series.at(first_close - timedelta(seconds=1)))  # no closed candle yet
    # candle opened at +2h closes at +3h: before that only two closes exist -> nan
    assert math.isnan(series.at(FIXED_NOW + timedelta(hours=2, minutes=59)))
    assert series.at(FIXED_NOW + timedelta(hours=3)) > 0
    # changing a still-open candle must not change the value
    altered = _candles(30)
    altered[20] = altered[20].model_copy(update={"close": Decimal(1)})
    t = FIXED_NOW + timedelta(hours=20, minutes=30)  # candle 20 still open
    assert VolatilitySeries(altered).at(t) == series.at(t)


def test_parser_and_features() -> None:
    rows = parsers.parse_trading_candles(
        [[1761955200000, 100, 101, 102, 99, 5]], symbol="tBTCUSD", timeframe="1h"
    )
    assert rows[0].close == Decimal(101)
    s = state(FIXED_NOW)
    feats = dict(zip(FeatureBuilder.names(), FeatureBuilder().update(s), strict=True))
    assert math.isnan(feats["btc_rv_24h"])
    s2 = state(FIXED_NOW)
    s2 = type(s2)(
        s2.as_of,
        s2.symbol,
        s2.ticker,
        s2.book,
        s2.trades,
        False,
        {"btc_rv_24h": 0.5, "eth_rv_24h": 0.7},
    )
    feats = dict(zip(FeatureBuilder.names(), FeatureBuilder().update(s2), strict=True))
    assert feats["btc_rv_24h"] == 0.5 and feats["eth_rv_24h"] == 0.7


def test_feed_attaches_point_in_time_volatility(sessions: sessionmaker[Session]) -> None:
    with session_scope(sessions) as s:
        repository.save_trading_candles(s, _candles(48) + _candles(48, 1.02, "tETHUSD"))
        assert repository.save_trading_candles(s, _candles(48)) == 0  # idempotent
    cfg = FeedConfig(
        exchange="bitfinex",
        symbol="fUST",
        start=FIXED_NOW + timedelta(hours=30),
        end=FIXED_NOW + timedelta(hours=31),
        step=timedelta(minutes=30),
        origins=(DataOrigin.EXCHANGE_API,),
    )
    with session_scope(sessions) as s:
        feed = load_feed(s, cfg)
    st = feed.state_at(FIXED_NOW + timedelta(hours=30))
    assert st.indicators["eth_rv_24h"] > st.indicators["btc_rv_24h"] > 0


def test_provider_refreshes_and_caches() -> None:
    calls: list[str] = []

    def fetch(pair: str) -> list[TradingCandle]:
        calls.append(pair)
        return _candles(30, pair=pair)

    vp = VolatilityProvider(fetch, refresh=timedelta(minutes=30))
    t = FIXED_NOW + timedelta(hours=29)
    first = vp(t)
    vp(t + timedelta(minutes=10))
    assert len(calls) == 2  # cached
    vp(t + timedelta(minutes=40))
    assert len(calls) == 4
    assert set(first) == {"btc_rv_24h", "eth_rv_24h"}
