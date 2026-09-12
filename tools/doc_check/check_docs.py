#!/usr/bin/env python3
"""Check the documentation claims that a machine can settle.

Five sessions committed to this repo in parallel and the docs went stale inside the
hour, repeatedly: a path that moved, a link to a renamed file, an endpoint table that
missed half the /suitcases routes, an identifier a doc still points at a file that no
longer contains it. Every one of those was caught by a human noticing in passing.

This catches that class and nothing else. What it deliberately does NOT check, and
why that list is not laziness, is in tools/doc_check/README.md.

    python3 tools/doc_check/check_docs.py            # report, always exit 0
    python3 tools/doc_check/check_docs.py --gate     # exit 1 if anything is found
    python3 tools/doc_check/check_docs.py README.md  # just these files
    python3 tools/doc_check/check_docs.py --selftest # prove each rule can fire

Rules, in the order they run: paths, links, commands, identifiers, routes, counts.
--only / --skip take those names.
"""

import argparse
import json
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent.parent

SKIP_DIRS = {".git", ".build", "node_modules", "graphify-out", "out", ".claude", "dist"}

# Historical records, not statements about the tree as it stands: a design spec
# describes the plan it was written from, a fix log describes the state it fixed.
# Checking them reports staleness that is the point of the document.
SKIP_DOCS = {"FIXES.md", "docs/pan_eval_log.md"}
SKIP_DOC_DIRS = {"docs/superpowers"}

SRC_EXT = {".py", ".swift", ".sh", ".js", ".ts"}
PATH_EXT = SRC_EXT | {
    ".md", ".json", ".yml", ".yaml", ".plist", ".toml", ".txt", ".svg", ".png",
    ".h", ".c", ".cpp", ".csv", ".lock", ".cfg", ".ini", ".xcodeproj",
}

# Names a doc writes in backticks next to a file that are language or library
# vocabulary, not something that file has to contain.
NOT_OURS = {
    "ValueError", "TypeError", "KeyError", "RuntimeError", "AssertionError",
    "Optional", "Dict", "List", "Tuple", "Set", "Any", "None", "True", "False",
    "String", "Int", "Double", "Float", "Bool", "Array", "UUID", "Data", "Codable",
    "Decodable", "Encodable", "JSONDecoder", "JSONEncoder", "XCTest", "XCTAssert",
    "SwiftUI", "RealityKit", "ARKit", "CoreGraphics", "Foundation", "numpy", "np",
    "FastAPI", "pymongo", "MongoDB", "Mongo", "JSON", "HTTP", "URL", "SVG", "AR",
    "N/A", "TODO", "NOTE", "_id",
}

STDLIB_MODULES = {"unittest", "pip", "venv", "json", "http", "pytest", "uv"}

NUMWORD = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
    "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13,
    "fourteen": 14, "fifteen": 15, "sixteen": 16, "seventeen": 17, "eighteen": 18,
    "nineteen": 19, "twenty": 20,
}

TICK = re.compile(r"`([^`\n]+)`")
LINK = re.compile(r"\[[^\]]*\]\(([^)\s]+)")
ROUTE_IN_DOC = re.compile(r"^(GET|POST|PATCH|PUT|DELETE)\s+(/[A-Za-z0-9_{}/\-]*)$")
ROUTE_IN_CODE = re.compile(r"^@\w+\.(get|post|patch|put|delete)\(\s*[\"']([^\"']+)")
MAKE_TARGET = re.compile(r"^([A-Za-z0-9_][A-Za-z0-9_.\-]*):", re.M)


class Finding:
    def __init__(self, rule, doc, line, text, why):
        self.rule, self.doc, self.line, self.text, self.why = rule, doc, line, text, why

    def __str__(self):
        return f"{self.doc}:{self.line}: [{self.rule}] {self.text} — {self.why}"


# --------------------------------------------------------------------------- input

