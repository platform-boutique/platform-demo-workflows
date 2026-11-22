#!/usr/bin/env python3
"""Generate the per-language pull-request workflows service-ci-{go,node,python,java,dotnet,none}.yaml.

usage: gen-service-ci.py [repo-root]      (writes <repo-root>/.github/workflows/service-ci-*.yaml)

Why generated: the Actions graph of a caller shows every job of the called workflow, skipped ones too. One workflow for all languages
therefore drew four hollow language boxes and nested boxes inside boxes. These files are FLAT (no nested `uses:` of other workflows)
and contain only what the repository really runs:

    Detect changes | Secret scan | Workflow lint      no needs: start at t=0, together
    Lint | Build | Tests                              need only `Detect changes` (skipped for a docs-only change), run in
    Dependency scan | Code scan | Image build and scan   parallel with the other two (the image is pushed as
                                                         ghcr.io/<repo>:tree-<hash> by runs that may; see wfgen.py)
    gate                                              needs EVERY job, always runs

Job names: sentence case, what the job does, no group prefixes; the caller's job id is the prefix GitHub shows (`ci / Build`), and the
required check stays `ci / gate`. The gate keys on JOB IDS (the keys of `needs`), never on names; it only shows the names.
Run summaries (GITHUB_STEP_SUMMARY, one section per job on the run page): `Detect changes` writes what changed (changed-paths.yaml),
`Image build and scan` the image (tools/wfgen.py), `gate` the verdict, the results and what the Merge button will do.

The left column means "starts at t=0", not "must pass first": a flaky gitleaks download or one lint complaint does not hide the
test, SAST and Trivy results (one round trip instead of two). The gate fails if any of them failed.

Source of truth (nothing is retyped here, so a fix cannot be forgotten):
  * the job bodies come from the stand-alone workflows, extracted as text: go-ci / node-ci / python-ci / java-gradle-ci / dotnet-ci
    (lint-format, build-check, unit-tests, sca, sast), changed-paths (detect), secret-scan, workflow-lint;
  * the optional smoke test and E2E job of go-ci / node-ci are folded into the Build and Tests jobs as steps with an `if:` on the
    input (one build and one toolchain setup instead of two; no job that shows up as "skipped");
  * tool versions (golangci-lint, gosec, semgrep, ...) are inputs of the stand-alone workflows; here they are written in as the
    stand-alone defaults, so they are bumped in one place;
  * input names, types and defaults are copied from the stand-alone workflow;
  * the image job is assembled from the steps in tools/wfgen.py, which service-publish.yaml uses too (tree lookup, build, digest record,
    Trivy scan: written once); only the gate script lives in this file.
Every transformation asserts that it matched, so a refactoring of a source workflow fails here instead of silently changing a file.
tools/check-consistency.py fails when a generated file differs from this output.
"""
import os, re, sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(sys.argv[1]) if len(sys.argv) > 1 else os.path.dirname(HERE)
WFDIR = os.path.join(ROOT, ".github", "workflows")

sys.path.insert(0, HERE)
import wfgen  # noqa: E402
from wfgen import Job, read_lines, sub, with_if  # noqa: E402

wfgen.set_root(ROOT)

LANGS = ["go", "node", "python", "java", "dotnet", "none"]
SOURCE = {"go": "go-ci.yaml", "node": "node-ci.yaml", "python": "python-ci.yaml", "java": "java-gradle-ci.yaml", "dotnet": "dotnet-ci.yaml"}
TITLE = {"go": "Go", "node": "Node", "python": "Python", "java": "Java", "dotnet": ".NET", "none": "no language pipeline"}

# inputs a file declares besides the common ones (names, types, defaults are copied from the stand-alone workflow)
DECLARED = {
    "go": ["build-path", "test-packages", "e2e-packages", "smoke-port", "smoke-env"],
    "node": ["node-version-file", "semgrep-config", "smoke-port", "smoke-command", "smoke-health-args", "smoke-script"],
    "python": ["python-version-file", "source-dir", "build-check-file", "tests-path", "bandit-config"],
    "java": ["java-version", "java-distribution", "semgrep-config"],
    "dotnet": ["dotnet-version-file", "solution", "project", "test-project", "test-filter", "semgrep-config"],
    "none": [],
}
# tool versions: inputs of the stand-alone workflow, a fixed value here (the stand-alone default)
TOOL_VERSIONS = ["golangci-lint-version", "govulncheck-version", "gosec-version", "semgrep-version", "ruff-version",
                 "pip-audit-version", "bandit-version"]
# the job was an optional job of the stand-alone workflow; the wording of its enabling input changes with it
DESC_FIX = {
    ("e2e-packages", "empty skips the job"): "empty skips the E2E step",
    ("smoke-port", "empty skips the job"): "empty skips the smoke test",
}

COMMON_INPUTS = """\
      image:
        description: Build the image and scan it (when code changed). false for a repository without an image pipeline.
        type: boolean
        default: true
      context:
        description: Docker build context
        type: string
        default: .
      dockerfile:
        description: Dockerfile name inside the context
        type: string
        default: Dockerfile
      trivyignores:
        description: Path to a .trivyignore file in the caller repo (optional)
        type: string
        default: ""
"""

GUARDS = ["changes", "secret-scan", "workflow-lint", "docs"]
# Job names: sentence case, what the job does, no group prefixes (the caller's job id is the prefix GitHub shows: `ci / Build`).
# Language detail belongs in the step names. The same names in all six files (tools/check-consistency.py pins them).
NAMES = {
    "changes": "Detect changes", "secret-scan": "Secret scan", "workflow-lint": "Workflow lint", "docs": "Docs check",
    "lint": "Lint", "proto": "Proto check", "shell": "Shell check", "build": "Build", "test": "Tests", "integration": "Integration tests",
    "sca": "Dependency scan", "sast": "Code scan", "image": "Image build and scan", "gate": "gate",
}
# v3.8.0: `proto` (buf) and `shell` (shellcheck) are in every file and end green with "nothing to check" when the repository has no
# such files; `integration` (TEST-02) exists in the five language files and is SKIPPED unless the caller sets `integration-command`.
CHECKS = {l: ["lint", "proto", "shell", "build", "test", "integration", "sca", "sast", "image"] for l in LANGS if l != "none"}
CHECKS["none"] = ["proto", "shell", "image"]
OPTIONAL = {"integration"}  # checks that run only when a caller input turns them on; the gate expects "skipped" otherwise


# ───────────────────────── reading the stand-alone workflows (tools/wfgen.py) ─────────────────────────

