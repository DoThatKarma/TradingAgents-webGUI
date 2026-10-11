# GHCR image workflow — design notes

Companion document for `.github/workflows/docker.yml` (ADR 0010). It records
what the workflow does, why, and what the owner must do once before the first
push works end-to-end.

## What it builds

The **server image** from `deploy/Dockerfile` — a self-contained, multi-stage
build (SPA compiled in stage 1, `pip install server/` + bundled dist in stage
2). No prebuilt `frontend/dist` is required: the image build is reproducible
from a clean checkout, which is exactly what CI must guarantee.

The upstream research stack (Tauric Research `tradingagents`) is **not** part
of this image on purpose: the WebGUI backend talks to a configured LLM/market
-data provider set, and ADR 0006 keeps the released server a small, auditable
unit. Pinning/upgrading the upstream research library stays a release-script
concern (`requirements.lock`), not an image concern.

## Triggers and tags

| Event | Tags produced | Rationale |
| --- | --- | --- |
| Push tag `v1.2.3` | `1.2.3`, `1.2`, `latest` | Semver pinning for audits; moving minor tag for convenience; `latest` is what `docker compose pull server` upgrades to (deploy/docker-compose.yml). |
| Manual `workflow_dispatch` | `sha-<shortsha>` only | Testable traceable build; **never** moves `latest`, so experiments cannot leak into the VPS upgrade path. |

`docker/metadata-action@v5` computes the tags; the image name is lowercased
from `GITHUB_REPOSITORY_OWNER` because GHCR paths must be lowercase while the
repo owner is `DoThatKarma`.

## Platform and cache

- **`linux/amd64` only.** The stated target is a conventional Ubuntu LTS VPS;
  multi-arch would double build time for no user. Revisit only if an ARM VPS
  or Raspberry-Pi deployment becomes a real request.
- **GitHub Actions cache** (`type=gha,mode=max`): layer reuse across builds —
  the expensive npm/pip layers survive until the lockfiles change.

## Permissions and security posture

- Workflow-level `permissions:` are minimal: `contents: read`,
  `packages: write`. Nothing else — consistent with ADR 0009's stance.
- Auth uses the workflow-scoped `GITHUB_TOKEN` (no PAT stored).
- `concurrency` cancels superseded builds on the same ref.
- Build context is filtered by `deploy/Dockerfile.dockerignore` (whitelist),
  so secrets such as `deploy/.env` never enter the build context.

## First-run checklist (owner, once)

1. Push a tag (`git tag v0.1.5 && git push origin v0.1.5`) — or run the
   workflow manually to smoke-test the build first.
2. Open **GitHub → your profile → Packages**; the new package
   `tradingagents-webgui-server` appears.
3. In the package settings, link it to this repository (if not auto-linked)
   and set visibility **public** so VPS `docker compose pull` works without
   a registry login. (Private also works — then add `docker login ghcr.io` on
   the VPS with a PAT with `read:packages`.)
4. On the VPS: `docker compose pull server && docker compose up -d`.

## Relationship to other workflows

- `ci.yml` gates merges; `docker.yml` does not re-run the test suite — it
  trusts the tag was cut on a green main (matching the release chain's
  environment-gate philosophy from ADR 0009).
- `release.yml` (zip packaging) and `docker.yml` (image) are independent
  consumers of the same `v*` tag; both run on tag push.
