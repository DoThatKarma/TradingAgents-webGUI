# Contributing

## Development flow

1. Branch from `main` using `feat/<scope>` / `fix/<scope>`
2. Follow conventional commits (`feat:`, `fix:`, `docs:`, `test:`, `chore:`)
3. All backend changes: `pytest server/tests` + `ruff check server` must pass
4. Tests are **offline-first**: no network, no API keys, no real LLM calls
5. Security-relevant changes need a security review note in the PR description

## Ground rules

- `tradingagents` is consumed as a **pip dependency** — never edit its files in this repo
- The GUI core must not import `ta_plugins` directly; only `server/app/instructions/adapters/` may
- Secrets live in `.env` (gitignored); never commit keys
