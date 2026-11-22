#!/usr/bin/env python3
"""Tests of the two app-repository generators, run as a user would (subprocess, scratch directories, nothing written to a real repo):

  * gen-guardrails.py --dependabot-only: the multi-ecosystem rules GitHub enforces (commit-message and the other group-only options live on
    the GROUP, never on an update that belongs to one; every grouped update has patterns and no schedule; the group exists) and the policy
    (monthly, one group, majors ignored, Docker also minors, 7-day cooldown). The checker is itself tested against the rejected form of
    2026-10-17. If a JSON schema file is given in $DEPENDABOT_SCHEMA (SchemaStore dependabot-2.0.json) and `jsonschema` is importable, the
    output is validated against it too (the schema does NOT know the group-only rule, so it cannot replace the checks above).
  * gen-service-ci.py (v3.8.0, the generated service-ci-*.yaml): the new jobs Proto check (buf), Shell check (shellcheck) and the optional
    Integration tests exist where they should, run only after change detection and for code changes, are waited for by the gate, have
    the pinned tool versions with a sha256 check, and their inputs have the defaults that keep every existing caller unchanged; the Java
    lint step is report-only by default. When `buf` / `shellcheck` are on PATH the generated steps are RUN against throw-away git
    repositories (clean, additive change, breaking change with the default and with the report-only input, legacy package, no proto,
    scripts with and without findings); without the tools those runs are skipped and say so.
  * gen-callers.py: every service in CFG gets ci.yaml; publish.yaml only where `publish` is on; the merge button's start-publish is false
    exactly where there is no publish.yaml; the flag flips cleanly; contradictory settings and unknown services stop with a clear error.

Needs python3 and PyYAML.  usage: tools/test-generators.py     exit 1 and one line per failed check"""
import os, re, shutil, subprocess, sys, tempfile
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


# ---------------------------------------------------------------- service-ci-*.yaml (v3.8.0 jobs)

WFDIR = os.path.join(ROOT, ".github", "workflows")
LANGS = ("go", "node", "python", "java", "dotnet", "none")
BUF_VERSION, BUF_SHA = "1.75.0", "fad1e38d6909bbb583a4da81d8c02a01046e2cd663bde7e337dd24277393764b"
SC_VERSION, SC_SHA = "0.11.0", "8c3be12b05d5c177a04c29e3c78ce89ac86f1595681cab149b65b97c4e227198"


def ci(lang):
    d = yaml.safe_load(open(os.path.join(WFDIR, f"service-ci-{lang}.yaml")))
    return d, d[True]["workflow_call"]["inputs"] if True in d else d["on"]["workflow_call"]["inputs"]


def json_ok(text, use, except_):
    import json
    c = json.loads(text)
    return c.get("version") == "v1" and c["lint"]["use"] == use and c["lint"]["except"] == except_


def step(job, name_part):
    hits = [s for s in job["steps"] if name_part in (s.get("name") or "")]
    return hits[0] if len(hits) == 1 else None


