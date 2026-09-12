# Looking at the plan views on Linux

Both of them: the interactive 3D view, and — via `--layers` — the 2D top-down layer
diagram that is the demo's fallback. Neither can be built on this box; `tools/plan3d`
renders what they render, to SVG.

## What the 3D view is

`packing-core` projects a `PackingPlan` into an interactive 3D-looking diagram of the
packed bag: the container drawn as an open box you look into, and every placement drawn
as a shaded solid inside it, back to front, with a step reveal.

The maths lives in `packing-core/Sources/PackingPlan/PlanProjection.swift` — an
orthographic camera (`PlanCamera`: yaw, pitch, zoom) that returns `[ProjectedBox]`
already sorted back to front, each with visible faces, a silhouette, a label anchor and
a depth key. The **view** that consumes it is SwiftUI, so it cannot be built — let alone
looked at — on this Linux box.

`tools/plan3d` exists for exactly that reason. It is a command-line renderer that takes
the same `[ProjectedBox]` the SwiftUI view takes and writes plain SVG. It is not a
second implementation of the projection: it calls `projected(camera:size:upTo:)` and
draws what comes back. If the SVG is wrong, the projection is wrong.

## Regenerating the SVGs

```sh
source swift/PackPhysics/swiftenv.sh          # Swift 6.3.3 via swiftly, + libxml2 compat
cd tools/plan3d
swift build --scratch-path /tmp/plan3d            # `.build` is contended when agents overlap
/tmp/plan3d/debug/plan3d <plan.json> <out-dir> \
    [--steps] [--cutaway] [--unpacked] [--violations] [--layers [--device]]
```

Both plan shapes are accepted: the raw `PackingPlan` document, and the server document
that wraps it under a `plan` key (what `GET /suitcases/{id}/plan` returns). Two plans worth
rendering: `packing-core/Sources/PackingPlan/Resources/plan.json`, the bundled demo carry-on,
and `fixtures/nested-carry-on.json`, which is that plan plus nested placements (see below).

Three cameras are written every run — `three-quarter.svg` (the default over-the-shoulder
view), `front.svg` (yaw 0, pitch 0, straight at the far wall) and `top.svg` (pitch 1.4,
nearly straight down). `--steps` additionally writes `step-01.svg …` at the default
camera, one per packing step, so the reveal sequence can be flipped through.

`--layers` is a different mode, not a flag on those: it writes `layer-01.svg …`, the 2D
top-down diagram, and no cameras. `--device` renders that diagram at the phone's scale
instead of a comfortable one. See **The 2D fallback** below.

Write the output to a scratch directory. **Do not commit the SVGs** — they are a
verification artefact, not a build product.

Each file prints one line:

```
three-quarter.svg  boxes=7 container-first=yes bbox=(36.0,54.6)-(864.0,645.4)
```

That is enough to catch a regression without opening anything: a box count that does not
match placements + 1, a container that is no longer element 0 (it must be, or it paints
over the items), or a projected bounding box that has drifted, collapsed to a point or
gone NaN.

## The numbers on the picture

Every SVG carries a header band above the scene — its own strip, not an overlay, because
stats painted over the container's top wall become unreadable exactly when the bag is full
and you most want to read them. It holds, in order:

```
53% full · 5 layers · 89% of the floor used
Biggest free block 40.6 × 8.2 × 18.0 cm — 34% of the free space
First in: Running shoes · Last in: Passport pouch · Highest in the bag: Toiletry kit · …
Nested: socks-pair in Running shoes, charger-pouch in Toiletry kit
Layer 1 · 4 items · 14.0 cm thick · 89% of the floor            (--cutaway only)
…
```

All of it comes from `PlanStats` — `fullnessText`, `layerCountText`, `floorCoverageText`,
`gapText`, `orderText`, and `PlanStats.Layer.summaryText` for the per-layer rows. **Nothing
here is computed in `plan3d`**, formatting included; if a number looks wrong, the bug is in
`PlanStats.swift` and both this and the SwiftUI view have it. `PlanStats` is always built
from the plan *as loaded*, never from the `--cutaway` copy, which grows the container and
would report a smaller fill fraction and a free block that does not exist.

The per-layer rows are in the header rather than pinned to each separated slab. Pinning was
tried first and lied: the highest screen point of a layer is its *rear* corner, which the
layer above stands in front of, so the captions came out in the wrong vertical order and
two of them landed on the same pixels. The header list reads 1…n against a picture that
stacks 1…n bottom-up.