def docs_in(root):
    out = []
    for p in sorted(root.rglob("*.md")):
        rel = p.relative_to(root)
        if SKIP_DIRS & set(rel.parts):
            continue
        if str(rel) in SKIP_DOCS or any(str(rel).startswith(d) for d in SKIP_DOC_DIRS):
            continue
        out.append(rel)
    return out


def prose_lines(text):
    """Yield (lineno, line, in_fence). Fenced blocks are marked, not dropped: paths
    and commands are checked inside them, prose rules are not."""
    fence = False
    for n, line in enumerate(text.splitlines(), 1):
        if line.lstrip().startswith("```"):
            fence = not fence
            continue
        yield n, line, fence


# --------------------------------------------------------------------------- paths

def path_like(tok):
    if not tok or " " in tok or "://" in tok or tok.startswith(("http", "#", "/", "-")):
        return False
    if "{" in tok or "}" in tok or "…" in tok or "$" in tok:
        return False
    if not re.fullmatch(r"[A-Za-z0-9_.@*\-]+(/[A-Za-z0-9_.@*\-]+)*/?", tok):
        return False
    ext = pathlib.Path(tok.rstrip("/")).suffix
    if ext in PATH_EXT:
        return True
    # No extension: only a path if its first segment is really a directory here.
    # Kills prose like `and/or`, `1/k`, `rigid`/`soft`.
    return "/" in tok and (ROOT / tok.split("/")[0]).is_dir()


_BY_NAME = {}


def by_name(name):
    """Every path in the tree with this basename, indexed once."""
    if not _BY_NAME:
        for p in ROOT.rglob("*"):
            if SKIP_DIRS & set(p.relative_to(ROOT).parts):
                continue
            _BY_NAME.setdefault(p.name, []).append(p)
    return _BY_NAME.get(name, [])


def resolve(tok, doc):
    """Repo root, then the doc's own directory, then anywhere in the tree.

    The last fallback matters because docs legitimately write a path relative to the
    thing they are about: docs/PLAN_3D.md says `cd tools/plan3d` and then names
    `fixtures/nested-carry-on.json`. Matching any path that ENDS with the token keeps
    that honest without the doc having to spell out the prefix, and a file that was
    actually deleted or renamed still has nothing to match.
    """
    tok = tok.rstrip("/")
    for base in (ROOT, ROOT / doc.parent):
        cand = base / tok
        if "*" in tok:
            if list(base.glob(tok)):
                return cand
        elif cand.exists():
            return cand
    if "*" not in tok:
        for hit in by_name(pathlib.Path(tok).name):
            if str(hit).endswith("/" + tok):
                return hit
    return None


def first_segment_is_real(tok, doc):
    head = tok.split("/")[0]
    return (ROOT / head).is_dir() or (ROOT / doc.parent / head).is_dir()


def check_paths(doc, text, out):
    for n, line, _ in prose_lines(text):
        for tok in TICK.findall(line):
            tok = tok.strip()
            # A bare basename is a name, not a location: `plan.json` and `top.svg` in
            # these docs are files a tool writes at runtime, and reporting them was
            # this rule's whole false-positive budget. Only paths that say WHERE, and
            # only ones that start somewhere real — `out_dir/candidates.json` names a
            # CLI argument, not a directory.
            if "/" not in tok or not first_segment_is_real(tok, doc):
                continue
            if path_like(tok) and resolve(tok, doc) is None:
                out.append(Finding("path", doc, n, tok, "no such file or directory"))


# --------------------------------------------------------------------------- links

def slug(heading):
    h = heading.strip().lstrip("#").strip().lower()
    h = h.replace("`", "")
    h = re.sub(r"[^\w\s\-]", "", h, flags=re.U)
    return re.sub(r"\s+", "-", h.strip())


def headings(path):
    try:
        body = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return set()
    return {slug(l) for l in body.splitlines() if l.startswith("#")}


