# Things to know

- **Docker Hub has no OCI referrers API.** cosign 3 defaults to the new bundle format stored as a referrer: `cosign sign` succeeds
  and `cosign verify` then finds nothing. `docker-promote` and, since v3.4.0, `service-publish` therefore sign/attest/verify with
  `--new-bundle-format=false --use-signing-config=false` (classic `sha256-<digest>.sig` tags), which Kyverno and most verifiers
  understand. The keyless identity of an image signed through a reusable workflow is
  `https://github.com/platform-boutique/platform-demo-workflows/.github/workflows/service-publish.yaml@<sha-or-ref>` since v3.4.0 (`docker-promote.yaml` before; images signed before the move to the organisation: `https://github.com/mladenovskistefan111/platform-demo-workflows/...`; both are accepted); images
  signed by the older per-repo pipelines carry `.../<service>/.github/workflows/ci-main.yaml@refs/heads/...`.
- **Staging on ghcr.io, publishing by digest:** what Trivy scanned is byte-for-byte what is published and signed; since v3.4.0 a Docker Hub digest that differs from the scanned one fails the run.
- **A job output that contains a secret is dropped by GitHub.** The Docker Hub image reference contains the account name (a secret), so `Publish image` outputs only the digest and the tag; the SBOM job rebuilds the reference.
- **A missing GitHub environment is created without protection on first use.** The `merge` job therefore asks the run for an approval of the environment `merge` and refuses to merge without one.
- **The same holds for `stage` and `prod`**: a missing environment has no reviewer, and the approval job would run at once. `Approve stage deployment` and `Approve prod deployment` therefore ask the run for an approval of their environment and fail without promoting anything (the caller grants `actions: read` for it).
- **A job that waits for an approval is in the run for up to 30 days.** The Publish run is not serialised end to end any more (no workflow-level `concurrency`), so a run waiting for a click blocks nobody; a newer build's waiting approval replaces an older one (the older run ends cancelled).
- **`gradle/actions` is held at v5**: v6 puts its caching component under proprietary terms; nobody accepted them.
- **Pins use a 7-day cooldown** (a brand-new release is a supply-chain risk). Dependabot is configured with majors as separate PRs.
- Newer **bandit** (1.9) flags `random.sample` (B311); recommendation-service uses `random.SystemRandom().sample`.
- GitHub's runner image has shellcheck; a local `actionlint` without it passes things CI later rejects.
- **Docs check (v3.7.0)**: the job `Docs check` runs `mkdocs build --strict` and `tools/check-md-links.py` only when the repository has an `mkdocs.yml` (otherwise it ends green: "nothing to check"). It also runs for a documentation-only change. mkdocs and mkdocs-techdocs-core are pinned in `tools/gen-service-ci.py` (Dependabot does not see pip commands in a workflow; the transitive dependencies are those techdocs-core pins or leaves open, there is no lock file). The link check covers relative links of README.md and the docs directory; external links and `#anchors` are not checked. The Mermaid/PlantUML plugins of TechDocs are not exercised beyond what strict build reports.
- **Coverage (v3.7.0)** is printed, never gated. A Node repository whose `test:ci` is not jest, and a Python repository without pytest-cov, print none; .NET and Java print none yet.
- **Not in v3.7.0**: `buf lint` / `buf breaking`. The repositories have no `buf.yaml`; the legacy proto package `hipstershop` lives in directories that do not match it, so a lint step needs a minimal inline config (MINIMAL without PACKAGE_DIRECTORY_MATCH) and a base fetched into a local `main` branch for `--against '.git#branch=main,subdir=proto'`. buf v1.75.0 (sha256 of buf-Linux-x86_64: fad1e38d6909bbb583a4da81d8c02a01046e2cd663bde7e337dd24277393764b) was chosen but could not be run here, so nothing was added.