def tool_defaults(lang):
    """{input name: default} of the tool-version inputs of a stand-alone workflow."""
    out = {}
    if lang not in SOURCE:
        return out
    for name, block in input_blocks(SOURCE[lang]).items():
        if name in TOOL_VERSIONS:
            out[name] = re.search(r"^        default: (.*)$", block, re.M).group(1).strip()
    return out


def input_blocks(fname):
    lines = read_lines(fname)
    i = lines.index("    inputs:") + 1
    blocks, key = {}, None
    while i < len(lines) and (lines[i].startswith("      ") or not lines[i].strip()):
        m = re.match(r"^      ([a-z0-9-]+):\s*$", lines[i])
        if m:
            key = m.group(1)
            blocks[key] = []
        if key and lines[i].strip():
            blocks[key].append(lines[i])
        i += 1
    return {k: "\n".join(v) + "\n" for k, v in blocks.items()}


# ───────────────────────── rendering ─────────────────────────

def render_job(key, steps, **kw):
    return wfgen.render_job(key, NAMES[key], steps, **kw)


STAGE2_NEEDS = ["changes"]  # only the change detection: a failed guard must not hide the results of the checks
STAGE2_IF = "${{ needs.changes.outputs.code == 'true' }}"


def guard_jobs():
    """The three guards: inline copies of changed-paths.detect, secret-scan.secret-scan and workflow-lint.actionlint."""
    jobs = []
    for key, fname, src in (("changes", "changed-paths.yaml", "detect"), ("secret-scan", "secret-scan.yaml", "secret-scan"),
                            ("workflow-lint", "workflow-lint.yaml", "actionlint")):
        j = Job(fname, src)
        jobs.append(render_job(key, j.steps, timeout=j.timeout(), perms=j.raw("permissions"),
                               outputs=j.raw("outputs") if "outputs" in j.fields else None))
    return jobs


# The docs check (DOC-12): `mkdocs build --strict` and the relative-link check, only for a repository that has an mkdocs.yml. It is a
# guard of the graph (no needs, starts at t=0, runs for a documentation-only change too: that is the change it exists for), and it
# always ends green when there is nothing to check, so the gate has one rule for it. The versions are fixed here (no workflow input):
# at least 7 days old when chosen, bumped by hand in this one place (Dependabot does not read pip commands in a workflow).
MKDOCS_VERSION = "1.6.1"
TECHDOCS_CORE_VERSION = "1.7.1"  # pins mkdocs-material and the other plugins itself
DOCS_PYTHON = "3.13"


def docs_job():
    with open(os.path.join(HERE, "check-md-links.py")) as f:
        links = f.read().rstrip("\n")
    assert "\nPY\n" not in links and "${{" not in links, "tools/check-md-links.py cannot be embedded as a heredoc"
    install = (f"pip install --quiet --disable-pip-version-check mkdocs=={MKDOCS_VERSION} mkdocs-techdocs-core=={TECHDOCS_CORE_VERSION}")
    steps = [
        wfgen.checkout_step(),
        ("      - name: Look for mkdocs.yml\n"
         "        id: site\n"
         "        run: |\n" + wfgen.script(DOCS_SITE_SCRIPT)),
        (f"      - uses: {wfgen.pin('actions/setup-python')}\n"
         "        if: steps.site.outputs.present == 'true'\n"
         "        with:\n"
         f"          python-version: \"{DOCS_PYTHON}\""),
        ("      - name: Install mkdocs\n"
         "        if: steps.site.outputs.present == 'true'\n"
         "        run: " + install),
        ("      - name: Build the docs (mkdocs build --strict)\n"
         "        id: build\n"
         "        if: steps.site.outputs.present == 'true'\n"
         "        run: mkdocs build --strict --site-dir \"$RUNNER_TEMP/site\""),
        # the link check runs after a failed build too: both findings in one run
        ("      - name: Check relative markdown links\n"
         "        id: links\n"
         "        if: ${{ !cancelled() && steps.site.outputs.present == 'true' }}\n"
         "        run: |\n" + wfgen.script("python3 -I - . <<'PY'\n" + links + "\nPY")),
        ("      - name: Write the docs summary\n"
         "        if: ${{ !cancelled() }}\n"
         "        env:\n"
         "          PRESENT: ${{ steps.site.outputs.present }}\n"
         "          BUILD: ${{ steps.build.outcome }}\n"
         "          LINKS: ${{ steps.links.outcome }}\n"
         "        run: |\n" + wfgen.script(DOCS_SUMMARY_SCRIPT)),
    ]
    return render_job("docs", steps, timeout=10, perms="    permissions:\n      contents: read",
                      outputs="    outputs:\n      present: ${{ steps.site.outputs.present }}")


DOCS_SITE_SCRIPT = r"""
set -euo pipefail
if [ -f mkdocs.yml ]; then
  echo "present=true" >> "$GITHUB_OUTPUT"
else
  echo "present=false" >> "$GITHUB_OUTPUT"
  echo "no mkdocs.yml in this repository: nothing to check"
fi
"""

DOCS_SUMMARY_SCRIPT = r"""
set -euo pipefail
words() { case "$1" in success) echo passed ;; failure) echo "**failed**" ;; skipped) echo "not run" ;; *) echo "not run" ;; esac; }
{
  echo "### Docs check"
  echo
  if [ "$PRESENT" != true ]; then
    echo "This repository has no \`mkdocs.yml\`, so there is nothing to check."
  else
    echo "| | |"
    echo "|---|---|"
    echo "| \`mkdocs build --strict\` | $(words "$BUILD") |"
    echo "| relative links in README.md and docs/ | $(words "$LINKS") |"
    echo
    echo "External links and \`#anchors\` are not checked. The details are in the log of each step."
  fi
} >> "$GITHUB_STEP_SUMMARY"
"""