for lang in LANGS:
    d, ins = ci(lang)
    jobs = d["jobs"]
    tag = f"service-ci[{lang}]"
    for jid, name in (("proto", "Proto check"), ("shell", "Shell check")):
        j = jobs.get(jid)
        check(f"{tag}: job {jid} exists as '{name}'", j is not None and j.get("name") == name)
        if j is None:
            continue
        check(f"{tag}: {jid} needs only changes and runs for code changes", j["needs"] == ["changes"] and "needs.changes.outputs.code == 'true'" in j["if"], str(j.get("if")))
        check(f"{tag}: {jid} has contents: read only and a timeout", j["permissions"] == {"contents": "read"} and j["timeout-minutes"] <= 15)
        check(f"{tag}: {jid} is waited for by the gate", jid in jobs["gate"]["needs"])
        check(f"{tag}: {jid} is a name the gate prints", f'[{jid}]="{name}"' in jobs["gate"]["steps"][0]["run"])
    check(f"{tag}: Integration tests {'is not in the file' if lang == 'none' else 'exists'}", ("integration" in jobs) == (lang != "none"))
    if "integration" in jobs:
        j = jobs["integration"]
        check(f"{tag}: integration runs only with code changes AND a non-empty integration-command", "needs.changes.outputs.code == 'true'" in j["if"] and "inputs.integration-command != ''" in j["if"], j["if"])
        check(f"{tag}: integration needs only changes, contents: read, waited for by the gate", j["needs"] == ["changes"] and j["permissions"] == {"contents": "read"} and "integration" in jobs["gate"]["needs"])
        last = j["steps"][-1]
        check(f"{tag}: the command goes through an env var, not into the script text", last["env"] == {"COMMAND": "${{ inputs.integration-command }}"} and "${{" not in last["run"], str(last))
        check(f"{tag}: integration sets up the toolchain like Tests (same steps before the command)", len(j["steps"]) >= 4, str([s.get("name") or s.get("uses") for s in j["steps"]]))
        check(f"{tag}: the gate's INTEGRATION switch is the input being non-empty", jobs["gate"]["steps"][0]["env"]["INTEGRATION"] == "${{ inputs.integration-command != '' }}")
    else:
        check(f"{tag}: the gate's INTEGRATION switch is constant false", jobs["gate"]["steps"][0]["env"]["INTEGRATION"] in ("false", False))
    # inputs: the defaults that leave every existing caller unchanged
    want = {"proto-dir": ("string", ""), "proto-lint-enforce": ("boolean", False), "proto-breaking-enforce": ("boolean", True), "shellcheck-enforce": ("boolean", False)}
    if lang != "none":
        want["integration-command"] = ("string", "")
    if lang == "java":
        want["java-lint-enforce"] = ("boolean", False)
    for k, (typ, default) in want.items():
        check(f"{tag}: input {k} is optional {typ}, default {default!r}", k in ins and ins[k]["type"] == typ and ins[k]["default"] == default and not ins[k].get("required"), str(ins.get(k)))
    check(f"{tag}: no other new input", not (set(ins) & {"java-lint-enforce"} and lang != "java") and ("integration-command" in ins) == (lang != "none"))
    # the pins, verified by sha256 before use
    pj, sj = jobs["proto"], jobs["shell"]
    inst = step(pj, "Install buf")
    check(f"{tag}: buf {BUF_VERSION} is pinned and its sha256 is checked", inst is not None and inst["env"] == {"BUF_VERSION": BUF_VERSION, "BUF_SHA256": BUF_SHA} and "sha256sum -c -" in inst["run"], str(inst))
    inst = step(sj, "Install shellcheck")
    check(f"{tag}: shellcheck {SC_VERSION} is pinned and its sha256 is checked", inst is not None and inst["env"] == {"SHELLCHECK_VERSION": SC_VERSION, "SHELLCHECK_SHA256": SC_SHA} and "sha256sum -c -" in inst["run"], str(inst))
    lint = step(pj, "buf lint")
    check(f"{tag}: buf lint uses MINIMAL without PACKAGE_DIRECTORY_MATCH (legacy hipstershop package)", lint is not None and json_ok(lint["env"]["LINT_CONFIG"], ["MINIMAL"], ["PACKAGE_DIRECTORY_MATCH"]), str(lint))
    br = step(pj, "buf breaking")
    check(f"{tag}: buf breaking compares with a checkout of the default branch", step(pj, "default branch (to compare") is not None and step(pj, "default branch (to compare")["with"]["ref"] == "${{ github.event.repository.default_branch }}" and br is not None and "--against" in br["run"])
    check(f"{tag}: the lint and shellcheck findings are report only unless enforced (the input is the only switch)", lint is not None and lint["env"]["ENFORCE"] == "${{ inputs.proto-lint-enforce }}" and step(sj, "Run shellcheck")["env"]["ENFORCE"] == "${{ inputs.shellcheck-enforce }}")
    check(f"{tag}: buf breaking is enforced by proto-breaking-enforce", br["env"]["ENFORCE"] == "${{ inputs.proto-breaking-enforce }}")
    xl = step(jobs["lint"], "Compiler warnings") if "lint" in jobs else None
    check(f"{tag}: the javac -Xlint step is {'in Lint, -Werror only when java-lint-enforce' if lang == 'java' else 'not here'}",
          (xl is not None and xl["env"] == {"ENFORCE": "${{ inputs.java-lint-enforce }}"} and "-Xlint:all" in xl["run"] and "--rerun" in xl["run"]) if lang == "java" else xl is None)


