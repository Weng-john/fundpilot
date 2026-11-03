"""Application settings loaded from environment variables (prefix ``FUNDPILOT_``).

There are no exchange credential fields: market data is public and live execution is not
implemented. The only secret is an optional LLM provider key for the supervisory agent.
"""

from __future__ import annotations

from decimal import Decimal
from enum import StrEnum
from functools import lru_cache

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class ExecutionMode(StrEnum):
    DRY_RUN = "dry_run"
    PAPER = "paper"
    LIVE = "live"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="FUNDPILOT_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # -- storage ------------------------------------------------------
    database_url: str = "sqlite:///./data/fundpilot.sqlite"

    # -- execution safety (Phase 1: only dry_run is meaningful) -------
    execution_mode: ExecutionMode = ExecutionMode.DRY_RUN

    # -- Bitfinex public API -------------------------------------------
    bitfinex_public_rest_url: str = "https://api-pub.bitfinex.com/v2"
    bitfinex_timeout_seconds: float = 15.0
    bitfinex_max_retries: int = Field(default=3, ge=0, le=10)
    # Official docs (per search summary) state 10-90 req/min depending on endpoint.
    # We default to the conservative lower region; tune per deployment.
    bitfinex_requests_per_minute: int = Field(default=15, ge=1, le=90)
    bitfinex_rate_limit_cooldown_seconds: float = 60.0
    # UNVERIFIED in this session: Bitfinex funding stats FRR has been documented as
    # "1/365th" of FRR, i.e. multiply by 365 to obtain the daily rate. The raw value
    # is always stored as well so this can be corrected retroactively.
    bitfinex_funding_stats_frr_scale: Decimal = Decimal(365)
    # UNVERIFIED: aggregated funding candle suffix (e.g. "a30:p2:p30") or "p30".
    bitfinex_candle_period_spec: str = "a30:p2:p30"

    # -- collection ----------------------------------------------------
    symbols: list[str] = Field(default_factory=lambda: ["fUST"])
    book_precision: str = "P0"
    book_length: int = 100
    raw_book_length: int = 100
    trades_limit: int = 1000
    collect_interval_seconds: int = Field(default=60, ge=10)

    # -- data quality --------------------------------------------------
    max_plausible_daily_rate: Decimal = Decimal("0.07")
    min_funding_period_days: int = 2
    max_funding_period_days: int = 120
    staleness_threshold_seconds: int = 300

    # -- optional OpenAI-compatible LLM for the supervisory agent -------
    # Leave unset to run fully deterministic (no LLM). This key is for the LLM provider only;
    # FundPilot has no exchange credential settings.
    llm_base_url: str | None = None
    llm_model: str | None = None
    llm_api_key: SecretStr | None = None

    # -- dashboard API --------------------------------------------------
    # Write endpoints (only "emergency stop") are disabled unless a token is set.
    api_token: SecretStr | None = None
    api_reports_root: str = "experiments/outputs"
    api_models_root: str = "models"

    # -- retention (days; 0 = keep forever) -----------------------------
    retention_book_snapshots_days: int = 90
    retention_tickers_days: int = 0
    retention_trades_days: int = 0
    retention_quality_issues_days: int = 365


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