# ───────────────────────── v3.8.0: proto check, shell check, integration tests, Java compiler warnings ─────────────────────────
#
# Rule of this block: nothing here may turn an existing repository's pull request red with the default inputs. A check that is
# clean on every app repository today FAILS the run (buf breaking: a pull request that breaks the wire contract of the default
# branch). A check that has findings on existing code (buf lint with the MINIMAL rules, shellcheck, javac -Xlint:all) is REPORT ONLY:
# the step ends green, writes `::warning::` annotations and the run summary lists the findings, and an input (`*-enforce`) turns
# it into a failure once the code is clean. The integration job runs only when the caller passes `integration-command`.
# Tool versions are pinned here and verified by sha256 (no workflow input: bumped by hand in this one place; Dependabot does not
# read a download URL in a script).
BUF_VERSION = "1.75.0"
BUF_SHA256 = "fad1e38d6909bbb583a4da81d8c02a01046e2cd663bde7e337dd24277393764b"  # buf-Linux-x86_64 of the release (sha256.txt)
# `hipstershop` (the legacy package of every service) does not match its directory, and the health proto sits next to the service
# proto: inline MINIMAL without PACKAGE_DIRECTORY_MATCH. v1 inline config: it makes the directory given to buf the module root
# (a v2 inline config makes the repository root the module and then refuses `proto/` as the input).
BUF_LINT_CONFIG = '{"version":"v1","lint":{"use":["MINIMAL"],"except":["PACKAGE_DIRECTORY_MATCH"]}}'
BUF_BREAKING_CONFIG = '{"version":"v1","breaking":{"use":["FILE"]}}'
SHELLCHECK_VERSION = "0.11.0"
SHELLCHECK_SHA256 = "8c3be12b05d5c177a04c29e3c78ce89ac86f1595681cab149b65b97c4e227198"  # shellcheck-v0.11.0.linux.x86_64.tar.xz (GitHub's digest)
PROTO_DIRS = ["proto", "src/main/proto", "src/protos"]  # looked at in this order when `proto-dir` is empty

# the inputs of all six files (identical text: tools/check-consistency.py pins them)
NEW_COMMON_INPUTS = """\
      proto-dir:
        description: Directory with the .proto files (the buf module root). Empty looks for proto, src/main/proto and src/protos; a repository without any skips the Proto check.
        type: string
        default: ""
      proto-lint-enforce:
        description: Fail the Proto check on buf lint findings (MINIMAL rules without PACKAGE_DIRECTORY_MATCH). false = report only, the findings are in the run summary.
        type: boolean
        default: false
      proto-breaking-enforce:
        description: Fail the Proto check when the pull request breaks the wire contract of the default branch (buf breaking, FILE rules). false = report only.
        type: boolean
        default: true
      shellcheck-enforce:
        description: Fail the Shell check on shellcheck findings in the tracked *.sh files. false = report only, the findings are in the run summary.
        type: boolean
        default: false
"""
# the five language files
INTEGRATION_INPUT = """\
      integration-command:
        description: Shell command that runs the integration tests (TEST-02), run from the repository root after the toolchain is set up and the dependencies are installed; Docker is available for throwaway containers. Empty (default) = no Integration tests job runs.
        type: string
        default: ""
"""
JAVA_LINT_INPUT = """\
      java-lint-enforce:
        description: Fail Lint on javac -Xlint:all warnings (compiled with -Werror). false = report only, the warnings are in the run summary.
        type: boolean
        default: false
"""

PROTO_SITE_SCRIPT = r"""
set -euo pipefail
dir=$PROTO_DIR
case "$dir" in
  *[!A-Za-z0-9._/-]* | /* | *..*)
    echo "::error::input proto-dir must be a relative path of letters, digits, . _ - and /"
    exit 1
    ;;
esac
if [ -n "$dir" ]; then
  if [ -z "$(git ls-files -- "$dir" | grep '\.proto$' || true)" ]; then
    echo "::error::input proto-dir is '$dir', which has no tracked .proto file"
    exit 1
  fi
else
  for candidate in __DIRS__; do
    if [ -n "$(git ls-files -- "$candidate" | grep '\.proto$' || true)" ]; then dir=$candidate; break; fi
  done
fi
if [ -z "$dir" ]; then
  echo "present=false" >> "$GITHUB_OUTPUT"
  if [ -n "$(git ls-files -- '*.proto' | head -1)" ]; then
    echo "other=true" >> "$GITHUB_OUTPUT"
    echo "::warning::.proto files exist outside __DIRS_TEXT__; set the input proto-dir to check them"
  else
    echo "no .proto file in this repository: nothing to check"
  fi
  exit 0
fi
echo "present=true" >> "$GITHUB_OUTPUT"
echo "dir=$dir" >> "$GITHUB_OUTPUT"
echo "proto directory: $dir"
"""

BUF_INSTALL_SCRIPT = r"""
set -euo pipefail
curl -fsSL --retry 3 --retry-all-errors -o "$RUNNER_TEMP/buf" "https://github.com/bufbuild/buf/releases/download/v${BUF_VERSION}/buf-Linux-x86_64"
echo "${BUF_SHA256}  $RUNNER_TEMP/buf" | sha256sum -c -
sudo install -m 0755 "$RUNNER_TEMP/buf" /usr/local/bin/buf
buf --version
"""

BUF_LINT_SCRIPT = r"""
set -uo pipefail
rc=0
buf lint "$PROTO_DIR" --config "$LINT_CONFIG" > "$RUNNER_TEMP/buf-lint.txt" 2>&1 || rc=$?
cat "$RUNNER_TEMP/buf-lint.txt"
case "$rc" in
  0) echo "result=passed" >> "$GITHUB_OUTPUT" ;;
  100)
    echo "result=findings" >> "$GITHUB_OUTPUT"
    n=$(grep -c . "$RUNNER_TEMP/buf-lint.txt" || true)
    if [ "$ENFORCE" = true ]; then
      echo "::error::buf lint: $n finding(s)"
      exit 1
    fi
    echo "::warning::buf lint: $n finding(s). Report only (input proto-lint-enforce is false); the list is in the run summary."
    ;;
  *)
    echo "result=error" >> "$GITHUB_OUTPUT"
    echo "::error::buf lint did not run (exit $rc)"
    exit 1
    ;;
esac
"""

BUF_BREAKING_SCRIPT = r"""
set -uo pipefail
base="base/$PROTO_DIR"
if [ -z "$(find "$base" -name '*.proto' -print -quit 2>/dev/null)" ]; then
  echo "result=new" >> "$GITHUB_OUTPUT"
  echo "the default branch has no .proto file in $PROTO_DIR: nothing to compare against"
  exit 0
fi
rc=0
buf breaking "$PROTO_DIR" --against "$base" --config "$BREAKING_CONFIG" > "$RUNNER_TEMP/buf-breaking.txt" 2>&1 || rc=$?
cat "$RUNNER_TEMP/buf-breaking.txt"
case "$rc" in
  0) echo "result=passed" >> "$GITHUB_OUTPUT" ;;
  100)
    echo "result=findings" >> "$GITHUB_OUTPUT"
    n=$(grep -c . "$RUNNER_TEMP/buf-breaking.txt" || true)
    if [ "$ENFORCE" = true ]; then
      echo "::error::buf breaking: $n change(s) break the wire contract of the default branch. Proto changes are additive only (new fields, new RPCs)."
      exit 1
    fi
    echo "::warning::buf breaking: $n change(s) break the wire contract of the default branch. Report only (input proto-breaking-enforce is false)."
    ;;
  *)
    echo "result=error" >> "$GITHUB_OUTPUT"
    echo "::error::buf breaking did not run (exit $rc)"
    exit 1
    ;;
esac
"""