# ---- running the generated steps (only with the real tools on PATH) -----------------------------------------------------------------

GIT_ENV = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.com"}


def git(cwd, *a):
    subprocess.run(["git", *a], cwd=cwd, check=True, capture_output=True, env={**os.environ, **GIT_ENV})


def make_repo(files):
    """A git repository with these files committed, and a clone of it in ./base (what the checkout of the default branch gives)."""
    tmp = tempfile.mkdtemp(prefix="test-svc-ci-")
    git(tmp, "init", "-q")
    for path, text in files.items():
        os.makedirs(os.path.dirname(os.path.join(tmp, path)) or tmp, exist_ok=True)
        open(os.path.join(tmp, path), "w").write(text)
    git(tmp, "add", "-A")
    git(tmp, "commit", "-q", "-m", "init")
    subprocess.run(["git", "clone", "-q", tmp, os.path.join(tmp, "base")], check=True, capture_output=True)
    return tmp


def run_job(lang, jid, repo, inputs=None):
    """Run the `run:` steps of one job of service-ci-<lang> in `repo` the way the runner does (bash -e -o pipefail, step outputs, `if:` on
    the presence). Install steps are not run (the tools are already on PATH). Returns (exit code of the first failing step or 0, log, summary, outputs)."""
    job = ci(lang)[0]["jobs"][jid]
    tmp = tempfile.mkdtemp(prefix="test-steps-")
    out, summ = os.path.join(tmp, "out"), os.path.join(tmp, "summary.md")
    open(out, "w").close()
    open(summ, "w").close()
    defaults = {"proto-dir": "", "proto-lint-enforce": "false", "proto-breaking-enforce": "true", "shellcheck-enforce": "false", "integration-command": ""}
    defaults.update(inputs or {})
    outputs, log, rc_first = {}, "", 0
    for st in job["steps"]:
        name = st.get("name", "")
        if "run" not in st or name.startswith("Install"):
            continue
        cond = str(st.get("if", ""))
        if "present == 'true'" in cond and outputs.get("site", {}).get("present") != "true":
            continue
        env = {"PATH": os.environ["PATH"], "HOME": os.environ.get("HOME", "/"), "RUNNER_TEMP": tmp, "GITHUB_OUTPUT": out, "GITHUB_STEP_SUMMARY": summ}

        def sub(m):
            e = m.group(1).strip()
            if e.startswith("inputs."):
                return defaults[e[7:]]
            if e.startswith("steps."):
                _, sid, _, key = e.split(".")
                return outputs.get(sid, {}).get(key, "")
            return {"github.event.repository.default_branch": "main"}.get(e, "")
        for k, v in (st.get("env") or {}).items():
            env[k] = re.sub(r"\$\{\{(.*?)\}\}", sub, str(v))
        p = subprocess.run(["bash", "-e", "-o", "pipefail", "-c", st["run"]], cwd=repo, env=env, capture_output=True, text=True)
        log += f"[{name}] exit {p.returncode}\n{p.stdout}{p.stderr}"
        res = {}
        for line in open(out).read().splitlines():
            if "=" in line:
                k, v = line.split("=", 1)
                res[k] = v
        open(out, "w").close()
        if st.get("id"):
            outputs[st["id"]] = res
        if p.returncode and not rc_first:
            rc_first = p.returncode
    return rc_first, log, open(summ).read(), outputs