def check_links(doc, text, out):
    for n, line, fence in prose_lines(text):
        if fence:
            continue
        # A link inside backticks is an example of link syntax, not a link. Blanking
        # with equal-length spaces keeps [`OVERVIEW.md`](OVERVIEW.md) matching.
        line = TICK.sub(lambda m: " " * len(m.group(0)), line)
        for target in LINK.findall(line):
            if target.startswith(("http", "mailto:", "tel:")):
                continue
            file_part, _, anchor = target.partition("#")
            if file_part:
                hit = resolve(file_part, doc)
                if hit is None:
                    out.append(Finding("link", doc, n, target, "link target missing"))
                    continue
            else:
                hit = ROOT / doc
            if anchor and hit.suffix == ".md" and slug(anchor) not in headings(hit):
                out.append(Finding("link", doc, n, target, "no such heading in target"))


# ------------------------------------------------------------------------ commands

def make_targets():
    mk = ROOT / "Makefile"
    return set(MAKE_TARGET.findall(mk.read_text())) if mk.exists() else set()


def package_names(doc):
    """Names declared in the Package.swift nearest the doc (its dir, then up)."""
    for d in [ROOT / doc.parent] + list((ROOT / doc.parent).parents):
        pkg = d / "Package.swift"
        if pkg.exists():
            return set(re.findall(r'name:\s*"([^"]+)"', pkg.read_text()))
        if d == ROOT:
            break
    return None


def module_exists(mod, doc):
    top = mod.split(".")[0]
    # Either importable from the repo root, or from the directory the doc lives in
    # (packer3d/README.md documents `python3 -m packer3d.cli`, run from packer3d/).
    for base in (ROOT, ROOT / doc.parent):
        if (base / top / "__init__.py").exists() or (base / f"{top}.py").exists():
            return True
    return False


def check_commands(doc, text, out):
    targets = make_targets()
    pkg = None
    for n, line, fence in prose_lines(text):
        # A command claim only counts inside a fence or backticks; bare prose says
        # "make them pass" and "make container semi-transparent".
        chunks = [line] if fence else TICK.findall(line)
        for chunk in chunks:
            for m in re.finditer(r"\bmake\s+([a-z][a-z0-9_.\-]*)", chunk):
                if m.group(1) not in targets:
                    out.append(Finding("command", doc, n, f"make {m.group(1)}",
                                       "no such target in Makefile"))
            for m in re.finditer(r"\bpython3?\s+-m\s+([A-Za-z_][A-Za-z0-9_.]*)", chunk):
                mod = m.group(1)
                if mod not in STDLIB_MODULES and not module_exists(mod, doc):
                    out.append(Finding("command", doc, n, f"python -m {mod}",
                                       "no such importable package at the repo root"))
            for m in re.finditer(r"\b(?:bash|sh|python3?)\s+([A-Za-z0-9_./\-]+\.(?:sh|py))",
                                 chunk):
                if resolve(m.group(1), doc) is None:
                    out.append(Finding("command", doc, n, m.group(0),
                                       "script does not exist"))
            for m in re.finditer(r"--(?:product|target)\s+([A-Za-z_][A-Za-z0-9_]*)", chunk):
                if pkg is None:
                    pkg = package_names(doc)
                if pkg and m.group(1) not in pkg:
                    out.append(Finding("command", doc, n, m.group(0),
                                       "not declared in the nearest Package.swift"))


# ----------------------------------------------------------------------- identifiers

def identifier_like(tok):
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)*(\(\))?", tok):
        return False
    bare = tok.rstrip("()")
    if bare in NOT_OURS or any(p in NOT_OURS for p in bare.split(".")):
        return False
    # Must look like code, not an English word a doc happens to backtick (`height`,
    # `position`, `k`) and not an acronym or a name in caps (`HACKCMU2026`, `EPS`).
    # snake_case, camelCase and PascalCase all survive; SHOUTY_CONSTANTS have the
    # underscore. A bare all-caps word does not, which is the price of that.
    return "_" in bare or (bare != bare.lower() and bare != bare.upper())