PROTO_SUMMARY_SCRIPT = r"""
set -uo pipefail
words() {
  case "$1" in
    passed) echo passed ;;
    findings) echo "$2" ;;
    new) echo "nothing to compare: the default branch has no proto here" ;;
    error) echo "**did not run**" ;;
    *) echo "not run" ;;
  esac
}
show() { # show <file> <heading>: the findings, at most 40 lines
  [ -s "$1" ] || return 0
  echo
  echo "$2"
  echo
  echo '```'
  head -n 40 "$1"
  [ "$(wc -l < "$1")" -le 40 ] || echo "... (the log of the step has the rest)"
  echo '```'
}
{
  echo "### Proto check"
  echo
  if [ "$PRESENT" != true ]; then
    echo "This repository has no .proto file in proto, src/main/proto or src/protos, so there is nothing to check."
    [ "$OTHER" != true ] || echo "It has .proto files elsewhere: set the input \`proto-dir\` to check them."
  else
    echo "Directory: \`$PROTO_DIR\`. buf __BUF_VERSION__, inline config (the MINIMAL lint rules without PACKAGE_DIRECTORY_MATCH, the FILE breaking rules); a \`buf.yaml\` in the repository is not used."
    echo
    echo "| | |"
    echo "|---|---|"
    echo "| \`buf lint\` | $(words "$LINT" "findings, report only (input \`proto-lint-enforce\`)") |"
    echo "| \`buf breaking\` against \`$DEFAULT_BRANCH\` | $(words "$BREAKING" "**breaks the contract of the default branch**") |"
    [ "$LINT" != findings ] || show "$RUNNER_TEMP/buf-lint.txt" "buf lint:"
    [ "$BREAKING" != findings ] || show "$RUNNER_TEMP/buf-breaking.txt" "buf breaking:"
  fi
} >> "$GITHUB_STEP_SUMMARY"
"""

SHELL_SITE_SCRIPT = r"""
set -euo pipefail
n=$(git ls-files -- '*.sh' | wc -l)
if [ "$n" -gt 0 ]; then
  echo "present=true" >> "$GITHUB_OUTPUT"
  echo "$n shell script(s)"
else
  echo "present=false" >> "$GITHUB_OUTPUT"
  echo "no tracked *.sh file in this repository: nothing to check"
fi
"""

SHELLCHECK_INSTALL_SCRIPT = r"""
set -euo pipefail
curl -fsSL --retry 3 --retry-all-errors -o "$RUNNER_TEMP/shellcheck.tar.xz" "https://github.com/koalaman/shellcheck/releases/download/v${SHELLCHECK_VERSION}/shellcheck-v${SHELLCHECK_VERSION}.linux.x86_64.tar.xz"
echo "${SHELLCHECK_SHA256}  $RUNNER_TEMP/shellcheck.tar.xz" | sha256sum -c -
mkdir "$RUNNER_TEMP/shellcheck"
tar -xJf "$RUNNER_TEMP/shellcheck.tar.xz" -C "$RUNNER_TEMP/shellcheck" --strip-components=1 "shellcheck-v${SHELLCHECK_VERSION}/shellcheck"
sudo install -m 0755 "$RUNNER_TEMP/shellcheck/shellcheck" /usr/local/bin/shellcheck
shellcheck --version | head -2
"""

SHELLCHECK_RUN_SCRIPT = r"""
set -uo pipefail
mapfile -d '' files < <(git ls-files -z -- '*.sh')
rc=0
shellcheck -x --format=gcc -- "${files[@]}" > "$RUNNER_TEMP/shellcheck.txt" 2>&1 || rc=$?
cat "$RUNNER_TEMP/shellcheck.txt"
case "$rc" in
  0) echo "result=passed" >> "$GITHUB_OUTPUT" ;;
  1)
    echo "result=findings" >> "$GITHUB_OUTPUT"
    n=$(grep -c . "$RUNNER_TEMP/shellcheck.txt" || true)
    level=warning
    [ "$ENFORCE" != true ] || level=error
    # one annotation per finding on the line it is about (the gcc format: file:line:col: level: message [SCnnnn])
    while IFS= read -r line; do
      if [[ "$line" =~ ^(.+):([0-9]+):([0-9]+):\ [a-z]+:\ (.*)$ ]]; then
        echo "::$level file=${BASH_REMATCH[1]},line=${BASH_REMATCH[2]},col=${BASH_REMATCH[3]}::${BASH_REMATCH[4]}"
      fi
    done < "$RUNNER_TEMP/shellcheck.txt"
    if [ "$ENFORCE" = true ]; then
      echo "::error::shellcheck: $n finding(s)"
      exit 1
    fi
    echo "::warning::shellcheck: $n finding(s). Report only (input shellcheck-enforce is false); the list is in the run summary."
    ;;
  *)
    echo "result=error" >> "$GITHUB_OUTPUT"
    echo "::error::shellcheck did not run cleanly (exit $rc)"
    exit 1
    ;;
esac
"""

SHELL_SUMMARY_SCRIPT = r"""
set -uo pipefail
{
  echo "### Shell check"
  echo
  if [ "$PRESENT" != true ]; then
    echo "This repository has no tracked \`*.sh\` file, so there is nothing to check."
  else
    echo "shellcheck __SHELLCHECK_VERSION__ on every tracked \`*.sh\` file (default severity, \`-x\`)."
    echo
    case "$RESULT" in
      passed) echo "Result: passed." ;;
      findings)
        echo "Result: **findings**, report only (input \`shellcheck-enforce\`):"
        echo
        echo '```'
        head -n 40 "$RUNNER_TEMP/shellcheck.txt"
        [ "$(wc -l < "$RUNNER_TEMP/shellcheck.txt")" -le 40 ] || echo "... (the log of the step has the rest)"
        echo '```'
        ;;
      *) echo "Result: **did not run**." ;;
    esac
  fi
} >> "$GITHUB_STEP_SUMMARY"
"""

