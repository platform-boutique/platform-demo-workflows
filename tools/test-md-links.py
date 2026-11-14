#!/usr/bin/env python3
"""Tests of tools/check-md-links.py (the markdown link check of the `Docs check` job), run as the workflow runs it: a scratch repository,
`python3 -` fed the script text. Needs python3.   usage: tools/test-md-links.py     exit 1 and one line per failed scenario"""
import os, subprocess, sys, tempfile

SCRIPT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "check-md-links.py")
failures, count = [], 0


def repo(files):
    d = tempfile.mkdtemp(prefix="test-md-links-")
    for name, text in files.items():
        p = os.path.join(d, name)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w") as f:
            f.write(text)
    return d


def run(files, name, ok, contains=(), absent=()):
    global count
    count += 1
    d = repo(files)
    p = subprocess.run([sys.executable, "-I", SCRIPT, d], capture_output=True, text=True)
    out = p.stdout + p.stderr
    problems = []
    if (p.returncode == 0) != ok:
        problems.append(f"exit code {p.returncode}, expected {'0' if ok else '1'}")
    problems += [f"missing {s!r}" for s in contains if s not in out]
    problems += [f"unexpected {s!r}" for s in absent if s in out]
    if problems:
        failures.append(f"{name}: {'; '.join(problems)}\n{out}")


run({"README.md": "[a](docs/a.md) ![img](docs/i.png \"title\") [d](docs/)\n", "docs/a.md": "[back](../README.md) [sib](b.md#top)\n", "docs/b.md": "x\n", "docs/i.png": ""},
    "valid relative links, an image with a title, a directory, a sibling with an anchor", True, contains=["0 broken"])
run({"README.md": "[gone](docs/gone.md)\n", "docs/a.md": "x\n"}, "a missing file in the README", False, contains=["README.md:1: broken link -> docs/gone.md", "::error file=README.md,line=1::"])
run({"docs/a.md": "ok\n\n[x](../proto/x.proto)\n"}, "a link out of docs/ to a file that does not exist", False, contains=["docs/a.md:3: broken link -> ../proto/x.proto"])
run({"docs/a.md": "[x](../proto/x.proto)\n", "proto/x.proto": ""}, "a link out of docs/ to a file that exists", True)
run({"README.md": "[w](https://example.invalid/x) [m](mailto:a@b.c) [t](tel:1) [p](//cdn/x) [h](#heading) [t](docs/{{ name }}.md)\n"},
    "URLs, anchors and templates are not checked", True, contains=["0 relative links"])
run({"README.md": "[a](docs/a.md#no-such-heading)\n", "docs/a.md": "# Other\n"}, "an anchor is not checked, the file is", True)
run({"README.md": "```\n[x](nope.md)\n```\n\n~~~sh\n[y](nope2.md)\n~~~\nUse `[z](nope3.md)` here.\n"}, "code blocks and inline code are skipped", True, contains=["0 relative links"])
run({"README.md": "```\n[x](nope.md)\n~~~\n[y](nope2.md)\n```\n[after](missing.md)\n"}, "a fence is closed by its own marker only", False,
    contains=["README.md:6: broken link -> missing.md"], absent=["nope"])
run({"README.md": "[ref]: docs/a.md\n[bad]: docs/zz.md \"Title\"\n", "docs/a.md": "x\n"}, "reference definitions", False, contains=["README.md:2: broken link -> docs/zz.md"], absent=["README.md:1"])
run({"README.md": "[a](<docs/my file.md>) [b](docs/other%20file.md) [c](docs/none%20here.md)\n", "docs/my file.md": "", "docs/other file.md": ""},
    "angle brackets and percent-encoding", False, contains=["-> docs/none%20here.md"], absent=["my file"])
run({"README.md": "[a](/docs/a.md) [b](/docs/zz.md)\n", "docs/a.md": ""}, "a path that starts with / is resolved against the repository root", False, contains=["-> /docs/zz.md"], absent=["-> /docs/a.md"])
run({"mkdocs.yml": "site_name: x\ndocs_dir: site-src\n", "site-src/a.md": "[x](gone.md)\n", "docs/b.md": "[y](gone2.md)\n"}, "docs_dir of mkdocs.yml is honoured", False,
    contains=["site-src/a.md:1"], absent=["docs/b.md"])
run({"docs/a/b/c.md": "[up](../../a.md) [bad](../x.md)\n", "docs/a.md": ""}, "relative to the page, nested", False, contains=["docs/a/b/c.md:1: broken link -> ../x.md"], absent=["-> ../../a.md"])
run({"README.md": "no links\n"}, "no docs directory at all", True, contains=["0 relative links in 1 pages"])
run({}, "an empty repository", True)

if failures:
    print("\n\n".join(failures))
    print(f"\n{len(failures)} of {count} link-check scenarios failed")
    sys.exit(1)
print(f"md-links: {count} scenarios ok")