PROTO = 'syntax = "proto3";\n\npackage hipstershop;\n\nmessage Address {\n  string street = 1;\n  int32 zip_code = 2;\n}\n\nservice S {\n  rpc Get(Address) returns (Address);\n}\n'
HEALTH = 'syntax = "proto3";\n\npackage grpc.health.v1;\n\nmessage HealthCheckRequest {\n  string service = 1;\n}\n'

if shutil.which("buf") and shutil.which("git"):
    # the legacy package and a second package in the same directory (as in currency-service): lint finds it, report only by default
    r = make_repo({"proto/a.proto": PROTO})
    rc, log, summ, o = run_job("go", "proto", r)
    check("proto run: a clean legacy-package repository passes lint and breaking", rc == 0 and o["lint"]["result"] == "passed" and o["breaking"]["result"] == "passed", log)
    check("proto run: the summary says what was checked", "### Proto check" in summ and "`buf lint` | passed" in summ, summ)
    r = make_repo({"proto/a.proto": PROTO, "proto/health.proto": HEALTH})
    rc, log, summ, o = run_job("go", "proto", r)
    check("proto run: MINIMAL findings (two packages in one directory) are report only: exit 0, warning, listed in the summary",
          rc == 0 and o["lint"]["result"] == "findings" and "::warning::buf lint" in log and "Multiple packages" in summ, log)
    rc, log, _, o = run_job("go", "proto", r, {"proto-lint-enforce": "true"})
    check("proto run: proto-lint-enforce=true fails on the same findings", rc != 0 and "::error::buf lint" in log, log)
    # PACKAGE_DIRECTORY_MATCH is not among the rules: hipstershop in proto/ is fine
    check("proto run: hipstershop in proto/ does not trip PACKAGE_DIRECTORY_MATCH", "PACKAGE_DIRECTORY_MATCH" not in log)
    # breaking against base
    r = make_repo({"proto/a.proto": PROTO})
    open(os.path.join(r, "proto/a.proto"), "w").write(PROTO.replace("  int32 zip_code = 2;\n", "  int32 zip_code = 2;\n  string extra = 3;\n"))
    rc, log, _, o = run_job("go", "proto", r)
    check("proto run: an additive change passes buf breaking", rc == 0 and o["breaking"]["result"] == "passed", log)
    open(os.path.join(r, "proto/a.proto"), "w").write(PROTO.replace("int32 zip_code = 2;", "string zip_code = 2;"))
    rc, log, summ, o = run_job("go", "proto", r)
    check("proto run: a changed field type fails by default and names the field", rc != 0 and o["breaking"]["result"] == "findings" and "zip_code" in log and "::error::buf breaking" in log, log)
    check("proto run: the summary names the broken contract", "breaks the contract of the default branch" in summ, summ)
    rc, log, _, o = run_job("go", "proto", r, {"proto-breaking-enforce": "false"})
    check("proto run: proto-breaking-enforce=false makes the same change report only", rc == 0 and "::warning::buf breaking" in log, log)
    open(os.path.join(r, "proto/a.proto"), "w").write(PROTO.replace("  int32 zip_code = 2;\n", ""))
    rc, log, _, o = run_job("go", "proto", r)
    check("proto run: a removed field fails", rc != 0 and "was deleted" in log, log)
    # a proto dir the default branch does not have yet: nothing to compare
    shutil.rmtree(os.path.join(r, "base", "proto"))
    rc, log, _, o = run_job("go", "proto", r)
    check("proto run: new proto directory (absent on the default branch) is not a failure", rc == 0 and o["breaking"]["result"] == "new", log)
    # directories other than proto/, explicit proto-dir, wrong values, no proto at all
    for d_ in ("src/main/proto", "src/protos"):
        r = make_repo({f"{d_}/a.proto": PROTO})
        rc, log, _, o = run_job("go", "proto", r)
        check(f"proto run: {d_} is found without an input", rc == 0 and o["site"]["dir"] == d_ and o["lint"]["result"] == "passed", log)
    r = make_repo({"api/a.proto": PROTO})
    rc, log, summ, o = run_job("go", "proto", r)
    check("proto run: protos outside the known directories are not silently ignored (warning + summary note), and not a failure", rc == 0 and o["site"]["present"] == "false" and "::warning::" in log and "proto-dir" in summ, log + summ)
    rc, log, _, o = run_job("go", "proto", r, {"proto-dir": "api"})
    check("proto run: proto-dir=api checks it", rc == 0 and o["site"]["dir"] == "api" and o["lint"]["result"] == "passed", log)
    for bad in ("../x", "/etc", "a b", "a;b"):
        rc, log, _, _ = run_job("go", "proto", r, {"proto-dir": bad})
        check(f"proto run: proto-dir={bad!r} is refused", rc != 0 and "relative path" in log, log)
    rc, log, _, _ = run_job("go", "proto", r, {"proto-dir": "nope"})
    check("proto run: a proto-dir without .proto files is an error", rc != 0, log)
    r = make_repo({"README.md": "x\n"})
    rc, log, summ, o = run_job("none", "proto", r)
    check("proto run: a repository without proto ends green with 'nothing to check'", rc == 0 and o["site"]["present"] == "false" and "nothing to check" in summ, log + summ)