JAVA_XLINT_SCRIPT = r"""
set -uo pipefail
# javac -Xlint:all through an init script, so no build file of the repository has to change; -Werror only when enforced
cat > "$RUNNER_TEMP/xlint.gradle" <<'GRADLE'
allprojects {
    tasks.withType(JavaCompile).configureEach {
        options.compilerArgs << '-Xlint:all'
        if (System.getenv('XLINT_WERROR') == 'true') { options.compilerArgs << '-Werror' }
    }
}
GRADLE
export XLINT_WERROR="$ENFORCE"
rc=0
./gradlew compileJava --rerun --no-daemon --console=plain --init-script "$RUNNER_TEMP/xlint.gradle" 2>&1 | tee "$RUNNER_TEMP/xlint.log" || rc=${PIPESTATUS[0]}
# the distinct warnings, without the workspace prefix (Gradle repeats them in its failure message)
grep ': warning: ' "$RUNNER_TEMP/xlint.log" | sed -e 's/^ *//' -e "s#^$PWD/##" | sort -u > "$RUNNER_TEMP/xlint-warnings.txt" || true
n=$(wc -l < "$RUNNER_TEMP/xlint-warnings.txt")
{
  echo "### Java lint (javac -Xlint:all)"
  echo
  if [ "$rc" != 0 ]; then
    echo "**Failed** (exit $rc)$([ "$ENFORCE" = true ] && echo ", compiled with -Werror (input \`java-lint-enforce\`)")."
  elif [ "$n" -gt 0 ]; then
    echo "**$n warning(s)**, report only (input \`java-lint-enforce\`):"
  else
    echo "No warnings."
  fi
  if [ "$n" -gt 0 ]; then
    echo
    echo '```'
    head -n 40 "$RUNNER_TEMP/xlint-warnings.txt"
    echo '```'
  fi
} >> "$GITHUB_STEP_SUMMARY"
if [ "$rc" != 0 ]; then
  echo "::error::compiling with -Xlint:all failed (exit $rc)"
  exit 1
fi
[ "$n" -eq 0 ] || echo "::warning::javac -Xlint:all: $n warning(s). Report only (input java-lint-enforce is false); the list is in the run summary."
"""


def env_block(pairs):
    return "        env:\n" + "".join(f"          {k}: {v}\n" for k, v in pairs)


def proto_job():
    present = "steps.site.outputs.present == 'true'"
    steps = [
        wfgen.checkout_step(),
        ("      - name: Look for proto files\n"
         "        id: site\n" + env_block([("PROTO_DIR", "${{ inputs.proto-dir }}")]) +
         "        run: |\n" + wfgen.script(PROTO_SITE_SCRIPT.replace("__DIRS__", " ".join(PROTO_DIRS)).replace("__DIRS_TEXT__", ", ".join(PROTO_DIRS[:-1]) + " or " + PROTO_DIRS[-1]))),
        (f"      - name: Check out the default branch (to compare against)\n"
         f"        if: {present}\n"
         f"        uses: {wfgen.pin('actions/checkout')}\n"
         "        with:\n"
         "          ref: ${{ github.event.repository.default_branch }}\n"
         "          path: base\n"
         "          persist-credentials: false"),
        ("      - name: Install buf\n"
         f"        if: {present}\n" + env_block([("BUF_VERSION", f'"{BUF_VERSION}"'), ("BUF_SHA256", f'"{BUF_SHA256}"')]) +
         "        run: |\n" + wfgen.script(BUF_INSTALL_SCRIPT)),
        ("      - name: Lint the proto files (buf lint)\n"
         "        id: lint\n"
         f"        if: {present}\n" +
         env_block([("PROTO_DIR", "${{ steps.site.outputs.dir }}"), ("LINT_CONFIG", f"'{BUF_LINT_CONFIG}'"),
                    ("ENFORCE", "${{ inputs.proto-lint-enforce }}")]) +
         "        run: |\n" + wfgen.script(BUF_LINT_SCRIPT)),
        # runs after a failed lint too: both findings in one run
        ("      - name: Check the contract of the default branch (buf breaking)\n"
         "        id: breaking\n"
         f"        if: ${{{{ !cancelled() && {present} }}}}\n" +
         env_block([("PROTO_DIR", "${{ steps.site.outputs.dir }}"), ("BREAKING_CONFIG", f"'{BUF_BREAKING_CONFIG}'"),
                    ("ENFORCE", "${{ inputs.proto-breaking-enforce }}")]) +
         "        run: |\n" + wfgen.script(BUF_BREAKING_SCRIPT)),
        ("      - name: Write the proto summary\n"
         "        if: ${{ !cancelled() }}\n" +
         env_block([("PRESENT", "${{ steps.site.outputs.present }}"), ("OTHER", "${{ steps.site.outputs.other }}"),
                    ("PROTO_DIR", "${{ steps.site.outputs.dir }}"), ("LINT", "${{ steps.lint.outputs.result }}"),
                    ("BREAKING", "${{ steps.breaking.outputs.result }}"), ("DEFAULT_BRANCH", "${{ github.event.repository.default_branch }}")]) +
         "        run: |\n" + wfgen.script(PROTO_SUMMARY_SCRIPT.replace("__BUF_VERSION__", BUF_VERSION))),
    ]
    return render_job("proto", steps, timeout=10, perms="    permissions:\n      contents: read", needs=STAGE2_NEEDS, cond=STAGE2_IF)


def shell_job():
    present = "steps.site.outputs.present == 'true'"
    steps = [
        wfgen.checkout_step(),
        ("      - name: Look for shell scripts\n"
         "        id: site\n"
         "        run: |\n" + wfgen.script(SHELL_SITE_SCRIPT)),
        ("      - name: Install shellcheck\n"
         f"        if: {present}\n" + env_block([("SHELLCHECK_VERSION", f'"{SHELLCHECK_VERSION}"'), ("SHELLCHECK_SHA256", f'"{SHELLCHECK_SHA256}"')]) +
         "        run: |\n" + wfgen.script(SHELLCHECK_INSTALL_SCRIPT)),
        ("      - name: Run shellcheck\n"
         "        id: shellcheck\n"
         f"        if: {present}\n" + env_block([("ENFORCE", "${{ inputs.shellcheck-enforce }}")]) +
         "        run: |\n" + wfgen.script(SHELLCHECK_RUN_SCRIPT)),
        ("      - name: Write the shell summary\n"
         "        if: ${{ !cancelled() }}\n" +
         env_block([("PRESENT", "${{ steps.site.outputs.present }}"), ("RESULT", "${{ steps.shellcheck.outputs.result }}")]) +
         "        run: |\n" + wfgen.script(SHELL_SUMMARY_SCRIPT.replace("__SHELLCHECK_VERSION__", SHELLCHECK_VERSION))),
    ]
    return render_job("shell", steps, timeout=10, perms="    permissions:\n      contents: read", needs=STAGE2_NEEDS, cond=STAGE2_IF)