def check_identifiers(doc, text, out):
    for n, line, fence in prose_lines(text):
        if fence:
            continue
        toks = [t.strip() for t in TICK.findall(line)]
        # Only an explicit path counts as "the doc says it is in this file". A bare
        # `geometry.py` or `main.swift` is a cross-reference, and there are several of
        # each in this tree — resolving one picks a file at random and reports on it.
        named = [t for t in toks if pathlib.Path(t).suffix in PATH_EXT or path_like(t)]
        files = [t for t in named
                 if "/" in t and path_like(t) and "*" not in t
                 and pathlib.Path(t).suffix in SRC_EXT and resolve(t, doc) is not None]
        # Exactly one file on the line, or the pairing is a guess. docs/SWIFT_PORT.md's
        # module map is `physics/schema.py` | `Schema.swift` | `ContractTests` — the
        # identifier belongs to the Swift column, and guessing cost four findings.
        if len(files) != 1 or len(named) != 1:
            continue
        target = resolve(files[0], doc)
        body = target.read_text(encoding="utf-8", errors="replace")
        # Docs cross-reference: "`validate_packer3d` (`physics/packer3d_adapter.py`)
        # -> prints `result_to_json`" names a symbol from a sibling module on the same
        # line. Searching the whole directory keeps that quiet and still catches the
        # thing worth catching: a symbol the docs send you to that is simply gone.
        nearby = "".join(
            p.read_text(encoding="utf-8", errors="replace")
            for p in sorted(target.parent.iterdir())
            if p.is_file() and p.suffix == target.suffix
        )
        for tok in toks:
            if tok in files or path_like(tok) or not identifier_like(tok):
                continue
            for part in tok.rstrip("()").split("."):
                if not re.search(rf"\b{re.escape(part)}\b", nearby):
                    where = f"{files[0]}, or anywhere in {target.parent.relative_to(ROOT)}/"
                    out.append(Finding("identifier", doc, n, tok,
                                       f"{part!r} does not appear in {where}"))
                    break


# --------------------------------------------------------------------------- routes

def declared_routes():
    main = ROOT / "server" / "main.py"
    if not main.exists():
        return None
    found = set()
    for line in main.read_text().splitlines():
        m = ROUTE_IN_CODE.match(line.strip())
        if m:
            found.add((m.group(1).upper(), re.sub(r"\{[^}]*\}", "{}", m.group(2))))
    return found


def check_routes(doc, text, out):
    real = declared_routes()
    if not real:
        return
    # The docs also quote other people's HTTP APIs (`GET /v1/models` is xAI's). Ours
    # are the ones that share a path segment with something server/main.py declares.
    ours = {s for _, p in real for s in p.split("/") if s and s != "{}"}
    mentioned = set()
    for n, line, _ in prose_lines(text):
        for tok in TICK.findall(line):
            m = ROUTE_IN_DOC.match(tok.strip())
            if not m:
                continue
            route = (m.group(1), re.sub(r"\{[^}]*\}", "{}", m.group(2)).rstrip("/") or "/")
            if not ours & {s for s in route[1].split("/") if s and s != "{}"}:
                continue
            mentioned.add(route)
            if route not in real:
                out.append(Finding("route", doc, n, tok,
                                   "server/main.py declares no such route"))
    # Only a doc that is already an endpoint reference owes full coverage. One that
    # mentions two routes in passing does not.
    if len(mentioned & real) >= 0.6 * len(real):
        for route in sorted(real - mentioned):
            out.append(Finding("route", doc, 0, f"{route[0]} {route[1]}",
                               "declared in server/main.py, missing from this doc"))


# --------------------------------------------------------------------------- counts

def _physics_modules():
    d = ROOT / "physics"
    return len([p for p in d.glob("*.py") if not p.name.startswith("__")]) if d.is_dir() else None


