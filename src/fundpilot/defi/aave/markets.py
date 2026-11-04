"""Aave V3 markets and USDT/USDC reserves.

Addresses from ``bgd-labs/aave-address-book`` (branch main, ``src/AaveV3<Chain>.sol``), fetched
2026-11-04. Chain ids are the standard EIP-155 ids. Re-check the address book before relying
on these for anything beyond read-only analysis.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Market:
    chain: str
    chain_id: int
    pool: str
    data_provider: str


@dataclass(frozen=True, slots=True)
class Reserve:
    market: Market
    symbol: str  # canonical: "USDT" or "USDC"
    book_symbol: str  # symbol used in the address book (USDT0, USDCn, USDt, ...)
    underlying: str
    decimals: int
    a_token: str

    @property
    def key(self) -> str:
        return f"{self.market.chain}:{self.symbol}"


ETHEREUM = Market(
    "ethereum",
    1,
    "0x87870Bca3F3fD6335C3F4ce8392D69350B4fA4E2",
    "0x0a16f2FCC0D44FaE41cc54e079281D84A363bECD",
)
ARBITRUM = Market(
    "arbitrum",
    42161,
    "0x794a61358D6845594F94dc1DB02A252b5b4814aD",
    "0x243Aa95cAC2a25651eda86e80bEe66114413c43b",
)
BASE = Market(
    "base",
    8453,
    "0xA238Dd80C259a72e81d7e4664a9801593F98d1c5",
    "0x0F43731EB8d45A581f4a36DD74F5f358bc90C73A",
)
OPTIMISM = Market(
    "optimism",
    10,
    "0x794a61358D6845594F94dc1DB02A252b5b4814aD",
    "0x243Aa95cAC2a25651eda86e80bEe66114413c43b",
)
POLYGON = Market(
    "polygon",
    137,
    "0x794a61358D6845594F94dc1DB02A252b5b4814aD",
    "0x243Aa95cAC2a25651eda86e80bEe66114413c43b",
)
AVALANCHE = Market(
    "avalanche",
    43114,
    "0x794a61358D6845594F94dc1DB02A252b5b4814aD",
    "0x243Aa95cAC2a25651eda86e80bEe66114413c43b",
)

MARKETS = {m.chain: m for m in (ETHEREUM, ARBITRUM, BASE, OPTIMISM, POLYGON, AVALANCHE)}

RESERVES: tuple[Reserve, ...] = (
    Reserve(
        ETHEREUM,
        "USDC",
        "USDC",
        "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48",
        6,
        "0x98C23E9d8f34FEFb1B7BD6a91B7FF122F4e16F5c",
    ),
    Reserve(
        ETHEREUM,
        "USDT",
        "USDT",
        "0xdAC17F958D2ee523a2206206994597C13D831ec7",
        6,
        "0x23878914EFE38d27C4D67Ab83ed1b93A74D4086a",
    ),
    Reserve(
        ARBITRUM,
        "USDT",
        "USDT",
        "0xFd086bC7CD5C481DCC9C85ebE478A1C0b69FCbb9",
        6,
        "0x6ab707Aca953eDAeFBc4fD23bA73294241490620",
    ),
    Reserve(
        ARBITRUM,
        "USDC",
        "USDCn",
        "0xaf88d065e77c8cC2239327C5EDb3A432268e5831",
        6,
        "0x724dc807b04555b71ed48a6896b6F41593b8C637",
    ),
    Reserve(
        BASE,
        "USDC",
        "USDC",
        "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913",
        6,
        "0x4e65fE4DbA92790696d040ac24Aa414708F5c0AB",
    ),
    Reserve(
        OPTIMISM,
        "USDT",
        "USDT",
        "0x94b008aA00579c1307B0EF2c499aD98a8ce58e58",
        6,
        "0x6ab707Aca953eDAeFBc4fD23bA73294241490620",
    ),
    Reserve(
        OPTIMISM,
        "USDC",
        "USDCn",
        "0x0b2C639c533813f4Aa9D7837CAf62653d097Ff85",
        6,
        "0x38d693cE1dF5AaDF7bC62595A37D667aD57922e5",
    ),
    Reserve(
        POLYGON,
        "USDT",
        "USDT0",
        "0xc2132D05D31c914a87C6611C10748AEb04B58e8F",
        6,
        "0x6ab707Aca953eDAeFBc4fD23bA73294241490620",
    ),
    Reserve(
        POLYGON,
        "USDC",
        "USDCn",
        "0x3c499c542cEF5E3811e1192ce70d8cC03d5c3359",
        6,
        "0xA4D94019934D8333Ef880ABFFbF2FDd611C762BD",
    ),
    Reserve(
        AVALANCHE,
        "USDC",
        "USDC",
        "0xB97EF9Ef8734C71904D8002F8b6Bc66Dd9c48a6E",
        6,
        "0x625E7708f30cA75bfd92586e17077590C60eb4cD",
    ),
    Reserve(
        AVALANCHE,
        "USDT",
        "USDt",
        "0x9702230A8Ea53601f5cD2dc00fDBc13d4dF4A8c7",
        6,
        "0x6ab707Aca953eDAeFBc4fD23bA73294241490620",
    ),
)


def reserves(
    symbols: tuple[str, ...] = ("USDT", "USDC"), chains: tuple[str, ...] | None = None
) -> list[Reserve]:
    return [
        r for r in RESERVES if r.symbol in symbols and (chains is None or r.market.chain in chains)
    ]


def find(chain: str, symbol: str) -> Reserve:
    for r in RESERVES:
        if r.market.chain == chain and r.symbol == symbol:
            return r
    raise KeyError(f"no Aave V3 {symbol} reserve configured on {chain}")