## Seeing into a full bag, and what the plan leaves out

Three flags. They are modes, not extra files: they change all three camera SVGs (and the
`--steps` ones), so render to a second out-dir if you want the plain view to compare
against. Each active mode is named in the SVG's title line.

**`--cutaway` — pull the layers apart.** A full bag is an opaque brick: the near items hide
the far ones and the second layer hides the first. The layers are `PlanStats.layerStats()` —
the library's own floor grouping, with its 5 mm tolerance, so the slabs the picture separates
are exactly the layers the header captions name — and each is lifted until it clears the
tallest item of the layer below plus a margin. A fixed gap would let a lifted layer straddle
a bottle standing on the one under it and hide it worse than before. The container is grown
by the same total, so the scale-to-fit still frames everything and an item still reads as
inside the walls.

There is nothing to cut away on the container itself: `projected()` already draws it from
the inside, near walls culled. And a plan whose placements all sit on the floor has one
layer and nothing to separate — the run says so, and the SVG is the plain view with a
different title. That is correct, not a broken flag.

**`--unpacked` — the shelf of leftovers.** A `PackingPlan` carries only what fits. The
solver's own result says what it left out and why, at `solver.unpacked` in the server
document, and that is where this reads from: a greyed, labelled shelf down the right-hand
side, one card per item with the solver's reason. If the document has no `solver.unpacked`
(a raw plan file), or the solver left nothing out, the shelf is not drawn at all and the run
says which — an empty shelf would claim the solver fit everything.

**`--violations` — outline the bad items in red.** From `validation.violations` in the
server document, keyed by the item each entry names (an entry naming a pair, like
`OBJECT_COLLISION`, marks both). The red outline and the violation type are drawn in one
pass *after* every box, deliberately out of painter order: an offender buried under three
other items is exactly the case you need to see, and marking it in depth order would leave
it half-covered. A violation naming an item that is not in the plan is listed on the
terminal and not drawn.

Both fixtures the tool was checked against had `validation.violations` empty, so the red
path was exercised against a copy with violations pasted in. If you touch it, do the same.

## Nested placements

A placement may declare `"nestedIn": {"itemId": …, "cavity": {"position", "size"}}` — the
solver put this item inside that host's scanned cavity, socks in a shoe. The contract is in
`packing-core/CLAUDE.md`; read it before touching this. There is no flag: nesting is a
property of the plan, so it is always drawn.

`Placement` has no property for the field — packing-core's Swift model does not decode it —
so `plan3d` reads it out of the raw JSON, the same side-car read it already does for
`solver.unpacked` and `validation.violations`, and it works on both document shapes.

