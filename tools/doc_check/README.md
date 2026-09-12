# doc_check — the documentation claims a machine can settle

```sh
python3 tools/doc_check/check_docs.py            # report, always exits 0
python3 tools/doc_check/check_docs.py --gate     # exits 1 if anything is found
python3 tools/doc_check/check_docs.py README.md  # only these files
python3 tools/doc_check/check_docs.py --selftest # prove every rule can still fire
python3 tools/doc_check/check_docs.py --only routes,counts
```

Stdlib only, no network, runs nothing it documents. Whole tree in ~0.4 s.

## Why it exists

Five sessions committed to this repo in parallel on 2026-09-12 and the docs went stale
inside the hour, repeatedly. `README.md` and `SCAN_OUTPUT.md` told the reader to compile
their LAN IP into `API.base`, months after the settings sheet started reading it from
`UserDefaults`. A README line describing the URL button was stale twenty minutes after it
was fixed, because a UI pass moved the button. The endpoint table was missing every
`/suitcases` route for a while. `docs/PLAN_3D.md` quoted `boxes=6` where the tool prints
7. Every one of those was caught by a human or an agent noticing in passing, which does
not scale, and the demo script is now a document people read aloud.

This checks the subset a machine can settle, so the subset it cannot gets the attention.

## What it checks

| Rule | The claim | How it is settled |
|---|---|---|
| `paths` | a backticked path that says **where** — `Spike/API.swift`, `tools/plan3d` | it exists, from the repo root, from the doc's own directory, or as the tail of a real path (docs write `fixtures/nested-carry-on.json` after a `cd tools/plan3d`) |
| `links` | `[text](target)` | the target resolves, and for `x.md#anchor` the heading exists in `x.md` |
| `commands` | a command in a fence or backticks | `make <target>` is a real `Makefile` target; `python3 -m <pkg>` is importable from the root or the doc's directory; `bash`/`python3 <script>` exists; `swift build --product <name>` is declared in the nearest `Package.swift`. Nothing is executed — several of these need Mongo, a network, or minutes of solver time. |
| `identifiers` | a backticked identifier on a line that names exactly one source file | it appears in that file, or in a sibling of the same extension in that directory |
| `routes` | `` `POST /items` `` in prose or a table | `server/main.py` declares it — and if a doc mentions 60%+ of the real routes it is an endpoint reference, so every route must appear in it |
| `counts` | six pinned numeric claims | counted from the real source: modules in `physics/`, placements and `container.zones` in the demo `plan.json`, XCTest methods under `packing-core/Tests` and `swift/PackPhysics/Tests`, and `plan3d`'s `boxes=` against placements + 1 |

Findings are one line each, `file:line: [rule] claim — why`.

## What it deliberately does not check, and why that is not laziness

**Claims about the physical world.** "The wheel wells are roughly 10 x 6 x 5 cm."
"LiDAR accuracy is roughly ±1 cm." There is no oracle in the repo. A checker that
pretended to verify these would be inventing an answer, which is worse than silence —
somebody would then trust it.

**Claims about the UI.** "Tap the gear at the top right." A SwiftUI hierarchy does not
tell you what is visually top-right, and a grep for `gearshape` proves the icon exists,
not that the sentence is true. This class rotted twice today and still needs a human with
the app in their hand. See "the one class that needs a human", below.

**Quoted tool output, except where the number has a static source.**
`docs/PLAN_3D.md` pastes `boxes=6 container-first=yes bbox=(60.5,28.0)-(839.5,672.0)`.
Reproducing that line means building Swift and running `plan3d` — minutes and a
toolchain, which is `scripts/pipeline_check.sh`'s job, not a 0.4 s doc linter's — so the
projected `bbox` is not checked. The box count is, because the doc states its own
invariant two paragraphs later ("a box count that does not match placements + 1") and
`plan.json` has the placements. Same for the pasted `Executed N tests` block in
`packing-core/README.md`: the count is a pinned rule, the timings are not.

