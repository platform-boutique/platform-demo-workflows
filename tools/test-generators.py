#!/usr/bin/env python3
"""Tests of the two app-repository generators, run as a user would (subprocess, scratch directories, nothing written to a real repo):

  * gen-guardrails.py --dependabot-only: the multi-ecosystem rules GitHub enforces (commit-message and the other group-only options live on
    the GROUP, never on an update that belongs to one; every grouped update has patterns and no schedule; the group exists) and the policy
    (monthly, one group, majors ignored, Docker also minors, 7-day cooldown). The checker is itself tested against the rejected form of
    2026-10-17. If a JSON schema file is given in $DEPENDABOT_SCHEMA (SchemaStore dependabot-2.0.json) and `jsonschema` is importable, the
    output is validated against it too (the schema does NOT know the group-only rule, so it cannot replace the checks above).
  * gen-callers.py: every service in CFG gets ci.yaml; publish.yaml only where `publish` is on; the merge button's start-publish is false
    exactly where there is no publish.yaml; the flag flips cleanly; contradictory settings and unknown services stop with a clear error.

Needs python3 and PyYAML.  usage: tools/test-generators.py     exit 1 and one line per failed check"""
import os, re, subprocess, sys, tempfile
import yaml

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
GUARD = os.path.join(ROOT, "tools", "gen-guardrails.py")
CALLERS = os.path.join(ROOT, "tools", "gen-callers.py")
SHA, VER = "0" * 40, "v0.0.0"
GROUP_ONLY = ("milestone", "commit-message", "target-branch", "pull-request-branch-name")
failures, count = [], 0


def check(name, ok, detail=""):
    global count
    count += 1
    if not ok:
        failures.append(f"FAIL {name} {detail}")


def run(*args):
    return subprocess.run([sys.executable, *args], capture_output=True, text=True)


# ---------------------------------------------------------------- dependabot

def dependabot_problems(doc):
    """The rules GitHub applies to multi-ecosystem groups, as far as the documentation states them (returns a list of problems)."""
    out = []
    groups = doc.get("multi-ecosystem-groups") or {}
    for i, u in enumerate(doc.get("updates", [])):
        name = f"updates[{i}] {u.get('package-ecosystem')}"
        g = u.get("multi-ecosystem-group")
        if g is None:
            continue
        if g not in groups:
            out.append(f"{name}: group {g!r} is not defined")
        for k in GROUP_ONLY:
            if k in u:
                out.append(f"{name}: {k!r} must be set on the group, not on an update that belongs to one")
        if "patterns" not in u:
            out.append(f"{name}: patterns is required with multi-ecosystem-group")
        if "schedule" in u:
            out.append(f"{name}: schedule belongs to the group")
    for gname, g in groups.items():
        if "interval" not in (g.get("schedule") or {}):
            out.append(f"group {gname}: schedule.interval is required")
    return out


def generate_dependabot(ecos):
    with tempfile.TemporaryDirectory() as tmp:
        r = run(GUARD, tmp, "--dependabot-only", ecos)
        if r.returncode != 0:
            return None, r.stderr
        text = open(os.path.join(tmp, ".github", "dependabot.yml")).read()
    return text, ""


# the form GitHub rejected (commit-message on each update): the checker must see it
REJECTED = """version: 2
multi-ecosystem-groups:
  dependencies:
    schedule:
      interval: monthly
updates:
  - package-ecosystem: gomod
    directory: /
    multi-ecosystem-group: dependencies
    patterns: ["*"]
    commit-message:
      prefix: "chore(deps)"
"""
check("checker flags commit-message on a grouped update", any("commit-message" in p for p in dependabot_problems(yaml.safe_load(REJECTED))))

schema = None
if os.environ.get("DEPENDABOT_SCHEMA"):
    try:
        import json, jsonschema
        schema = json.load(open(os.environ["DEPENDABOT_SCHEMA"]))
    except ImportError:
        print("test-generators: jsonschema not installed, schema validation skipped")