else:
    print("test-generators: buf or git not on PATH, the proto steps were not run")

CLEAN_SH = '#!/usr/bin/env bash\nset -euo pipefail\necho "$HOME"\n'
BAD_SH = '#!/usr/bin/env bash\nfiles="a b"\nls $files\n'
if shutil.which("shellcheck") and shutil.which("git"):
    r = make_repo({"run.sh": CLEAN_SH})
    rc, log, summ, o = run_job("go", "shell", r)
    check("shell run: a clean script passes", rc == 0 and o["shellcheck"]["result"] == "passed" and "Result: passed" in summ, log)
    r = make_repo({"tests/run.sh": BAD_SH, "ok.sh": CLEAN_SH})
    rc, log, summ, o = run_job("go", "shell", r)
    check("shell run: findings are report only by default: exit 0, an annotation on the line, listed in the summary",
          rc == 0 and o["shellcheck"]["result"] == "findings" and "::warning file=tests/run.sh,line=3" in log and "SC2086" in summ, log)
    rc, log, _, _ = run_job("go", "shell", r, {"shellcheck-enforce": "true"})
    check("shell run: shellcheck-enforce=true fails on the same findings", rc != 0 and "::error file=tests/run.sh,line=3" in log, log)
    r = make_repo({"main.go": "package main\n"})
    rc, log, summ, o = run_job("go", "shell", r)
    check("shell run: no scripts ends green with 'nothing to check'", rc == 0 and o["site"]["present"] == "false" and "nothing to check" in summ, log + summ)
    r = make_repo({"a b.sh": CLEAN_SH})
    rc, log, _, o = run_job("go", "shell", r)
    check("shell run: a file name with a space is handled", rc == 0 and o["shellcheck"]["result"] == "passed", log)
else:
    print("test-generators: shellcheck or git not on PATH, the shell steps were not run")

# the integration step: the command runs under bash -euo pipefail from the repository root
if shutil.which("git"):
    r = make_repo({"marker.txt": "x\n"})
    for lang in ("go", "node", "python", "java", "dotnet"):
        job = ci(lang)[0]["jobs"]["integration"]
        last = job["steps"][-1]
        for cmd, ok in (("test -f marker.txt\ntrue | cat", True), ("test -f marker.txt\nfalse", False), ("false | cat", False), ("echo $UNSET_VARIABLE", False)):
            p = subprocess.run(["bash", "-e", "-o", "pipefail", "-c", last["run"]], cwd=r, env={"PATH": os.environ["PATH"], "COMMAND": cmd}, stdin=subprocess.DEVNULL, capture_output=True, text=True)
            check(f"integration[{lang}]: command {cmd!r} {'passes' if ok else 'fails'} the step", (p.returncode == 0) == ok, p.stdout + p.stderr)

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
