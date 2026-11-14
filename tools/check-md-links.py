#!/usr/bin/env python3
"""Markdown link check of an app repository (DOC-12): every relative link of README.md and of the pages under docs/ must point to
a file or directory that exists. Python 3 standard library only.

usage: check-md-links.py [repo-dir]       (default: the current directory)    exit 1 and one line per broken link

  * checked: inline links and images `[text](path)`, reference definitions `[id]: path`; the path is resolved against the directory
    of the page that holds the link (a path that starts with `/` against the repository root, as GitHub does);
  * not checked: http(s)/mailto/... links (they need the network and fail for reasons that are not the repository's), `#anchors`
    (the heading slugs of GitHub and of mkdocs differ), links with a `{{ template }}`;
  * code is not text: fenced code blocks and inline code spans are skipped;
  * the pages are README.md and every `*.md` under `docs_dir` of mkdocs.yml (default `docs`).

This file is the single source: tools/gen-service-ci.py puts it into the `Docs check` job of service-ci-<language>.yaml as written
here, and tools/test-md-links.py tests it.
"""
import os, re, sys
from urllib.parse import unquote

root = os.path.abspath(sys.argv[1] if len(sys.argv) > 1 else ".")

docs_dir = "docs"
if os.path.isfile(os.path.join(root, "mkdocs.yml")):
    with open(os.path.join(root, "mkdocs.yml"), encoding="utf-8") as f:
        m = re.search(r"^docs_dir:\s*['\"]?([^'\"\s#]+)", f.read(), re.M)
    if m:
        docs_dir = m.group(1).rstrip("/")

pages = []
if os.path.isfile(os.path.join(root, "README.md")):
    pages.append("README.md")
for base, dirs, files in os.walk(os.path.join(root, docs_dir)):
    dirs[:] = sorted(d for d in dirs if not d.startswith("."))
    pages += [os.path.relpath(os.path.join(base, n), root) for n in sorted(files) if n.endswith(".md")]

INLINE = re.compile(r"!?\[[^\]]*\]\(\s*(<[^>]*>|[^)\s]*)(?:\s+(?:\"[^\"]*\"|'[^']*'))?\s*\)")
REFDEF = re.compile(r"^ {0,3}\[[^\]]+\]:\s*(<[^>]*>|\S+)")
SCHEME = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*:")
FENCE = re.compile(r"^ {0,3}(```|~~~)")

broken, checked = [], 0
for page in pages:
    with open(os.path.join(root, page), encoding="utf-8", errors="replace") as f:
        lines = f.read().split("\n")
    fence = None
    for n, line in enumerate(lines, 1):
        m = FENCE.match(line)
        if m:
            if fence is None:
                fence = m.group(1)
            elif m.group(1) == fence:
                fence = None
            continue
        if fence is not None:
            continue
        text = re.sub(r"`[^`]*`", "", line)
        targets = [t for t in INLINE.findall(text)]
        r = REFDEF.match(text)
        if r:
            targets.append(r.group(1))
        for t in targets:
            t = t.strip("<>")
            if not t or t.startswith("#") or SCHEME.match(t) or t.startswith("//") or "{{" in t:
                continue
            path = unquote(re.split(r"[#?]", t, 1)[0])
            if not path:
                continue
            full = os.path.join(root, path.lstrip("/")) if path.startswith("/") else os.path.join(root, os.path.dirname(page), path)
            checked += 1
            if not os.path.exists(os.path.normpath(full)):
                broken.append((page, n, t))

for page, n, t in broken:
    print(f"::error file={page},line={n}::broken link: {t}")
    print(f"{page}:{n}: broken link -> {t}")
print(f"markdown links: {checked} relative links in {len(pages)} pages, {len(broken)} broken")
sys.exit(1 if broken else 0)
