# ADR 0009: GitHub automation — CI, tag-driven releases, and Pages

Status: accepted

Context: releases were previously fully manual: `scripts/package_release.sh`
was run locally by the owner, the zip was uploaded by hand, and no automated
checks ran on pull requests. The repo has a FastAPI backend (pytest, ruff),
a Vite/React/TS frontend (vitest, `npm run build`), and a release script that
self-verifies that the pinned upstream commit is fetchable from GitHub before
baking it into `requirements.lock`.

Decision:

- **CI on every PR and push to main** (`.github/workflows/ci.yml`): two
  parallel jobs with cancel-in-progress concurrency.
  - Backend: install with dev extras (`pip install -e "server[dev]"` — the
    extras already exist in `server/pyproject.toml`), `ruff check server`,
    `ruff format --check server`, then `pytest tests -q` in `server/`.
  - Frontend: Node 22 (Vite 7 requires >= 20.19 or >= 22.12), `npm ci`,
    `npm test`, `npm run build`.
- **Tag-driven releases** (`.github/workflows/release.yml`): pushing a tag
  `v*` runs the existing packaging script in CI. The job uses full-depth
  checkout, resolves `TRADINGAGENTS_PIN_SHA` to the fork's `main` HEAD via
  `git ls-remote` at build time, and lets the script self-verify
  fetchability. The zip plus a `sha256.txt` are attached to a GitHub release
  with auto-generated notes and a "Built against TauricResearch fork pin
  `<sha>`" line in the body.
- **Pages for the SPA** (`.github/workflows/pages.yml`): pushes to main
  touching `frontend/**` or `docs/**` (or manual `workflow_dispatch`) build
  the SPA, add a `404.html` SPA fallback (copy of `index.html`), upload via
  `actions/upload-pages-artifact@v3` and deploy via `actions/deploy-pages@v4`
  under the `github-pages` environment.
- **Environment gate on releases**: the release `publish` job targets the
  `release` environment; the owner configures it in the UI with required
  reviewers (and optionally restricts deployment tags to `v*`). The YAML
  only references the environment by name — the gate itself lives in repo
  settings.
- **Supply-chain stance**: first-party actions are pinned by major tag
  (`checkout@v4`, `setup-python@v5`, `setup-node@v4`,
  `upload-pages-artifact@v3`, `deploy-pages@v4`), as is
  `softprops/action-gh-release@v2`. Full-SHA pinning is stricter but costs
  more maintenance; major-tag pinning matches a small repo's needs.

Phasing:

- **Phase 1 (this ADR):** CI, release, and Pages workflows land; the owner
  applies the UI gates (branch protection, environment reviewers, Pages
  toggle).
- **Phase 2:** Pages goes live, plus Cloudflare PR previews (separate
  decision).
- **Phase 3:** upstream-drift radar in the fork (separate decision).

Consequences:

- Branch protection (require PR, required CI check on main) is set manually
  by the owner in the UI; workflows alone cannot enforce it.
- `GITHUB_TOKEN` permissions stay minimal per job: `contents: read` for CI,
  `contents: write` on the release job only, and `pages: write` +
  `id-token: write` on the Pages deploy job only. No secrets are stored; the
  release script itself unsets token/key env vars before building.
- The release zip's upstream pin floats to fork `main` at tag time. This is
  intentional: a tag should build against the best current fork state, and
  the script refuses to bake an unfetchable pin.
- Known follow-up (not a blocker): `frontend/vite.config.ts` has no `base`,
  so on project pages (`<user>.github.io/TradingAgents-webGUI/`) asset URLs
  may need `base: './'` (or the repo subpath) before Pages is fully usable;
  the release zip is unaffected (served by the local server, not Pages).