def _swift_tests(pkg):
    d = ROOT / pkg / "Tests"
    if not d.is_dir():
        return None
    # Verified against the real runs on this checkout: `swift test` executed exactly
    # 102 for packing-core and 180 for swift/PackPhysics, and this count is 102/180.
    # Only valid while these packages are XCTest-only; a swift-testing @Test would
    # not be counted, so the guard below refuses to answer if one appears.
    src = "".join(p.read_text(errors="replace") for p in d.rglob("*.swift"))
    if "@Test" in src:
        return None
    return len(re.findall(r"^\s*func test\w*\(", src, re.M))


def _demo_plan(key):
    p = ROOT / "packing-core/Sources/PackingPlan/Resources/plan.json"
    if not p.exists():
        return None
    plan = json.loads(p.read_text())
    if key == "zones":
        return len(plan.get("container", {}).get("zones", []))
    return len(plan.get("placements", []))


# Counts worth checking are the ones with an exact source. A generic "number word
# followed by a countable noun" scan over these docs matches 40+ lines, nearly all
# of them prose ("one status line", "two items whose boxes overlap"), so the
# generic rule is not worth having. These are pinned instead; a pinned entry whose
# source has moved reports itself rather than passing.
PINNED = [
    ("docs/PHYSICS.md", r"([A-Za-z]+|\d+) modules,", _physics_modules,
     "modules in physics/ (excluding __init__/__main__)"),
    ("packing-core/README.md", r"([A-Za-z]+|\d+) items, ([A-Za-z]+|\d+) zones",
     lambda: (_demo_plan("placements"), _demo_plan("zones")),
     "placements and container.zones in the demo plan.json"),
    ("packing-core/CLAUDE.md", r"([A-Za-z]+|\d+) items in ([A-Za-z]+|\d+) zones",
     lambda: (_demo_plan("placements"), _demo_plan("zones")),
     "placements and container.zones in the demo plan.json"),
    # No pin for packing-core/README.md's test count: the doc deliberately stopped quoting one.
    # That suite grew 74 -> 86 -> 90 -> 102 -> 105 in a single morning, so a pinned number there
    # meant this checker's only job was telling someone to retype it. A doc that says "green"
    # and shows `Executed <n> tests` cannot rot. Keep the pin below for swift/PackPhysics, which
    # is a stable ported suite and where a moving count is genuinely news.
    ("docs/SWIFT_PORT.md", r"(\d+) tests total",
     lambda: _swift_tests("swift/PackPhysics"),
     "XCTest methods under swift/PackPhysics/Tests"),
    # The only number in a pasted tool-output block with a static source: the doc's own
    # stated invariant two paragraphs down is "a box count that does not match
    # placements + 1" is a regression, and plan3d confirmed it prints 7 for this plan.
    ("docs/PLAN_3D.md", r"boxes=(\d+)", lambda: _demo_plan("placements") + 1,
     "placements in the demo plan.json + 1, the container box"),
]


def as_int(word):
    w = word.strip().lower()
    return int(w) if w.isdigit() else NUMWORD.get(w)


def check_counts(doc, text, out):
    for target_doc, pattern, truth_fn, label in PINNED:
        if str(doc) != target_doc:
            continue
        truth = truth_fn()
        truth = truth if isinstance(truth, tuple) else (truth,)
        if any(t is None for t in truth):
            out.append(Finding("count", doc, 0, label,
                               "cannot count it any more — the source moved, so this "
                               "claim is now unchecked"))
            continue
        hits = 0
        # Fences included on purpose: packing-core/README.md pastes the real
        # `Executed N tests` line, which is exactly as stale as the prose above it.
        for n, line, _fence in prose_lines(text):
            m = re.search(pattern, line)
            if not m:
                continue
            hits += 1
            claimed = tuple(as_int(g) for g in m.groups())
            if claimed != truth:
                out.append(Finding("count", doc, n, m.group(0),
                                   f"{label} is {', '.join(str(t) for t in truth)}"))
        if not hits:
            out.append(Finding("count", doc, 0, label,
                               "the claim this rule pins is no longer in the doc — "
                               "reword the rule or drop it"))


