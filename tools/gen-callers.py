#!/usr/bin/env python3
"""Write the two caller files of an app repository: .github/workflows/ci.yaml (pull requests -> service-ci-<language>.yaml, check `ci / gate`,
then the `merge` job: the owner approves the `merge` environment on the run page and the pull request is squash-merged) and
publish.yaml (push to main, or a manual/merge-button run with `before` -> service-publish.yaml). Removes the old ci-*.yaml / release.yaml.
usage: gen-callers.py <repo-dir> <service> <wf-sha> <wf-ver>
<service> is the app name = repository name without the `app-` prefix = helm chart name = Docker Hub repository.

Per service, CFG may carry `publish` (default: True, except for lang "none"). publish=False writes ONLY ci.yaml (the pull-request gate and
the merge button, whose `start-publish` is then false) and no publish.yaml: Publish opens a dev pull request in platform-demo-helm, which
needs a chart and a line in envs/*/workloads.yaml first (APPLICATION-STANDARDS CI-03). Flip the flag when the chart exists."""
import os, sys

repo_dir, svc, sha, ver = sys.argv[1:5]
WF = "platform-boutique/platform-demo-workflows/.github/workflows"
GO_CHECKOUT_ENV = ["PRODUCT_CATALOG_SERVICE_ADDR=localhost:3550", "CART_SERVICE_ADDR=localhost:7070",
    "CURRENCY_SERVICE_ADDR=localhost:7000", "SHIPPING_SERVICE_ADDR=localhost:50051",
    "PAYMENT_SERVICE_ADDR=localhost:50051", "EMAIL_SERVICE_ADDR=localhost:8080"]

CFG = {
  "checkout-service": dict(lang="go", with_={"test-packages": "./internal/...", "e2e-packages": "./tests/e2e/...", "smoke-port": '"5050"', "smoke-env": ("|", GO_CHECKOUT_ENV)}, trivy=""),
  "shipping-service": dict(lang="go", with_={"test-packages": "./internal/...", "smoke-port": '"50051"'}, trivy=".trivyignore"),
  "frontend": dict(lang="go", with_={}, trivy=""),
  "product-catalog-service": dict(lang="go", with_={"test-packages": "./internal/..."}, trivy=""),
  "currency-service": dict(lang="node", with_={"smoke-port": '"7000"', "smoke-script": ".github/smoke.sh"}, trivy=".trivyignore"),
  "payment-service": dict(lang="node", with_={"smoke-port": '"50051"'}, trivy=".trivyignore"),
  "email-service": dict(lang="python", with_={}, trivy=".trivyignore"),
  "recommendation-service": dict(lang="python", with_={}, trivy=".trivyignore"),
  "ad-service": dict(lang="java", with_={}, trivy=""),
  "product-catalog-db": dict(lang="none", with_={}, trivy=""),
  "cart-service": dict(lang="dotnet", with_={"solution": "cartservice.sln", "project": "src/cartservice.csproj", "test-project": "tests/cartservice.tests.csproj"}, trivy=""),
  # The five services below have NO helm chart yet (deferred): they get the pull-request caller only. When the chart and the lines in
  # platform-demo-helm envs/{dev,stage,prod}/workloads.yaml exist, delete `publish=False` and regenerate: publish.yaml appears and the
  # merge button starts it. They are Go gRPC + Postgres (build-path default ./cmd/server/...; the integration tests carry the build tag
  # `integration`, so the default test-packages ./... does not run them and needs no database) or Python/Locust. No smoke test: the
  # server needs Postgres at start-up.
  "review-service": dict(lang="go", with_={}, trivy="", publish=False),
  "order-service": dict(lang="go", with_={}, trivy="", publish=False),
  "user-service": dict(lang="go", with_={}, trivy="", publish=False),
  "inventory-service": dict(lang="go", with_={}, trivy="", publish=False),
  # no inbound API; the entry point is src/loadgen/__main__.py, not the default src/server.py
  "load-generator": dict(lang="python", with_={"build-check-file": "src/loadgen/__main__.py"}, trivy="", publish=False),
}
if svc not in CFG:
    sys.exit(f"gen-callers: unknown service {svc!r}; add it to CFG (known: {', '.join(sorted(CFG))})")
CFG = CFG[svc]
# Publish needs an image: a repository without an image pipeline (lang none) cannot publish. Both settings must agree.
PUBLISH = CFG.get("publish", CFG["lang"] != "none")
if not isinstance(PUBLISH, bool):
    sys.exit(f"gen-callers: {svc}: publish must be True or False, got {PUBLISH!r}")
if PUBLISH and CFG["lang"] == "none":
    sys.exit(f"gen-callers: {svc}: publish=True contradicts lang=\"none\" (no image pipeline, nothing to publish); drop publish or set a language")


# inputs of service-ci-<language> (see its `on.workflow_call.inputs`); the keys in CFG["with_"] are passed through unchanged
def with_block(d, indent="      "):
    if not d:
        return ""  # an empty `with:` is a syntax error for actionlint
    out = "    with:\n"
    for k, v in d.items():
        if isinstance(v, tuple):
            out += f"{indent}{k}: {v[0]}\n" + "".join(f"{indent}  {l}\n" for l in v[1])
        else:
            out += f"{indent}{k}: {v}\n"
    return out

ci_inputs = dict(CFG["with_"])
if CFG["lang"] == "none":
    ci_inputs["image"] = "false"  # no image pipeline for this repository
if CFG["trivy"]:
    ci_inputs["trivyignores"] = CFG["trivy"]