**Behaviour and capability claims.** "`swift test` does not run on Linux" (false as of
this morning), "Grok is synchronous and takes 5-6 s per item", "the solver packs bounding
boxes only". Each needs the thing run, and some need a network.

**Prose counts in general.** The obvious rule — a number word followed by a countable
noun — matches 40+ lines in these docs and almost all of them are prose: "one status
line", "two items whose boxes overlap", "one container, 4-6 rigid items". A checker that
reports forty things nobody will act on gets switched off, so `counts` is a short pinned
list instead, and each entry names its source. A pinned entry whose source has moved, or
whose claim has been reworded out of the doc, reports **itself** rather than quietly
passing — the rule fails loudly instead of rotting.

**Anything requiring judgement about whether a doc *should* say something.** Missing
documentation is not detectable; wrong documentation is.

## Deliberate blind spots inside the rules it does have

These are precision trades, each one paid for by a false positive it removed:

- A bare basename (`plan.json`, `top.svg`, `main.swift`) is never checked as a path. In
  these docs those are files a tool writes at runtime, and there are 15 `main.swift`s in
  the tree, so resolving one picks a file at random. That was 100+ false positives.
- A path whose first segment is not a real directory is skipped — `out_dir/candidates.json`
  names a CLI argument, not a location.
- The `identifiers` rule needs exactly one file-ish token on the line. `docs/SWIFT_PORT.md`'s
  module map is `` `physics/schema.py` | `Schema.swift` | `ContractTests` ``, where the
  identifier belongs to the Swift column; guessing cost four false positives.
- It searches the named file's whole directory, not just the file, because docs
  cross-reference siblings on one line. A symbol that moved between two files in the same
  package is not reported; a symbol that is *gone* still is.
- An all-caps word with no underscore (`EPS`, `HACKCMU2026`) is not treated as an
  identifier — too many are acronyms, repo names and env vars. `MIN_CONTACT_AREA_M2` has
  the underscore and is checked.
- A documented route whose path shares no segment with any real route is assumed to be
  somebody else's API (`GET /v1/models` is xAI's).
- Known residual, found by pointing the checker at this very file: a sentence that names
  one file and, further along, a symbol from somewhere else still fires. Only proximity
  would fix it, and every proximity threshold tried was arbitrary. If a finding looks like
  a cross-reference, it probably is one — say so here rather than loosening the rule.
- `FIXES.md`, `docs/pan_eval_log.md` and `docs/superpowers/` are skipped. They are
  historical records — a fix log describes the state it fixed, a design spec describes the
  plan it was written from. Reporting their staleness would report the point of the
  document.

## Measured precision

Against the real tree at commit `635218b`: **7 findings, 7 true positives, no false
positives.** Each was verified independently, not taken on the checker's word —
`swift test` was actually run in both Swift packages (102 and 180, against 86 and 178 in
the docs), `plan3d` was actually built and run (`boxes=7`, against `boxes=6` in the doc),
`git log -S` found the commit that deleted the two PAN helpers, and the route was read
straight off the decorators in `server/main.py`.

The tuning history is the honest part: the first version reported **138 findings of which
3 were real (2%)**. Every entry in "deliberately does not check" and every blind spot
below is a measured false-positive class from that run, not a guess.

`--selftest` seeds one doc with a fresh instance of each error class and asserts every
rule fires, so "no findings" can be told apart from "the checker broke". `counts` is
pinned to real doc paths and cannot be seeded that way; it is covered by the four real
findings it makes today.

## The one class that needs a human

**UI affordances.** "Tap the small URL button at the bottom of the control panel" was
stale twenty minutes after it was corrected, and nothing in this repo can tell you that.
The demo script is read aloud on stage, so a wrong affordance is the one kind of staleness
that fails in front of an audience rather than in a test. Someone has to hold the phone
and walk `docs/DEMO_SCRIPT.md` before the demo. No checker will do it.
