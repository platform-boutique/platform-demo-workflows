# Adding a service

What a new application repository needs so that the shared pipeline works. Everything below is read from `tools/gen-callers.py`,
`tools/gen-guardrails.py`, `tools/protect-repo.sh` and the reusable workflows of this repository; the GitHub settings themselves are not
applied by any generator, they are set by hand or by `tools/protect-repo.sh` (not run for the services below).

## 1. Generate the files

1. Add an entry to `CFG` in `tools/gen-callers.py`: `lang` (`go`, `node`, `python`, `java`, `dotnet`, `none`), `with_` (inputs of
   `service-ci-<lang>.yaml`, passed through unchanged), `trivy` (`.trivyignore` path or `""`), and `publish` (see section 3).
   The service name is the repository name without `app-` and also the helm chart / values file / Docker Hub name.
2. `tools/gen-callers.py <repo-dir> <service> <workflow-sha> <version>` writes `.github/workflows/ci.yaml` (jobs `ci` and `merge`) and, when
   `publish` is on, `publish.yaml`. An unknown service, `publish=True` with `lang="none"`, or a non-boolean `publish` stops with a one-line error.
3. `tools/gen-guardrails.py <repo-dir> <ecosystems>` writes `CODEOWNERS`, `.github/dependabot.yml` and `.pre-commit-config.yaml`
   (`--dependabot-only <ecosystems>` for the Dependabot file alone, see [Dependabot](dependabot.md)).

What the pull-request workflow expects in the repository (from the workflow files; defaults can be changed with `with_`):

| Language | Expected | Input to override |
|---|---|---|
| all | `Dockerfile` in the build context root | `context`, `dockerfile` |
| go | `go.mod`; `go build` of `./cmd/server/...`; `go test` of `./...` (a test with a build tag such as `integration` is not run) | `build-path`, `test-packages`, `e2e-packages`, `smoke-port`, `smoke-env` |
| python | `.python-version`, `requirements.txt`, `requirements-dev.txt`, `src/` (bandit), entry file `src/server.py` that must parse, `tests/`, bandit settings in `pyproject.toml` | `python-version-file`, `source-dir`, `build-check-file`, `tests-path`, `bandit-config` |

## 2. Settings of the repository on GitHub

| What | Setting | Where it comes from |
|---|---|---|
| Branch protection on `main` | pull request required (0 approvals, stale reviews dismissed), required status check **`ci / gate`** (the only one; `merge` is not part of it), admins included, linear history, no force push, no deletion, conversations resolved | `tools/protect-repo.sh <repo> '["ci / gate"]'` |
| Repository | squash merge only, branches deleted on merge, auto-merge allowed, secret scanning + push protection, Dependabot alerts and security updates, tag ruleset `protect-release-tags` (`v*` cannot be moved or deleted) | `tools/protect-repo.sh`; drift is read by `tools/check-repo-settings.sh` |
| Environment `merge` | required reviewer: the owner (the merge button; `service-merge.yaml` refuses to merge without an approval of it) | [One pipeline](one-pipeline.md), [Things to know](things-to-know.md) |
| Environments `stage`, `prod` | required reviewer: the owner, *Prevent self-review* off, deployment branches: `main`; `dev` has no click. Only `publish.yaml` uses `stage` and `prod`. Create them with the reviewer BEFORE the first Publish: a missing environment is created without protection | [One pipeline](one-pipeline.md) |
| Secrets for Publish | repository: `DOCKERHUB_USERNAME`, `DOCKERHUB_TOKEN`; organisation: `PROMOTE_APP_ID`, `PROMOTE_APP_PRIVATE_KEY` (the GitHub App scoped to `platform-demo-helm`) | `publish.yaml` passes exactly these four; the pull-request workflows use only `GITHUB_TOKEN` |

A repository that only runs pull-request CI (`publish=False`) needs branch protection, the environment `merge` and no secrets.

## 3. Publish is a separate switch: `publish` in `CFG`

`publish.yaml` ends in `Open dev PR`, which edits `envs/<env>/values/<service>.yaml` in `platform-demo-helm` and **fails if that file does
not exist** ("A promotion only changes the image of an existing values file", `gitops-promote.yaml`). A service without a chart therefore must
not get a Publish caller. In `CFG`, `publish=False` makes the generator

- write `ci.yaml` only (no `publish.yaml`; an old one in the directory is removed), and
- give the merge button `start-publish: false`, so the `merge` job squash-merges and starts nothing.

Currently `publish=False`: `review-service`, `order-service`, `user-service`, `inventory-service`, `load-generator`. When the chart and the values
files for the environments exist in `platform-demo-helm`: delete `publish=False` from the entry, run `tools/gen-callers.py` again (same
arguments), create the `stage` and `prod` environments and the secrets above, commit the two changed files. `tools/test-generators.py` and
`tools/check-permissions.py` carry a list of the services without Publish; update it in the same commit.
