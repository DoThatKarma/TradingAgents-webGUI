# ADR 0004: License — GUI repository

Status: **Accepted — supersedes the earlier Apache-2.0 recommendation (owner-confirmed 2026-09-13)**

## Context

- The repo was created with GitHub-default GPL-3.0 (the LICENSE file is the verbatim canonical FSF text).
- The earlier draft ADR recommended Apache-2.0 for consistency with upstream. The owner has since defined
  binding requirements that Apache-2.0 cannot satisfy:
  free use (R1); paid hosted-service offering allowed (R2); mandatory credit to **both** DoThatKarma and
  TauricResearch (R3); substantial derivatives must be shared on the same terms (R4); patent grant,
  warranty/liability limits and trademark non-grant (R5).
- Apache-2.0 has no copyleft, so it fails R4 outright and imposes no attribution duty for hosted unmodified
  instances (R3). A custom "Apache+copyleft" hybrid was rejected: no OSI approval, no legal review, no case
  law, contributor chill (see `decision-memo.md` §4).
- The upstream `tradingagents` fork and its plugin SDK remain **Apache-2.0 unchanged**; the GUI consumes
  upstream as a pip dependency and only `server/app/instructions/adapters/` touches `ta_plugins`, so a clean
  upstream contribution PR stays possible.

## Decision (recommended)

License the gui-repo as **AGPL-3.0-only**, using the verbatim canonical FSF text, with a `NOTICE` file
crediting DoThatKarma / TradingAgents-webGUI and TauricResearch / TradingAgents (Apache-2.0).

Rationale: AGPL-3.0 is the only standard OSI license meeting R1–R4 fully; its §13 extends share-alike to
network servers, matching the owner's intent for a web app; §2 explicitly permits charging, so paid hosting
stays legal; §§10–11 and 15–16 carry the patent grant, patent retaliation and warranty/liability limits;
attribution flows through §§4, 5 and 7 (notice retention, no origin misrepresentation). R5's explicit
trademark clause is the only partial — coverable by a short standalone trademark policy, not by editing the
license. Honest trade-off accepted by the owner's R4: any competitor may legally run the same code as a
service; differentiation comes from product quality, brand and support.

## Consequences

- Modified network servers must offer their Corresponding Source to users (§13).
- GPL-3.0-only remains the fallback if network copyleft is judged too strict (then R4 binds distribution
  only).
- No change to SDK/upstream licensing; upstream Apache attribution preserved via NOTICE.
- Repo files need no bulk relicense: the existing LICENSE is simply replaced by the canonical AGPL text and
  the README gains a "License & credits" section.

## Alternatives rejected

- Apache-2.0: fails R4 (no copyleft) and R3 for hosted unmodified copies.
- GPL-3.0-only: fails R4 for modified network serving.
- Custom hybrid: non-OSI, unvetted, incompatible-feeling, zero precedent.
- Dual AGPL-3.0-or-commercial: viable later (requires CLA and commercial terms); not needed for stated intent.

## Implementation checklist (owner, after confirmation)

1. Replace `LICENSE` with the canonical AGPL-3.0 text (`LICENSE-proposed`).
2. Add `NOTICE` from `NOTICE-proposed`.
3. Insert `README-license-section.md` content into README (donation line stays untouched in intro).
4. Add a copyright header/footer convention to main entry files (Appendix wording of AGPL-3.0).
5. Ensure `scripts/package_release.sh` ships LICENSE + NOTICE in dist-release zips.
6. Optional: standalone trademark policy file closing the R5 gap.