for ecos in ("gomod,docker", "npm,docker", "pip,docker", "gradle,docker", "nuget,docker", "docker", "gomod,docker,docker-compose"):
    text, err = generate_dependabot(ecos)
    check(f"dependabot[{ecos}]: generator runs", text is not None, err)
    if text is None:
        continue
    doc = yaml.safe_load(text)
    check(f"dependabot[{ecos}]: GitHub's multi-ecosystem rules", dependabot_problems(doc) == [], str(dependabot_problems(doc)))
    group = doc["multi-ecosystem-groups"]["dependencies"]
    check(f"dependabot[{ecos}]: commit-message is on the group", group.get("commit-message") == {"prefix": "chore(deps)"}, str(group))
    check(f"dependabot[{ecos}]: no update carries commit-message", all("commit-message" not in u for u in doc["updates"]))
    check(f"dependabot[{ecos}]: monthly, one group", group["schedule"] == {"interval": "monthly"} and len(doc["multi-ecosystem-groups"]) == 1)
    names = [u["package-ecosystem"] for u in doc["updates"]]
    check(f"dependabot[{ecos}]: the listed ecosystems plus github-actions, in order", names == ecos.split(",") + ["github-actions"], str(names))
    for u in doc["updates"]:
        eco = u["package-ecosystem"]
        skip = u["ignore"][0]["update-types"]
        want = ["version-update:semver-major"] + (["version-update:semver-minor"] if eco in ("docker", "docker-compose") else [])
        check(f"dependabot[{ecos}]: {eco} ignores {want}", skip == want and u["ignore"][0]["dependency-name"] == "*", str(skip))
        check(f"dependabot[{ecos}]: {eco} cooldown 7 days, patterns *", u["cooldown"] == {"default-days": 7} and u["patterns"] == ["*"])
        check(f"dependabot[{ecos}]: {eco} is in the group", u["multi-ecosystem-group"] == "dependencies")
    if "nuget" in names:
        n = [u for u in doc["updates"] if u["package-ecosystem"] == "nuget"][0]
        check("dependabot[nuget]: directories /src and /tests", n["directories"] == ["/src", "/tests"], str(n))
    if schema:
        errs = list(jsonschema.Draft7Validator(schema).iter_errors(doc))
        check(f"dependabot[{ecos}]: valid against the SchemaStore schema", not errs, "; ".join(e.message for e in errs[:3]))

# regenerating keeps the ecosystems of the existing file (the way the rollout calls it)
with tempfile.TemporaryDirectory() as tmp:
    os.makedirs(os.path.join(tmp, ".github"))
    open(os.path.join(tmp, ".github", "dependabot.yml"), "w").write(REJECTED + "  - package-ecosystem: docker\n    directory: /\n")
    r = run(GUARD, tmp, "--dependabot-only")
    doc = yaml.safe_load(open(os.path.join(tmp, ".github", "dependabot.yml")).read())
    check("dependabot: --dependabot-only keeps the existing ecosystems and repairs the file",
          r.returncode == 0 and [u["package-ecosystem"] for u in doc["updates"]] == ["gomod", "docker", "github-actions"] and dependabot_problems(doc) == [], r.stderr)

# ---------------------------------------------------------------- callers

src = open(CALLERS).read()
cfg_block = src[src.index("CFG = {"):src.index("if svc not in CFG")]
SERVICES = re.findall(r'^  "([a-z-]+)": dict\(', cfg_block, re.M)
PUBLISH_OFF = {"review-service", "order-service", "user-service", "inventory-service", "load-generator"}  # no helm chart yet
check("callers: CFG lists 16 services (15 app repositories + product-catalog-db)", len(SERVICES) == 16, str(SERVICES))
check("callers: the five new services are in CFG", PUBLISH_OFF <= set(SERVICES))


def gen(svc, script=CALLERS, workdir=None):
    tmp = workdir or tempfile.mkdtemp(prefix="test-callers-")
    r = run(script, tmp, svc, SHA, VER)
    return tmp, r


def wf(tmp, name):
    p = os.path.join(tmp, ".github", "workflows", name)
    return yaml.safe_load(open(p)) if os.path.exists(p) else None


