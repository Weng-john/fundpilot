# Contributing

1. `uv sync`, then work on a feature branch.
2. Conventional commits: `feat:`, `fix:`, `docs:`, `test:`, `refactor:`, `chore:`, `ci:`.
3. Before opening a PR: `ruff check`, `ruff format --check`, `mypy src tests`, `pytest`.
4. Exchange behaviour must cite an official source or be marked **unverified** in
   `docs/bitfinex-integration.md`.
5. Never commit secrets, API keys, account data or real portfolio data. Test fixtures must be
   hand-written and labelled as such.
6. Financial values: `Decimal` only. Timestamps: aware UTC only.
7. Do not present simulated or forecast numbers as realised performance.
