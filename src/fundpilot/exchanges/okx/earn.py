"""OKX Simple Earn Flexible public rates.

Verified against https://www.okx.com/docs-v5/en/ ("Public borrow info" / "Public borrow history",
fetched 2026-11-04) and live responses:

* ``GET /api/v5/finance/savings/lending-rate-summary?ccy=`` -> ``avgRate`` (24 h average annual
  *borrowing* rate), ``preRate`` (last annual borrowing rate), ``estRate`` (next estimate).
* ``GET /api/v5/finance/savings/lending-rate-history?ccy=&after=&before=&limit=`` -> hourly
  ``rate`` (annual *borrowing* rate) and ``lendingRate`` (annual *lending* rate), ``ts`` ms;
  max 100 per page, records since 2021-12-14; ``amt`` is deprecated.
* Public endpoints: 6 requests/second per IP.

Inference (not stated by OKX): ``lendingRate`` is net of OKX's 15 % share. The FAQ gives
"hourly return = lent amount x APR / 365 / 24 x 85 %", and the observed lendingRate/rate ratio
had a median of 0.85 over 2026-08-05 to 2026-11-04.

Simple Earn **Fixed** has no API: its endpoints went offline on 2025-03-03 (docs change log).
"""

from __future__ import annotations

import time
from collections.abc import Callable
from datetime import datetime
from decimal import Decimal
from typing import Any

import httpx
from pydantic import BaseModel, ConfigDict

from fundpilot.core.models import DataOrigin
from fundpilot.core.time import from_ms, to_ms
from fundpilot.exchanges.errors import ExchangeAPIError, ResponseParseError
from fundpilot.exchanges.ratelimit import TokenBucket

BASE_URL = "https://www.okx.com"
HISTORY_PATH = "/api/v5/finance/savings/lending-rate-history"
SUMMARY_PATH = "/api/v5/finance/savings/lending-rate-summary"
PAGE_MAX = 100


class EarnRate(BaseModel):
    """One hourly Simple Earn Flexible observation (annual fractions)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    venue: str = "okx"
    product: str = "simple_earn_flexible"
    ccy: str
    observed_at: datetime
    borrow_rate: Decimal | None
    lending_rate: Decimal | None  # what lenders earn (believed net of OKX's 15 %)
    origin: DataOrigin = DataOrigin.EXCHANGE_API


class EarnSummary(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    ccy: str
    avg_borrow_rate_24h: Decimal | None
    last_borrow_rate: Decimal | None
    next_borrow_rate_est: Decimal | None


def _dec(v: Any) -> Decimal | None:
    if v in (None, ""):
        return None
    try:
        return Decimal(str(v))
    except ArithmeticError as exc:
        raise ResponseParseError(f"invalid number {v!r}") from exc


def _data(body: Any) -> list[dict[str, Any]]:
    if not isinstance(body, dict) or "code" not in body:
        raise ResponseParseError(f"unexpected OKX response: {str(body)[:200]}")
    if str(body["code"]) != "0":
        raise ExchangeAPIError(f"OKX error {body.get('code')}: {body.get('msg')}")
    data = body.get("data")
    if not isinstance(data, list):
        raise ResponseParseError("OKX response has no data list")
    return data


def parse_history(body: Any) -> list[EarnRate]:
    return [
        EarnRate(
            ccy=str(r["ccy"]),
            observed_at=from_ms(int(r["ts"])),
            borrow_rate=_dec(r.get("rate")),
            lending_rate=_dec(r.get("lendingRate")),
        )
        for r in _data(body)
    ]


def parse_summary(body: Any) -> list[EarnSummary]:
    return [
        EarnSummary(
            ccy=str(r["ccy"]),
            avg_borrow_rate_24h=_dec(r.get("avgRate")),
            last_borrow_rate=_dec(r.get("preRate")),
            next_borrow_rate_est=_dec(r.get("estRate")),
        )
        for r in _data(body)
    ]


class OkxEarnClient:
    def __init__(
        self,
        base_url: str = BASE_URL,
        *,
        timeout: float = 20.0,
        transport: httpx.BaseTransport | None = None,
        limiter: TokenBucket | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        # OKX rejects some default library user agents with HTTP 403 (observed 2026-11-04).
        self._client = httpx.Client(
            base_url=base_url,
            timeout=timeout,
            transport=transport,
            headers={"User-Agent": "fundpilot/0.1 (+research)", "Accept": "application/json"},
        )
        self._limiter = limiter or TokenBucket(180, burst=3, sleep=sleep)  # 3/s (< 6/s)

    def close(self) -> None:
        self._client.close()

    def _get(self, path: str, params: dict[str, Any]) -> Any:
        self._limiter.acquire()
        resp = self._client.get(path, params={k: v for k, v in params.items() if v is not None})
        if resp.status_code >= 400:
            raise ExchangeAPIError(f"OKX HTTP {resp.status_code} on {path}")
        return resp.json()

    def summary(self, ccy: str) -> EarnSummary:
        rows = parse_summary(self._get(SUMMARY_PATH, {"ccy": ccy}))
        if not rows:
            raise ResponseParseError(f"no summary for {ccy}")
        return rows[0]

    def history(
        self, ccy: str, *, after: datetime | None = None, limit: int = PAGE_MAX
    ) -> list[EarnRate]:
        """Newest first; ``after`` returns records *earlier* than that time (OKX semantics)."""
        return parse_history(
            self._get(
                HISTORY_PATH,
                {
                    "ccy": ccy,
                    "limit": str(limit),
                    "after": None if after is None else str(to_ms(after)),
                },
            )
        )

    def backfill(self, ccy: str, start: datetime, max_pages: int = 2000) -> list[EarnRate]:
        out: dict[datetime, EarnRate] = {}
        cursor: datetime | None = None
        for _ in range(max_pages):
            page = self.history(ccy, after=cursor)
            if not page:
                break
            for r in page:
                if r.observed_at >= start:
                    out[r.observed_at] = r
            oldest = min(r.observed_at for r in page)
            if oldest < start or (cursor is not None and oldest >= cursor):
                break
            cursor = oldest
        return [out[t] for t in sorted(out)]
