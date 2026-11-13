# Dependabot

`tools/gen-guardrails.py <repo> --dependabot-only` writes each repository's `.github/dependabot.yml`:

- **at most one pull request per repository per month**: a single multi-ecosystem group (language packages, Docker, Actions together), schedule monthly, 7-day cooldown;
- **major versions are ignored** everywhere, and Docker base images (Go, Node, Python, Temurin, ...) ignore **minor** versions as well: a toolchain or major upgrade is a deliberate decision (a card on the project board), not a bot PR;
- the rules are **in the file**, not in Dependabot's per-repository memory (`@dependabot ignore ...`), which a repository transfer wipes (that is what flooded the organisation with ~25 PRs after the move);
- Dependabot **security** alerts and security updates are a separate mechanism, enabled per repository by `tools/transfer-repo.sh` / `tools/protect-repo.sh`.

To take a major upgrade deliberately: raise it by hand (or temporarily remove the ignore), test, merge.

## The shape of the file

```yaml
version: 2
multi-ecosystem-groups:
  dependencies:                 # the one group; its schedule is the only schedule
    schedule:
      interval: monthly
    commit-message:             # on the GROUP (see below)
      prefix: "chore(deps)"
updates:
  - package-ecosystem: gomod    # one block per ecosystem of the repository, then github-actions
    directory: /                # nuget: directories ["/src", "/tests"]
    multi-ecosystem-group: dependencies
    patterns: ["*"]
    cooldown: { default-days: 7 }
    ignore:
      - dependency-name: "*"
        update-types: ["version-update:semver-major"]   # docker, docker-compose: also ...-minor
```

## `commit-message` belongs to the group

GitHub documents `milestone`, `commit-message`, `target-branch` and `pull-request-branch-name` as **group-only options**; setting one on an
update that belongs to a multi-ecosystem group is a configuration error (GitHub Docs, "Configuring multi-ecosystem updates for Dependabot",
section *Configuration merging behavior*). The generator used to write `commit-message` on every update, GitHub rejected the whole file
("`commit-message` should only be set on the associated multi-ecosystem group, not directly on updates that are part of a multi-ecosystem
update group") and the monthly/majors-ignored policy never took effect. The generator now writes it once, on the group.

`labels` and `assignees` would be additive (group and ecosystem level are merged); the generator sets neither. `patterns` is required on every
grouped update (same page, *Troubleshooting*) and a grouped update carries no schedule of its own (the group's applies); `tools/test-generators.py`
checks these rules on every generated file.

## Regenerate

```
tools/gen-guardrails.py <repo-dir> --dependabot-only            # keeps the ecosystems of the existing file
tools/gen-guardrails.py <repo-dir> --dependabot-only gomod,docker   # a repository without the file (a new service)
```

Ecosystems: `gomod npm pip gradle nuget docker docker-compose`; `github-actions` is always added.

## What is checked, and what is not

- `tools/test-generators.py` (run by this repository's CI): the rules above and the policy for several ecosystem sets. With
  `DEPENDABOT_SCHEMA=<dependabot-2.0.json>` and `jsonschema` installed it also validates against the SchemaStore schema. Note that the schema
  **does not know the group-only rule**: it accepts the rejected form of 2026-10-17, so the explicit checks are what catch it.
- Not checkable offline: whether GitHub's server accepts the file. After the first push, read the check run named `.github/dependabot.yml` on the
  commit (and the repository's *Insights, Dependency graph, Dependabot* page); a second, different complaint is possible.