# The service-ci-<language> workflows are flat (no nested workflow), so GitHub validates only their own jobs: every caller grants
# contents: read + packages: write (the image job reads the build cache and pushes ghcr.io/<repo>:tree-<hash> for a run that may; a
# fork's token is read-only whatever is asked); only the dotnet file has a job (the test reporter) that needs checks: write.
ci_perms = ("      contents: read\n"
            "      packages: write # the image job pushes ghcr.io/<repo>:tree-<hash> (same-repository runs; a fork's token stays read-only)\n")
if CFG["lang"] == "dotnet":
    ci_perms += "      checks: write # the test reporter publishes a check run\n"

# The merge button is service-merge.yaml (defined once, like the rest). The caller keeps the `if:` (only same-repository pull requests that
# are not Dependabot's: those have a read-only token) and grants what the called job needs; a repository without a Publish caller has
# none to start.
MERGE_WITH = ""
if CFG["lang"] == "none":
    MERGE_WITH = "    with:\n      start-publish: false # no image pipeline, hence no Publish\n"
elif not PUBLISH:
    MERGE_WITH = "    with:\n      start-publish: false # no publish.yaml yet (no helm chart): set publish=True in tools/gen-callers.py when it exists\n"

CI = f"""name: CI

# Pull-request gate: guards (change detection, secrets, workflows) start at t=0; when code changed, lint, build, tests, SCA, SAST
# and an image build + CVE scan run in parallel with them (the image is pushed as ghcr.io/<repo>:tree-<hash> and reused by Publish
# after the merge). The only required status check is `ci / gate`; `merge` comes after it and is not part of it.
on:
  pull_request:
    branches: [main]
  workflow_dispatch:

permissions: {{}}

concurrency:
  group: ci-${{{{ github.ref }}}}
  cancel-in-progress: true

jobs:
  ci:
    permissions:
{ci_perms}    uses: {WF}/service-ci-{CFG['lang']}.yaml@{sha} # {ver}
{with_block(ci_inputs)}
  # The merge button: waits for you to approve the environment `merge` on the run page (Review deployments), then squash-merges the
  # tested pull request and starts Publish. Not for forks or Dependabot (read-only token): use the normal Merge button there.
  merge:
    needs: ci
    if: ${{{{ github.event_name == 'pull_request' && github.event.pull_request.head.repo.full_name == github.repository && github.actor != 'dependabot[bot]' }}}}
    permissions:
      contents: write
      pull-requests: write
      actions: write # starts Publish
    uses: {WF}/service-merge.yaml@{sha} # {ver}
{MERGE_WITH}"""

pub_inputs = {"app": svc}
if CFG["trivy"]:
    pub_inputs["trivyignores"] = CFG["trivy"]
pub_inputs["before"] = "${{ inputs.before }}"

PUB = f"""name: Publish

# A merge to main is ONE pipeline in this run: the image is published ONCE (the image the pull request built is reused when main's
# tree is the same; Trivy, Docker Hub as sha-<commit>, cosign, SBOM), the pull request in platform-demo-helm moves `dev` to the new
# digest, then YOU approve stage and prod on this run's page (Review deployments: the environments `stage` and `prod` of this
# repository, required reviewer = the owner, deployment branch main) and each approval opens the next helm pull request.
# No tags in this repository, no rebuilds. The `merge` job of ci.yaml starts this workflow by hand-dispatch (a merge made with the
# workflow token fires no push trigger) and passes `before` = main's commit before the merge, so a documentation-only merge
# publishes nothing. Started by hand without `before`, it publishes.
#
# There is deliberately NO workflow-level `concurrency` here: a run may wait days for a click, and a group on the whole workflow would
# queue every later merge behind it. The jobs that must not overlap carry their own groups in service-publish.yaml.
on:
  push:
    branches: [main]
  workflow_dispatch:
    inputs:
      before:
        description: Commit main was at before the change (empty = publish; set = a documentation-only change since then publishes nothing)
        type: string
        required: false
        default: ""

permissions: {{}}

jobs:
  publish:
    permissions:
      contents: read
      packages: write
      id-token: write # keyless cosign signing
      actions: read # the approval jobs read who approved the environments stage and prod of this run
    uses: {WF}/service-publish.yaml@{sha} # {ver}
{with_block(pub_inputs)}    secrets:
      DOCKERHUB_USERNAME: ${{{{ secrets.DOCKERHUB_USERNAME }}}}
      DOCKERHUB_TOKEN: ${{{{ secrets.DOCKERHUB_TOKEN }}}}
      PROMOTE_APP_ID: ${{{{ secrets.PROMOTE_APP_ID }}}}
      PROMOTE_APP_PRIVATE_KEY: ${{{{ secrets.PROMOTE_APP_PRIVATE_KEY }}}}
"""

d = os.path.join(repo_dir, ".github", "workflows")
os.makedirs(d, exist_ok=True)
for old in ("ci-feature.yaml", "ci-main.yaml", "release.yaml"):
    p = os.path.join(d, old)
    if os.path.exists(p): os.remove(p)
open(os.path.join(d, "ci.yaml"), "w").write(CI)
if PUBLISH:  # no image pipeline (product-catalog-db) or no helm chart yet (publish=False): no Publish caller
    open(os.path.join(d, "publish.yaml"), "w").write(PUB)
elif os.path.exists(os.path.join(d, "publish.yaml")):
    os.remove(os.path.join(d, "publish.yaml"))
print("wrote callers for", svc, "(ci.yaml + publish.yaml)" if PUBLISH else "(ci.yaml only: publish is off)")