def integration_job(lang):
    """TEST-02: the setup steps of the Tests job (checkout, toolchain, dependencies), then the caller's command. Only when the caller
    sets `integration-command`; the gate expects `skipped` otherwise."""
    tests = Job(SOURCE[lang], "unit-tests")
    names = [s.split("\n")[0] for s in tests.steps]
    cut = names.index("      - name: Run unit tests")
    setup = tests.steps[:cut]
    assert len(setup) >= 3, f"{tests.src}: the steps before 'Run unit tests' changed: {names}"
    steps = setup + [
        "      - name: Run the integration tests\n" + env_block([("COMMAND", "${{ inputs.integration-command }}")]) +
        "        run: |\n" + wfgen.script('bash -euo pipefail -c "$COMMAND"')]
    return render_job("integration", steps, timeout=30, perms="    permissions:\n      contents: read", needs=STAGE2_NEEDS,
                      cond="${{ needs.changes.outputs.code == 'true' && inputs.integration-command != '' }}")


def lang_jobs(lang):
    """Lint, Build, Tests, SCA, SAST of one language, from its stand-alone workflow, and the optional Integration tests job (dict by job id)."""
    fname = SOURCE[lang]
    tools = tool_defaults(lang)
    lint, build, tests = Job(fname, "lint-format"), Job(fname, "build-check"), Job(fname, "unit-tests")
    sca, sast = Job(fname, "sca"), Job(fname, "sast")
    b_steps, t_steps = list(build.steps), list(tests.steps)
    b_timeout, t_timeout = build.timeout(), tests.timeout()

    def fold(job_key, keep_from, names, cond, into, timeout):
        """Append the steps of an optional job (after its checkout/setup steps) to another job's steps."""
        j = Job(fname, job_key)
        extra = j.steps[keep_from:]
        assert len(extra) == len(names) and all(n in s for s, n in zip(extra, names)), f"{j.src}: steps changed: {[s.split(chr(10))[0] for s in j.steps]}"
        into.extend(with_if(s, cond, j.src) for s in extra)
        return timeout + j.timeout()

    if lang == "go":
        # the smoke test needs the binary: build it once (-o) when the smoke test is on, instead of building twice in two jobs
        s = b_steps[2]
        assert "- name: Build\n" in s, build.src
        s = sub(s, "          BUILD_PATH: ${{ inputs.build-path }}\n", "          BUILD_PATH: ${{ inputs.build-path }}\n          SMOKE_PORT: ${{ inputs.smoke-port }}\n", build.src)
        s = sub(s, '          go build "${targets[@]}"',
                '          if [ -n "$SMOKE_PORT" ]; then\n'
                '            go build -o "$RUNNER_TEMP/server" "${targets[@]}" # kept for the smoke test below\n'
                '          else\n'
                '            go build "${targets[@]}"\n'
                '          fi', build.src)
        b_steps[2] = s
        assert Job(fname, "smoke-test").steps[:2] == build.steps[:2], "go-ci: smoke-test no longer starts like build-check"
        # smoke steps keep the default `success()`: they run the binary the Build step just made, so after a failed build they
        # would only add a second, misleading failure. The E2E step does not depend on the unit tests: `!cancelled()` shows its
        # result even when a unit test failed.
        b_timeout = fold("smoke-test", 3, ["Install grpcurl", "Start server and check gRPC health"], "inputs.smoke-port != ''", b_steps, b_timeout)
        t_timeout = fold("e2e-tests", 2, ["Run E2E tests"], "${{ !cancelled() && inputs.e2e-packages != '' }}", t_steps, t_timeout)
    elif lang == "node":
        # same toolchain and `npm run build` as the Build job already did: only the grpcurl and start/health steps are added
        assert Job(fname, "smoke-test").steps[:3] == build.steps[:3], "node-ci: smoke-test no longer starts with the steps of build-check"
        b_timeout = fold("smoke-test", 4, ["Install grpcurl", "Start server and check gRPC health"], "inputs.smoke-port != ''", b_steps, b_timeout)

    jobs = {"lint": lint, "build": build, "test": tests, "sca": sca, "sast": sast}
    steps = {"lint": lint.steps, "build": b_steps, "test": t_steps, "sca": sca.steps, "sast": sast.steps}
    timeouts = {"lint": lint.timeout(), "build": b_timeout, "test": t_timeout, "sca": sca.timeout(), "sast": sast.timeout()}
    if lang == "java":  # v3.8.0: javac -Xlint:all, report only unless java-lint-enforce (ad-service has 6 warnings today)
        steps["lint"] = steps["lint"] + [
            "      - name: Compiler warnings (javac -Xlint:all)\n" + env_block([("ENFORCE", "${{ inputs.java-lint-enforce }}")]) +
            "        run: |\n" + wfgen.script(JAVA_XLINT_SCRIPT)]
        timeouts["lint"] += 5
    out = {}
    for key in ("lint", "build", "test", "sca", "sast"):
        text = steps[key]
        for name, default in tools.items():  # tool versions: the stand-alone default, written in
            text = [s.replace("${{ inputs." + name + " }}", default) for s in text]
        out[key] = render_job(key, text, timeout=timeouts[key], perms=jobs[key].raw("permissions"),
                              needs=STAGE2_NEEDS, cond=STAGE2_IF)
    out["integration"] = integration_job(lang)
    return out


# The image job: build once per source tree, push it for runs that may, scan that exact digest. The steps are shared with
# service-publish.yaml (tools/wfgen.py), which finds the image this job pushed and ships it instead of building again.
def image_steps():
    return [
        wfgen.checkout_step(),
        wfgen.mode_step(),
        wfgen.buildx_step(),
        wfgen.login_step("Log in to ghcr.io (to read the build cache, and to push the image)",
                         cont="${{ steps.mode.outputs.push != 'true' }}",
                         comment="a run that cannot push only needs the cache, and a cache that cannot be read only makes the build slower: "
                                 "its failed login must not fail the pull request"),
        wfgen.cache_step(),
        wfgen.find_step("${{ steps.mode.outputs.push }}"),
        wfgen.buildinfo_step(),
        wfgen.build_step(push="expr", cache_write="when-pushing"),
        wfgen.record_step("${{ steps.mode.outputs.push }}"),
        wfgen.trivy_step("${{ steps.image.outputs.ref }}",
                         "same version, threshold and ignore file as the scan in service-publish.yaml (which scans the same digest again)"),
        wfgen.summary_step(scan=True),
    ]