RULES = {
    "paths": check_paths, "links": check_links, "commands": check_commands,
    "identifiers": check_identifiers, "routes": check_routes, "counts": check_counts,
}


# ----------------------------------------------------------------------------- main

def run(docs, rules):
    out = []
    for doc in docs:
        text = (ROOT / doc).read_text(encoding="utf-8", errors="replace")
        for name in rules:
            RULES[name](doc, text, out)
    return out


def selftest():
    """Each rule against a doc seeded with exactly the staleness that happened."""
    import tempfile
    # An endpoint table with every real route but one, so the coverage half of the
    # route rule -- the one the root README's missing /suitcases rows needed -- fires.
    real = sorted(declared_routes())
    dropped = real[0]
    table = "\n".join(f"| `{m} {p.replace('{}', '{id}')}` | listed |" for m, p in real[1:])
    seeded = f"""# Seeded
Compile your IP into `API.nosuchfield` in `Spike/API.swift`.
The scanner lives in `Spike/NoSuchFile.swift`.
See [the missing one](docs/NOPE.md) and [a bad anchor](README.md#not-a-heading).
Run `make nosuchtarget` and `python3 -m nosuchpkg` and `bash scripts/nope.sh`.
| `POST /suitcases/{{id}}/nosuch` | invented |
{table}
"""
    expect = {"identifier": "API.nosuchfield", "path": "Spike/NoSuchFile.swift",
              "link": "README.md#not-a-heading", "command": "make nosuchtarget",
              "route": "POST /suitcases/{id}/nosuch",
              "route (coverage)": f"{dropped[0]} {dropped[1]}"}
    with tempfile.TemporaryDirectory(dir=ROOT) as d:
        doc = pathlib.Path(d, "seeded.md")
        doc.write_text(seeded)
        got = run([doc.relative_to(ROOT)], list(RULES))
    by_rule = {}
    for f in got:
        by_rule.setdefault(f.rule, []).append(f.text)
    ok = True
    for rule, needle in expect.items():
        hit = needle in by_rule.get(rule.split(" ")[0], [])
        print(f"  {'PASS' if hit else 'FAIL'}  {rule:11} expected to catch {needle}")
        ok &= hit
    for f in got:
        print(f"    seeded finding: {f}")
    print("selftest:", "all rules fire" if ok else "A RULE DID NOT FIRE")
    return 0 if ok else 1


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("docs", nargs="*", help="markdown files (default: the whole tree)")
    ap.add_argument("--gate", action="store_true", help="exit 1 if anything is found")
    ap.add_argument("--only", help="comma-separated rule names")
    ap.add_argument("--skip", help="comma-separated rule names")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()

    if args.selftest:
        return selftest()

    rules = args.only.split(",") if args.only else list(RULES)
    if args.skip:
        rules = [r for r in rules if r not in args.skip.split(",")]
    bad = [r for r in rules if r not in RULES]
    if bad:
        sys.exit(f"unknown rule(s): {', '.join(bad)}. known: {', '.join(RULES)}")

    docs = ([pathlib.Path(d).resolve().relative_to(ROOT) for d in args.docs]
            if args.docs else docs_in(ROOT))
    found = run(docs, rules)

    for f in sorted(found, key=lambda f: (str(f.doc), f.line)):
        print(f)
    by_rule = {}
    for f in found:
        by_rule[f.rule] = by_rule.get(f.rule, 0) + 1
    tail = ", ".join(f"{k} {v}" for k, v in sorted(by_rule.items())) or "nothing"
    print(f"\n{len(docs)} docs, {len(found)} finding(s): {tail}")
    return 1 if (args.gate and found) else 0


if __name__ == "__main__":
    raise SystemExit(main())