for svc in SERVICES:
    tmp, r = gen(svc)
    check(f"callers[{svc}]: generator runs", r.returncode == 0, r.stderr)
    ci, pub = wf(tmp, "ci.yaml"), wf(tmp, "publish.yaml")
    check(f"callers[{svc}]: ci.yaml exists with the jobs ci and merge", ci is not None and sorted(ci["jobs"]) == ["ci", "merge"])
    if ci is None:
        continue
    has_pub = svc not in PUBLISH_OFF and svc != "product-catalog-db"
    check(f"callers[{svc}]: publish.yaml {'exists' if has_pub else 'is NOT written'}", (pub is not None) == has_pub)
    sp = (ci["jobs"]["merge"].get("with") or {}).get("start-publish", True)
    check(f"callers[{svc}]: merge button starts Publish exactly where publish.yaml exists", sp == has_pub, f"start-publish={sp}")

# the flag flips: remove `publish=False` of review-service in a copy of the generator -> publish.yaml appears, start-publish is back to default
with tempfile.TemporaryDirectory() as tmp:
    flipped = os.path.join(tmp, "gen-callers.py")
    text = src.replace('"review-service": dict(lang="go", with_={}, trivy="", publish=False)', '"review-service": dict(lang="go", with_={}, trivy="")')
    check("callers: flip test edits the entry", text != src)
    open(flipped, "w").write(text)
    out, r = gen("review-service", flipped)
    ci, pub = wf(out, "ci.yaml"), wf(out, "publish.yaml")
    check("callers: without publish=False review-service gets publish.yaml", r.returncode == 0 and pub is not None, r.stderr)
    check("callers: ... and the merge button starts it", ci is not None and "with" not in ci["jobs"]["merge"])
    check("callers: ... and publish.yaml calls service-publish with app: review-service",
          pub is not None and pub["jobs"]["publish"]["with"]["app"] == "review-service" and "service-publish.yaml@" in pub["jobs"]["publish"]["uses"])

    # a leftover publish.yaml of an earlier run is removed when the flag is off (the generator owns the directory)
    stale, r = gen("review-service", flipped)
    r = run(CALLERS, stale, "review-service", SHA, VER)
    check("callers: publish=False removes a stale publish.yaml", r.returncode == 0 and wf(stale, "publish.yaml") is None, r.stderr)

    # inconsistent settings
    bad = os.path.join(tmp, "bad-none.py")
    open(bad, "w").write(src.replace('"product-catalog-db": dict(lang="none", with_={}, trivy="")', '"product-catalog-db": dict(lang="none", with_={}, trivy="", publish=True)'))
    _, r = gen("product-catalog-db", bad)
    check("callers: publish=True with lang=none is an error", r.returncode != 0 and "contradicts" in r.stderr, r.stderr)
    bad2 = os.path.join(tmp, "bad-type.py")
    open(bad2, "w").write(src.replace('"order-service": dict(lang="go", with_={}, trivy="", publish=False)', '"order-service": dict(lang="go", with_={}, trivy="", publish="no")'))
    _, r = gen("order-service", bad2)
    check("callers: a non-boolean publish is an error", r.returncode != 0 and "must be True or False" in r.stderr, r.stderr)

_, r = gen("no-such-service")
check("callers: an unknown service is a one-line error naming the known ones", r.returncode != 0 and "unknown service" in r.stderr and "review-service" in r.stderr and "Traceback" not in r.stderr, r.stderr)

# the load generator is not a Go/Python-default service: its entry point is not src/server.py
tmp, _ = gen("load-generator")
lg = wf(tmp, "ci.yaml")["jobs"]["ci"]
check("callers[load-generator]: python gate with its own entry point", "service-ci-python.yaml@" in lg["uses"] and lg["with"] == {"build-check-file": "src/loadgen/__main__.py"}, str(lg))

for f in failures:
    print(f)
print(f"test-generators: {count - len(failures)}/{count} checks passed")
sys.exit(1 if failures else 0)