def image_job():
    perms = ("    permissions:\n      contents: read\n"
             "      packages: write # pushes ghcr.io/<repo>:tree-<hash> for a run that may (same repository, not Dependabot); a fork's token is read-only")
    return render_job("image", image_steps(), timeout=30, perms=perms, needs=STAGE2_NEEDS,
                      cond="${{ needs.changes.outputs.code == 'true' && inputs.image }}")


# The gate. Same contract as before: the only required check. Strict about what must have succeeded, lenient about nothing else.
GATE_SCRIPT = r"""
# Jobs this workflow has, in the order of the graph: the three guards, then the checks (generated per language), and their names.
guards=" __GUARDS__ "
checks=" __CHECKS__ "
declare -A names=(__NAMES__)

# Text that does not come from this script (an unknown job id, a result) is shown with letters, digits and . _ - only.
safe() { printf '%s' "$1" | LC_ALL=C tr -c 'A-Za-z0-9._-' '?' | cut -c1-60; }
label() { echo "${names[$1]:-$(safe "$1")}"; }
words() {
  case "$1" in
    success) echo passed ;;
    failure) echo failed ;;
    cancelled) echo cancelled ;;
    skipped) echo skipped ;;
    *) safe "$1" ;;
  esac
}

stop() { # stop <message for the log> <heading for the summary>: nothing can be judged
  echo "::error::$1"
  echo "### Blocked: $2" >> "$GITHUB_STEP_SUMMARY"
  exit 1
}

case "$IMAGE" in
  true | false) ;;
  *) stop "input image is '$IMAGE', expected true or false" "the input image is neither true nor false" ;;
esac
case "$INTEGRATION" in
  true | false) ;;
  *) stop "integration is '$INTEGRATION', expected true or false" "the integration switch is neither true nor false" ;;
esac

declare -A result
unknown=()
mapfile -t rows < <(jq -r 'to_entries[] | [.key, .value.result] | @tsv' <<< "$NEEDS")
for row in "${rows[@]}"; do
  IFS=$'\t' read -r job res <<< "$row"
  result[$job]=$res
  [ -n "${names[$job]+x}" ] || unknown+=("$job")
done
for job in $guards $checks; do
  [ -n "${result[$job]+x}" ] || stop "job $job is missing from needs" "$(label "$job") is missing from the results the gate received"
done

# 1. The cause first: a job that failed or was cancelled. Everything downstream of a failed guard is skipped by GitHub, and
#    that skip is a consequence, not a finding, so it is not reported as one.
failed=0
unexplained=0
bad_code=0
causes=()
for job in $guards $checks "${unknown[@]}"; do
  case "${result[$job]}" in
    failure | cancelled)
      failed=1
      echo "::error::$job ${result[$job]}"
      causes+=("$job")
      ;;
  esac
done

# 2. What each job should have done. Guards always succeed. Checks succeed when code changed (the image check only if image is on, the
#    integration tests only if the caller gave integration-command); otherwise they are skipped: docs-only change, image off, integration
#    not enabled. Any other result is unexplained.
declare -A want
for job in $guards; do want[$job]=success; done
for job in $checks; do
  on=true
  if [ "$job" = "image" ] && [ "$IMAGE" != "true" ]; then on=false; fi
  if [ "$job" = "integration" ] && [ "$INTEGRATION" != "true" ]; then on=false; fi
  if [ "$CODE" = "true" ] && [ "$on" = "true" ]; then want[$job]=success; else want[$job]=skipped; fi
done
if [ "$failed" = 0 ] && [ "$CODE" != "true" ] && [ "$CODE" != "false" ]; then
  echo "::error::change detection gave '$CODE', expected true or false"
  failed=1
  bad_code=1
fi

table=""
first_unexplained=""
for job in $guards $checks "${unknown[@]}"; do
  res=${result[$job]}
  expected=${want[$job]:-}
  shown_expected=${expected:-(unknown job)}
  printf '%-14s %-10s (expected %s)\n' "$job" "$res" "$shown_expected"
  if [ -z "$expected" ]; then
    cell="$(words "$res") (unknown job)"
  elif [ "$res" = "$expected" ]; then
    cell=$(words "$res")
    if [ "$res" = skipped ]; then
      if [ "$CODE" = "false" ]; then cell="skipped: documentation only"
      elif [ "$job" = "integration" ]; then cell="skipped: not enabled (the input integration-command is empty)"
      else cell="skipped: no image pipeline"; fi
    fi
    if [ "$job" = docs ] && [ "$res" = success ] && [ "$DOCS" = false ]; then cell="passed: no mkdocs.yml, nothing to check"; fi
  elif [ "$failed" = 1 ]; then
    cell=$(words "$res")
  else
    cell="$(words "$res"), expected $(words "$expected")"
  fi
  table+="| $(label "$job") | $cell |"$'\n'
  # Only when nothing failed: otherwise the failure above is the cause and skipped jobs downstream of it are expected.
  if [ "$failed" = 0 ] && [ "$res" != "$expected" ]; then
    echo "::error::$job is '$res', expected '$shown_expected'"
    unexplained=1
    [ -n "$first_unexplained" ] || first_unexplained=$job
  fi
done

# 3. The summary: the verdict first, then what changed, the results, and what the Merge button will do.
blocked=1
if [ "${#causes[@]}" -gt 0 ]; then
  verdict="Blocked: $(label "${causes[0]}") $(words "${result[${causes[0]}]}")"
  [ "${#causes[@]}" -eq 1 ] || verdict+=" (and $((${#causes[@]} - 1)) more)"
elif [ "$bad_code" = 1 ]; then
  verdict="Blocked: change detection gave no valid answer"
elif [ "$unexplained" = 1 ]; then
  verdict="Blocked: $(label "$first_unexplained") ended as $(words "${result[$first_unexplained]}"), which was not expected"
elif [ "$EVENT" = pull_request ]; then
  verdict="Ready to merge"
  blocked=0
else
  verdict="All checks passed"
  blocked=0
fi

{
  echo "### $verdict"
  echo
  case "$CODE" in
    true) echo "Change: **code**. All checks apply."; echo ;;
    false) echo "Change: **documentation only**. Tests, scans and the image build were skipped."; echo ;;
  esac
  echo "| Job | Result |"
  echo "|---|---|"
  printf '%s' "$table"
  if [ "$EVENT" = pull_request ]; then
    echo
    if [ "$blocked" = 1 ]; then
      echo "Merging is blocked: \`ci / gate\` is the required check. The \`merge\` job does not run after a failed run."
    elif [ "$HEAD_REPO" = "$GITHUB_REPOSITORY" ] && [ "$ACTOR" != 'dependabot[bot]' ]; then
      publish=""
      [ "$IMAGE" != true ] || publish=" and start Publish"
      echo "**Merge button:** approve the \`merge\` environment on this run page (Review deployments) to squash-merge this pull request${publish}."
      if [ "$IMAGE" = true ] && [ "$CODE" = false ]; then
        echo "Publish will find only documentation and publish nothing."
      fi
    else
      echo "The \`merge\` job does not run for fork or Dependabot pull requests: use the Merge button on the pull request."
      if [ "$IMAGE" = true ]; then echo "Publish starts from the push to \`main\`."; fi
    fi
  fi
} >> "$GITHUB_STEP_SUMMARY"
exit $((failed + unexplained))
"""


