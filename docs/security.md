# Security guidelines

1. **No credentials in Phase 1.** Settings contain no API key fields; tests never need them.
2. **Secrets** (later phases) come only from environment variables or a secrets manager, never
   from files in the repo. `.env` is git-ignored; `.env.example` contains no secrets.
3. **Least privilege.** Future Bitfinex keys: read + funding offer permissions only.
   **Withdrawal and transfer permissions must be disabled.** FundPilot will never call
   withdrawal/transfer endpoints, and code review must reject any change that does.
4. **Default to dry-run.** `FUNDPILOT_EXECUTION_MODE=dry_run`. Live execution does not exist yet
   and, when built, will require explicit config *and* human approval.
5. **AI components** (Phase 6) will have no direct access to execution; their outputs pass
   deterministic validation and the risk manager.
6. **No real portfolio data** in the repository, fixtures, issues or logs. Test fixtures are
   hand-written and labelled as such.
7. **Dependencies** are pinned via `uv.lock`; Dependabot proposes weekly updates.
8. **Reporting**: use GitHub private security advisories (see `SECURITY.md`).
