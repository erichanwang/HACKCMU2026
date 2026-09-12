# Looking at the 3D plan view on Linux

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
swift build
swift run plan3d <plan.json> <out-dir> [--steps]
```

Both plan shapes are accepted: the raw `PackingPlan` document, and the server document
that wraps it under a `plan` key (what `GET /suitcases/{id}/plan` returns).

Three cameras are written every run — `three-quarter.svg` (the default over-the-shoulder
view), `front.svg` (yaw 0, pitch 0, straight at the far wall) and `top.svg` (pitch 1.4,
nearly straight down). `--steps` additionally writes `step-01.svg …` at the default
camera, one per packing step, so the reveal sequence can be flipped through.

Write the output to a scratch directory. **Do not commit the SVGs** — they are a
verification artefact, not a build product.

Each file prints one line:

```
three-quarter.svg  boxes=6 container-first=yes bbox=(60.5,28.0)-(839.5,672.0)
```

That is enough to catch a regression without opening anything: a box count that does not
match placements + 1, a container that is no longer element 0 (it must be, or it paints
over the items), or a projected bounding box that has drifted, collapsed to a point or
gone NaN.

## Checking the projection is right

Open the SVGs in a browser, or rasterise them:

```sh
google-chrome --headless --disable-gpu --window-size=900,700 \
  --screenshot=out.png file://$PWD/three-quarter.svg
```

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