def gate_job(lang):
    names = " ".join(f'[{k}]="{NAMES[k]}"' for k in GUARDS + CHECKS[lang])
    script = (GATE_SCRIPT.replace("__GUARDS__", " ".join(GUARDS)).replace("__CHECKS__", " ".join(CHECKS[lang]))
              .replace("__NAMES__", names))
    body = "\n".join(("          " + l) if l else "" for l in script.strip("\n").split("\n"))
    needs = GUARDS + CHECKS[lang]
    # the integration job exists in the five language files; its switch is the input, true when a command was given
    integration = "${{ inputs.integration-command != '' }}" if "integration" in CHECKS[lang] else "false"
    step = (
        "      - name: Every job passed, or was skipped for a reason we know\n"
        "        env:\n"
        "          NEEDS: ${{ toJSON(needs) }}\n"
        "          IMAGE: ${{ inputs.image }}\n"
        f"          INTEGRATION: {integration}\n"
        "          CODE: ${{ needs.changes.outputs.code }}\n"
        "          DOCS: ${{ needs.docs.outputs.present }}\n"
        "          EVENT: ${{ github.event_name }}\n"
        "          HEAD_REPO: ${{ github.event.pull_request.head.repo.full_name }}\n"
        "          ACTOR: ${{ github.actor }}\n"
        "        run: |\n" + body)
    return render_job("gate", [step], timeout=5, perms="    permissions: {}", needs=needs, cond="${{ always() }}")


def inputs_block(lang):
    text = ""
    if lang != "none":
        src = input_blocks(SOURCE[lang])
        missing = [n for n in DECLARED[lang] if n not in src]
        assert not missing, f"{SOURCE[lang]} has no input {missing}"
        for n in DECLARED[lang]:
            block = src[n]
            for (iname, old), new in DESC_FIX.items():
                if iname == n and old in block:
                    block = block.replace(old, new)
            text += block
    return COMMON_INPUTS + NEW_COMMON_INPUTS + text + (INTEGRATION_INPUT if lang != "none" else "") + (JAVA_LINT_INPUT if lang == "java" else "")


HEADER = """\
name: Service CI ({title})

# GENERATED by tools/gen-service-ci.py - do not edit; change the stand-alone workflow or the generator and run the generator.
#
# The pull-request pipeline of {who}, flat: every job is in this file and nothing is called from here, so the Actions graph shows
# exactly what runs, left to right, with plain names (`ci / Build`):
#
#   {s1}   (no needs: start at t=0)
#   {s2}
#            (need only `Detect changes`; a docs-only change skips them, the guards still run)
#   gate     (needs every job, always runs, names the job that failed first)
#
# The left column means "starts at t=0", not "must pass first": a failed guard does not hide the results of the checks.
# `ci / gate` is the ONLY required check.
{notes}#
# Call it with:  uses: platform-boutique/platform-demo-workflows/.github/workflows/{file}@<sha> # <tag>
# The calling job must grant: {grant}.

on:
  workflow_call:
    inputs:
{inputs}
# Least privilege by default; every job below declares what it needs.
permissions: {{}}

jobs:
"""


def render(lang):
    file = f"service-ci-{lang}.yaml"
    who = "a repository without a language pipeline" if lang == "none" else f"a {TITLE[lang]} app repository"
    s1 = " | ".join(NAMES[k] for k in GUARDS)
    s2 = " | ".join(NAMES[k] for k in CHECKS[lang])
    notes = ""
    if lang == "none":
        notes = ("# `image: false` (product-catalog-db) leaves only the guards and the gate; the image job is then skipped. With the image\n"
                 "# left on this suits a repository whose tests are their own jobs (the developer portal).\n")
    elif lang in ("go", "node"):
        notes = ("# The optional smoke test (input smoke-port) is a step of Build and the E2E tests (input e2e-packages, go) a step of\n"
                 "# Tests: no extra jobs, and the smoke test reuses the build.\n" if lang == "go" else
                 "# The optional smoke test (input smoke-port) is a step of Build: no extra job, and it reuses the build.\n")
    grant = "contents: read and packages: write (the image job pushes the tree image)" + (" and checks: write (the test reporter)" if lang == "dotnet" else "")
    head = HEADER.format(title=TITLE[lang], who=who, s1=s1, s2=s2, notes=notes, file=file, grant=grant, inputs=inputs_block(lang))
    lj = lang_jobs(lang) if lang != "none" else {}
    ordered = {**lj, "proto": proto_job(), "shell": shell_job()}
    jobs = guard_jobs() + [docs_job()] + [ordered[k] for k in CHECKS[lang] if k != "image"] + [image_job(), gate_job(lang)]
    text = head + "\n".join(jobs)
    assert not re.search(r"[ \t]+$", text, re.M), f"{file}: trailing whitespace"
    # every `inputs.<name>` used must be declared in this file
    declared = set(re.findall(r"^      ([a-z0-9-]+):\s*$", head.split("\npermissions:")[0].split("    inputs:\n")[1], re.M))
    used = set(re.findall(r"\binputs\.([a-z0-9-]+)", text.split("\njobs:\n")[1]))
    assert used <= declared, f"{file}: uses undeclared inputs {sorted(used - declared)}"
    return file, text.rstrip("\n") + "\n"


def render_all():
    return dict(render(l) for l in LANGS)


def main():
    for name, text in render_all().items():
        with open(os.path.join(WFDIR, name), "w") as f:
            f.write(text)
        print("wrote", name)


if __name__ == "__main__":
    main()