**When nothing is nested, nothing changes.** Every producer in the tree emits `null` today
(`server/app_plan.py`'s `_nesting()`), so the normal case is the render you already know,
plus the header band. The run says so once:

```
nesting: no placement declares nestedIn — no cavities drawn, every item a plain solid
```

An absent key and a `null` are the same thing and are not worth a line each.

`tools/plan3d/fixtures/nested-carry-on.json` is the fixture that does exercise it: the
bundled demo carry-on plus three placements, one per branch — a good nest, a nest whose item
pokes out of its cavity, and one naming a host that is not in the plan. It is a copy of
`packing-core/Sources/PackingPlan/Resources/plan.json` rather than an edit of it, because
that file belongs to packing-core.

**How a nest is drawn.**

* The **host becomes an open shell**: its fills drop to 0.28 opacity and its silhouette
  stroke goes light. `ProjectedBox.faces` holds only the faces turned *toward* the camera,
  so fading them is precisely "the near faces dropped" — you look through the near wall into
  the cavity. The silhouette stays because it is what carries the containment read, which is
  the whole thing the picture exists to check; a host erased to nothing would show you the
  nested item and tell you nothing about whether it is inside anything.
* The **cavity cell is outlined in dashed cyan**, drawn *after* every box for the same
  reason the red violation outlines are: the nested item fills the cell, so an outline drawn
  in painter order would be hidden by the very item it bounds. The cell is projected by
  handing it to `projected(camera:size:upTo:)` as a placement of the same container — the
  fit depends only on the container and the canvas, so it lands in the same screen space and
  no projection maths is duplicated here.
* The **relationship is labelled** under the cell — `in Running shoes` and
  `cavity 10.0 × 6.0 × 10.0 cm` (`PlanStats.sizeText`) — with a dashed leader to the host's
  centre, and the header band lists every nest. A host's own step label moves from its centre
  to its top rim, because its centre is where the nest and the cavity caption now live.
* The **nested item is pulled in front of its host** in the draw order. It has to be: a
  nested item interpenetrates its host, `nearer()` returns nil for that pair on purpose, and
  the library's painter sort falls back to centre depth — a coin flip for two near-concentric
  boxes. It is moved to immediately after the host, so it inherits the host's place and
  anything genuinely in front of the host still paints over it.
* Under `--cutaway`, a **nested item rides with its host** instead of with its own floor
  height. It rests in a cavity, not on a shelf, so lifting it by its own layer would tear the
  nest apart. Its `PlanStats` layer is usually still a different one — a cavity floor is a
  floor height like any other — so the header caption for that layer says
  `· 1 nested, drawn with the host`, and the run prints which item that is.

**The failure is drawn as a failure.** The field is an assertion, and a consumer that trusts
it without checking the cell relabels any collision the solver missed as a feature. So the
renderer verifies it and flags, in the same red as `--violations` but *not* gated on that
flag (that flag reads someone else's verdict out of the document; this is the renderer
refusing to draw a claim it just disproved):

| what | flag |
| --- | --- |
| item pokes outside its declared cavity | `OUTSIDE CAVITY by 1.0 cm on x` |
| the cavity is not inside the host | `CAVITY OUTSIDE <host>` |
| `nestedIn` names an item not in the plan | `NESTED IN MISSING '<host>'` |

The other rejections in the contract — a `nestedIn` with no `itemId`, one with no usable
cavity cell, a self-reference, a cycle — are "not nested": no cavity is drawn, the item is a
plain solid, and the run prints one line saying which and why. Only the dangling host is
drawn, because the contract says that one additionally raises its own issue.

Finally, a verified nest is annotated in the geometry line rather than dropped from it:

```
geometry issues: shoes-pair overlaps socks-pair by (0.080, 0.050, 0.080) m  [declared nest,
inside the cavity]; toiletry-kit overlaps charger-pouch by (0.060, 0.060, 0.080) m
```

`geometryIssues()` knows nothing about nesting and correctly calls both pairs an overlap.
Dropping the first would hide a real one the day the cavity check breaks; leaving it
unexplained trains you to skim the line that reports the second.

## The 2D fallback — `--layers`

**What it is for.** `PlanDiagramView` is the view PackAR falls back to when there is no
AR session, no plane detection or no camera permission — the one that has to work on
stage. It is SwiftUI, nobody on this team has a Mac, and so until this mode existed
nobody had ever seen it. `--layers` is how anyone without a Mac checks the demo fallback
before relying on it.

```sh
/tmp/plan3d/debug/plan3d packing-core/Sources/PackingPlan/Resources/plan.json /tmp/out --layers
```

One SVG per layer, `layer-01.svg` upward, plus one summary line each on the terminal:

```
layer-01.svg  Layer 1 · 4 items · 14.0 cm thick · 89% of the floor · nothing from below
layer-02.svg  Layer 2 · 1 item · 4.5 cm thick · 19% of the floor · 3 up through the floor:
              Running shoes, Toiletry kit, Rolled sweater
```

**It is not a second layout.** `plan3d` links `PackingPlanUI` — that target compiles on
Linux because both its SwiftUI and its CoreGraphics imports are guarded, which is also
why `swift test` runs here — and calls exactly what the view calls:
`plan.layers()` for the grouping, `plan.protrusions(into:)` for the items from lower
layers that poke up through this layer's floor, and `FootprintProjection` for every
rectangle on the picture. If the diagram is wrong, the view is wrong. A second copy of
the maths would have been a diagram that agrees with nothing.

Each picture holds the container footprint to scale, this layer's items as filled
labelled rectangles inside it, the protrusions as dashed outlines tagged with their step
number, the layer caption (`Layer 2 of 3 · floor at 3.5 cm · 4.5 cm thick · 1 item`, the
view's own caption line), and a legend of item labels and notes with the view's "Dashed:
… stand up through this layer." sentence. The stats band above is the same `PlanStats`
band the 3D mode carries, built from the plan as loaded.

Four things are `plan3d`'s and **not** the view's, so do not read them as fidelity:

* the `PlanStats` header band and the `Nested:` row (the view has no header band; it
  shows an orange `geometryIssues()` banner instead, which this mode prints to the
  terminal);
* the red `OUTSIDE CAVITY` / `NESTED IN MISSING` outlines, which are the renderer's own
  nesting checks, same as in the 3D mode;
* item colours, which are `plan3d`'s per-step palette — the same colour per step as the
  3D mode, which the view does not offer — because `Placement.diagramColor` is inside the
  library's `#if canImport(SwiftUI)` and does not exist on Linux at all;
* the step-number tag on a protrusion outline. The view draws protrusions bare and names
  them only in the legend. The full name centred under each outline was tried and was
  worse: two items standing side by side in the end well have almost the same footprint
  bottom edge, so the captions landed on each other and the right-hand one ran off the
  container.

One honest gap left:

* **`--cutaway` is ignored** (the run says so). It grows the container, which a to-scale
  footprint cannot survive, and separating the layers is what this mode does anyway.

### Two scales, and which one answers which question — `--device`

The default page is 900 px wide with a 560 × 680 column for the footprint, which puts the
demo bag on screen at **1115 pt/m**. `--layers --device` renders the same layout through
the same `FootprintProjection` at the scale the device actually gives it — **881 pt/m** —
and writes `device-layer-01.svg …` beside the desktop ones so the two can be compared:

```sh
/tmp/plan3d/debug/plan3d packing-core/Sources/PackingPlan/Resources/plan.json /tmp/out \
    --layers --device
```

**Which to trust.** Use the default page for *is the diagram right* — rectangles inside
the footprint, protrusions correct, captions climbing. Use `--device` for *is the diagram
readable, and how much of it is on screen at once*. The gap between the two is now 21%,
not the 2× it was before `af90f89`; what `--device` is for has changed with it, from
"does the label survive" to "how far do you scroll".

**Where the device scale comes from.**

| | |
|---|---|
| screen | 390 × 844 pt — the iPhone 12/13/14/15 logical size, the narrowest current non-mini (15 Pro and 16 are 393 × 852, the SE 375 × 667) |
| presentation | a `.sheet` at the `.large` detent, which is how `Spike/SpikeApp.swift` shows the view. 844 − 47 top safe − 10 detent peek − 34 home indicator = **753 pt** of frame |
| column | `planDiagramPadding` is 16, so **358 pt** wide |
| diagram | `planDiagramHeight(footprint:width:)` = width × depth/width = **358 × 537 pt**, taken *first* |
| scale | 358 / 0.4064 = **881 pt/m**, and the footprint fills its box exactly |
| fixed chrome | header, layer picker, layer caption and their spacing: **162 pt**, above the `ScrollView` and never scrolls away |
| scroll viewport | 753 − 162 − 16 bottom padding = **575 pt**; banners, diagram, legend and details scroll inside it |

**The diagram is not the residual, and that is the whole design.** `planDiagramHeight` is a
function of width alone on purpose: a height that cannot see how much room is left cannot
be negotiated down by an over-full `VStack`. So the scale is one number for a given
footprint and screen width — the same on every layer of every plan — and chrome no longer
costs picture, it costs *scrolling*. `--device` reports both: the budget line shows the
diagram taking its fixed height and the chrome stacking around it, and a `scroll:` line
says whether the bottom of the bag is on screen at rest.

* Demo plan, no banners: the diagram starts at the top of the scroll column, 537 pt of it
  against a 575 pt viewport — **the whole bag is visible without scrolling**, on all three
  layers.
* `fixtures/nested-carry-on.json`, two banners: 90 pt each pushes the picture 208 pt down
  the column, so **170 pt of the bag is below the fold**. Full size, full labels, one
  flick away. The device render draws that fold as a dashed line across the picture.

`plan3d` copies `planDiagramPadding` and `planDiagramHeight` rather than calling them:
both are declared outside the view's SwiftUI guard so Linux can check them, but both are
`internal` to `PackingPlanUI` and this is a different module. They are named as copies in
`Phone`, and the run asserts the one relationship that catches the formula being copied
backwards — a height of width × depth/width is exactly what makes `FootprintProjection`'s
two candidate scales equal.

**Two caveats, both in the optimistic direction.** Presenting through `PlanViewer` rather
than `PlanDiagramView` adds its own mode picker and padding, about 48 pt more fixed chrome
— which now costs scroll, not scale. And text wrapping is estimated at ~0.55 em per
character rather than measured, which over-counts a caption line here and there; the demo
header is budgeted at two lines and renders as one.

The per-item table prints the rectangle in points, then what the **view** puts in it
against what this **SVG** puts in it. The SVG now mirrors the view's
`lineLimit(2).minimumScaleFactor(0.75)` — one line at full size, or two at 75%, marked
`label*` — so the two columns agree on both plans at both scales. Before that it drew a
bare step number wherever one line overflowed, which meant reporting `2` for the toiletry
kit while the phone showed "2. Toiletry kit" over two lines: the picture under-reporting
the device on the exact item the scale question was about.

#### The verdict

**Yes, on both plans, at every layer.** At 881 pt/m the view's 54 × 28 pt label threshold
is **6.1 cm across by 3.2 cm deep**. On the demo plan every one of the six items clears it
with room to spare and carries name *and* dimensions — including the toiletry kit, 10 cm
wide and 88 pt on screen, which was the item that failed the old layout.

One item in the tree loses its label: the fixture's **charger pouch, 6 × 8 cm → 52.9 ×
70.5 pt**, 1.1 pt under the width gate, drawn as a bare `8`. That is the threshold working,
not failing — and the item is 6 cm across.

Two smaller observations from reading the renders:

* **Protrusion outlines are fine.** Dashed grey and unfilled stay unmistakable against a
  filled item, and at 881 pt/m their step-number tags are well clear of each other — the
  nested pair on fixture layer 3 (`2` containing `8`) reads cleanly, and no tag lands on
  the container wall.
* **The bare step number is low-contrast.** It is drawn in the item's own palette colour at
  9 pt on that item's 25%-opacity fill — a magenta `8` on dark purple. It is the only text
  a too-small item gets, so it is the one place a white or high-contrast fill would be
  worth more than colour-coding.

#### The four suggestions, re-judged

The first has been taken and is why the numbers above are what they are.

1. ~~**Put the body in a `ScrollView`**~~ — **done in `af90f89`**, together with pinning the
   diagram to `planDiagramHeight` so it cannot be squeezed. This is the change; the other
   three were mostly ways of buying back the height it now takes unconditionally.
2. **Collapse the banners to one tappable line.** Still worth something, but much less: it
   no longer rescues the picture, it just removes the 170 pt scroll on a plan that has
   issues. Cheap, not urgent.
3. **Move the legend behind the picture.** Now pointless. The legend is below the diagram
   in a scroll view, which is where a reference list belongs; it costs the picture nothing.
4. **Lean harder on `minimumScaleFactor` below the threshold.** Still open, and now the
   only one that changes what anybody can read: the charger pouch is 1.1 pt short of the
   54 pt gate and gets a bare `8`. A one-line abbreviated label would fit. Small, real.

One thing `--device` does *not* show, and should not be read as evidence about: the dotted
inner border and the "in `<host>`" line that `af90f89` added to a nested item's rectangle.
`plan3d`'s `--layers` draws nested items as ordinary rectangles, so its picture is missing
that cue at both scales.

### Checking a layer diagram is right

**1. Every rectangle inside the footprint, and the footprint the right shape.** This is
the honest X/Z view — there is no projection to hide behind. An item crossing the grey
wall is a solver bug and `geometryIssues()` at the top of the run should already say so.

**2. The protrusions are the tall items from below, and nothing else.** In the demo plan,
layer 2 (floor 3.5 cm) must show the shoes (11.5 cm), the dopp kit (14 cm) and the sweater
roll (7 cm) as dashed outlines, and layer 3 (floor 8 cm) must show the first two and *not*
the sweater, which stops at 7 cm. If a layer shows free floor where a tall item stands,
the diagram is inviting the user to pack into a space that is occupied — the exact failure
this outline exists to prevent, and worth re-checking after any change to
`protrusions(into:)`.

**3. The captions climb.** `layer-01` must be the lowest floor and each next one higher,
with the `Layer n of m` tag bottom-left agreeing with the filename. The 3D `--cutaway`
mode got this wrong once by pinning captions to slabs.

**4. Cross-check the terminal against the pictures.** The per-layer line is
`PlanStats.Layer.summaryText` — the same string the 3D `--cutaway` header prints — while
the picture is grouped by `PackingPlanUI`'s `layers()`. Those are two separate
implementations of the same 5 mm floor rule in the library, and the run prints a `WARNING`
if their layer counts ever disagree.

**6. Read it again at device scale.** `--device` is the same picture at the size it will
be on stage. Since `af90f89` the difference is modest — 881 pt/m against 1115 — so the
question it answers is no longer "does the label survive" but "how much of the bag is on
screen at once": a plan with issue banners pushes the bottom of the picture below the
fold. See **Two scales** above.

**5. Nested items.** A nest is *not* a thing the 2D view knows about: a nested item is
drawn as an ordinary rectangle in whatever layer its own floor puts it in, and its host is
usually a dashed protrusion from a lower layer. That reads correctly — in
`fixtures/nested-carry-on.json` the socks sit inside the shoes' dashed outline on layer 3
— but nothing on the picture says "nested", and if the item's floor lands in a different
layer from its host's, the two halves of the nest are in two different pictures. The run
prints which items those are.

## Checking the projection is right

Open the SVGs in a browser, or rasterise them:

```sh
google-chrome --headless --disable-gpu --window-size=900,700 \
  --screenshot=out.png file://$PWD/three-quarter.svg
```

A `--layers` SVG is taller than 700 — the header band grows with the stats — so use
`--window-size=900,860` for those or the axis note at the bottom is cut off.

Then look for these three things, in this order.

**1. Silhouette inside the container.** Every item outline must sit inside the grey
container walls in all three views. `top.svg` is the honest one for X/Z and `front.svg`
for X/Y: an item poking past a wall there is either a solver bug (which
`geometryIssues()` reports at the top of the run, so cross-check that line) or a
projection bug in the scale-to-fit.

**2. No box drawn over one in front of it.** This is the failure mode to actually hunt
for. Look at a stack: an item resting on another must have its whole near face visible
*above* the surface it rests on. If the lower item's top face cuts a diagonal line
across the upper item — the upper item looking half-sunk into the lower one — the
painter's sort put them in the wrong order. `ProjectedBox.sortDepth` is the depth of the
box *centre*, which is exact only for boxes separated along the view direction; a small
box standing on a large one is not, and the large box's centre can be nearer than the
small box's while the small box still occludes it.

**3. Shading consistent per face direction.** `ProjectedFace.shade` comes from a key
light fixed in *bag* space, not camera space. So in any one image, every box's top face
must be the brightest, and the two visible side faces must be shaded the same way as the
corresponding faces of every other box — all the +X faces one value, all the +Z faces
another. If a box's shading disagrees with its neighbours', or if the shading changes
between `three-quarter.svg` and `top.svg` for the same face, the light is leaking into
camera space and the model will appear to ripple as the user drags.

The container is drawn from the inside (its *far* walls, at reduced opacity), which is
what you see looking into an open bag. If you ever see a wall painted over an item, that
inside-out flip has broken.

## Checking a nested render is right

**Use `top.svg`.** It is the only camera where a nest reads reliably. A host sits low in the
bag by definition — it is the thing other things get stacked on — so in `three-quarter.svg`
the layer above it, and in `front.svg` whatever is nearer in Z, will cover part of it. That
is honest occlusion and not a bug, but it means the nest is a sliver of colour there. Looking
straight down, the cavity outline and the item inside it are unobstructed.

Then, in this order:

**1. The nested item is inside its dashed cell, with margin on every side.** This is the
whole test, and it is why the cell is drawn at all. In `top.svg` you are checking X and Z by
eye and reading Y off the flag; a box whose edge crosses the dashed line is a wrongly
declared `nestedIn`, and it should already be carrying a red `OUTSIDE CAVITY` outline. If it
crosses the line and is *not* flagged, the containment check is broken — that is a bug in
this tool, not in the plan.

**2. The dashed cell is inside the host's silhouette.** A cavity floating outside its host is
a producer bug and reads instantly as one: cyan dashes over bare container floor. It should
carry `CAVITY OUTSIDE <host>`.

**3. The host is a shell and you can see through it.** If a host renders as an opaque solid
with the nest invisible behind it, either it was not recognised as a host — check the
`nesting:` lines on the terminal and the `Nested:` header row — or the draw-order pull failed
and the host is painting over its own contents.

**4. Cross-check the header against the terminal.** Every nest the run prints should appear
in the `Nested:` row, and every rejection line on the terminal should correspond to an item
drawn as a plain solid with no cavity around it. A nest that prints but has no cyan cell in
any camera means the cavity projection is off.

**5. Under `--cutaway`, the nest stayed together.** A nested item drawn in a slab of its own,
floating clear of its host, means the host-pinned lift has broken. The layer whose item went
elsewhere says `· 1 nested, drawn with the host` in its caption; if a caption says that and
the item is *not* with its host in the picture, the two halves disagree.
