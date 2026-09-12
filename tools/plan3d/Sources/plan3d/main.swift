import Foundation
import PackingPlan
import PackingPlanUI

// plan3d — render a PackingPlan to SVG so the 3D projection and the 2D fallback
// diagram can be inspected on a machine with no Mac and no simulator. See
// docs/PLAN_3D.md.
//
//   swift run plan3d <plan.json> <out-dir> [--steps] [--cutaway] [--unpacked]
//                                          [--violations] [--layers [--device]]

let width: Float = 900
let height: Float = 700
let canvas = SIMD2<Float>(width, height)

/// Extra canvas to the right of the bag, used only by `--unpacked`.
let shelfWidth: Float = 300

/// Fixed palette by step; the container is grey. Colour identifies the item,
/// `shade` from the projection identifies the face direction.
let palette: [(UInt8, UInt8, UInt8)] = [
    (232, 93, 82), (240, 168, 63), (233, 214, 84), (126, 200, 108),
    (86, 180, 205), (110, 133, 224), (176, 116, 214), (226, 118, 172),
]

/// Cavity outlines and the nesting labels. One colour for the whole nesting
/// story, so "cyan means nesting" is the only thing to learn.
let nestStroke = "#3fd8d0"

func hex(_ rgb: (UInt8, UInt8, UInt8), _ shade: Float) -> String {
    let s = min(max(shade, 0), 1)
    return String(
        format: "#%02X%02X%02X",
        Int(Float(rgb.0) * s), Int(Float(rgb.1) * s), Int(Float(rgb.2) * s)
    )
}

func escaped(_ s: String) -> String {
    s.replacingOccurrences(of: "&", with: "&amp;")
        .replacingOccurrences(of: "<", with: "&lt;")
        .replacingOccurrences(of: ">", with: "&gt;")
}

func points(_ p: [SIMD2<Float>]) -> String {
    p.map { String(format: "%.2f,%.2f", $0.x, $0.y) }.joined(separator: " ")
}

/// Greedy break at `limit` characters, so a solver's one-line reason fits the
/// shelf without an SVG text-layout engine.
func wrapped(_ text: String, _ limit: Int) -> [String] {
    var lines = [""]
    for word in text.split(separator: " ") {
        if lines[lines.count - 1].isEmpty {
            lines[lines.count - 1] = String(word)
        } else if lines[lines.count - 1].count + word.count + 1 <= limit {
            lines[lines.count - 1] += " " + word
        } else {
            lines.append(String(word))
        }
    }
    return lines
}

/// The items the solver could not fit, drawn as a greyed shelf beside the bag —
/// they are in the solver result but not in the plan, so nothing in the
/// projection knows about them.
func shelf(_ items: [(id: String, reason: String)]) -> String {
    let x = width + 20
    var out = "<g id=\"unpacked\">\n"
    out += "  <line x1=\"\(width)\" y1=\"0\" x2=\"\(width)\" y2=\"\(Int(height))\" "
    out += "stroke=\"#2c3038\" stroke-width=\"2\"/>\n"
    out += "  <text x=\"\(x)\" y=\"24\" font-family=\"monospace\" font-size=\"14\" fill=\"#8a8f98\">"
    out += escaped("left out by the solver (\(items.count))") + "</text>\n"

    var y: Float = 44
    for item in items {
        let lines = wrapped(item.reason, 36)
        let boxHeight = 30 + Float(lines.count) * 14
        out += String(
            format: "  <rect x=\"%.1f\" y=\"%.1f\" width=\"%.1f\" height=\"%.1f\" rx=\"4\" ",
            x, y, shelfWidth - 40, boxHeight
        )
        out += "fill=\"#42474f\" fill-opacity=\"0.55\" stroke=\"#5d636d\" stroke-width=\"1\"/>\n"
        out += String(format: "  <text x=\"%.1f\" y=\"%.1f\" ", x + 10, y + 18)
        out += "font-family=\"sans-serif\" font-size=\"13\" fill=\"#c9ced6\">"
        out += escaped(item.id) + "</text>\n"
        for (i, line) in lines.enumerated() {
            out += String(format: "  <text x=\"%.1f\" y=\"%.1f\" ", x + 10, y + 34 + Float(i) * 14)
            out += "font-family=\"sans-serif\" font-size=\"11\" fill=\"#8a8f98\">"
            out += escaped(line) + "</text>\n"
        }
        y += boxHeight + 10
    }
    return out + "</g>\n"
}

// MARK: - Nested placements

/// A `nestedIn` assertion that survived the contract checks in `declaredNests`:
/// the item, the host it sits inside, and the one cavity cell the overlap
/// between them is permitted in.
struct Nest {
    let itemID: String
    let hostID: String
    let hostLabel: String
    let cavity: BoundingBox
}

func number(_ any: Any?) -> Float? {
    if let d = any as? Double { return Float(d) }
    if let i = any as? Int { return Float(i) }
    return nil
}

func vector(_ any: Any?) -> Vector3? {
    guard let d = any as? [String: Any],
          let x = number(d["x"]), let y = number(d["y"]), let z = number(d["z"])
    else { return nil }
    return Vector3(x, y, z)
}

/// `nestedIn` per placement, read straight out of the JSON.
///
/// `Placement` has no property for it — packing-core's Swift model does not
/// decode the field — so this is the same side-car read as `solver.unpacked` and
/// `validation.violations` below, and it accepts both document shapes.
///
/// Only assertions a consumer may trust come back in `nests`. Per
/// packing-core/CLAUDE.md, absent, `null`, a self-reference, a cycle and a host
/// that is not in the plan are all "not nested", and a host with no cavity cell
/// is not representable at all; each rejection comes back as a `note`. The
/// dangling host is the one the contract says additionally raises its own issue,
/// so that one also comes back in `flags` and gets drawn.
func declaredNests(
    _ document: Any?,
    plan: PackingPlan
) -> (nests: [Nest], notes: [String], flags: [String: String]) {
    var raw: [[String: Any]] = []
    if let root = document as? [String: Any] {
        // The server wraps the plan under `plan`; the solver can also emit it raw.
        let inner = (root["plan"] as? [String: Any]) ?? root
        raw = inner["placements"] as? [[String: Any]] ?? []
    }

    let labels = Dictionary(plan.placements.map { ($0.itemID, $0.label) }, uniquingKeysWith: { a, _ in a })
    var nests: [Nest] = []
    var notes: [String] = []
    var flags: [String: String] = [:]
    var hostOf: [String: String] = [:]

    for entry in raw {
        guard let item = entry["itemId"] as? String else { continue }
        // Absent and null are the same thing and are not worth a note: every
        // producer in the tree emits null today.
        guard let nested = entry["nestedIn"] as? [String: Any] else { continue }
        guard let host = nested["itemId"] as? String, !host.isEmpty else {
            notes.append("\(item): nestedIn with no itemId — not nested")
            continue
        }
        guard let cell = nested["cavity"] as? [String: Any],
              let position = vector(cell["position"]),
              let size = vector(cell["size"])
        else {
            notes.append("\(item): nestedIn names '\(host)' with no usable cavity cell — "
                + "not checkable, so not nested")
            continue
        }
        if host == item {
            notes.append("\(item): nestedIn points at itself — not nested")
            continue
        }
        guard let hostLabel = labels[host] else {
            notes.append("\(item): nestedIn names '\(host)', which is not a placement in this plan")
            flags[item] = "NESTED IN MISSING '\(host)'"
            continue
        }
        hostOf[item] = host
        nests.append(
            Nest(
                itemID: item,
                hostID: host,
                hostLabel: hostLabel,
                cavity: BoundingBox(minCorner: position, size: size)
            )
        )
    }

    // A cycle has no outermost host, so there is no "inside" to draw. Drop the
    // whole chain rather than pick a winner.
    var cyclic: Set<String> = []
    for start in hostOf.keys {
        var seen: Set<String> = [start]
        var cursor = start
        while let next = hostOf[cursor] {
            if !seen.insert(next).inserted {
                cyclic.formUnion(seen)
                break
            }
            cursor = next
        }
    }
    if !cyclic.isEmpty {
        notes.append("nesting cycle — none of these are nested: " + cyclic.sorted().joined(separator: ", "))
        nests.removeAll { cyclic.contains($0.itemID) }
    }

    return (nests, notes, flags)
}

/// The cavity cells projected into the same screen space as the plan.
///
/// Built by handing the cavities to `projected(camera:size:upTo:)` as if they
/// were placements of the same container: the projector's scale-to-fit depends
/// only on `container.interior` and the canvas, so the transform is identical
/// and no projection maths is duplicated here.
func projectedCavities(
    _ nests: [Nest],
    container: Container,
    camera: PlanCamera
) -> [String: ProjectedBox] {
    guard !nests.isEmpty else { return [:] }
    let asPlacements = nests.enumerated().map { index, nest in
        Placement(
            step: index + 1,
            itemID: nest.itemID,
            label: nest.hostLabel,
            zone: "",
            position: nest.cavity.minCorner,
            size: nest.cavity.size,
            rotation: .xyz,
            note: ""
        )
    }
    let cavityPlan = PackingPlan(
        version: 1, units: .meters, container: container, placements: asPlacements
    )
    // Element 0 is the container; drop it.
    let projected = cavityPlan.projected(camera: camera, size: canvas, upTo: nil).dropFirst()
    return Dictionary(projected.map { ($0.id, $0) }, uniquingKeysWith: { a, _ in a })
}

/// A nested item lives inside its host, so the two interpenetrate — `nearer()`
/// returns nil for that pair on purpose and the library's painter sort falls back
/// to centre depth, which for two near-concentric boxes is a coin flip. The nest
/// is the whole point of this picture, so pull each nested box to just in front of
/// its host: it inherits the host's place in the order, so anything genuinely in
/// front of the host still paints over it.
func nestedInFront(_ boxes: [ProjectedBox], _ nests: [Nest]) -> [ProjectedBox] {
    var out = boxes
    for nest in nests {
        guard let from = out.firstIndex(where: { $0.id == nest.itemID }) else { continue }
        let box = out.remove(at: from)
        guard let host = out.firstIndex(where: { $0.id == nest.hostID }) else {
            out.insert(box, at: from)
            continue
        }
        out.insert(box, at: host + 1)
    }
    return out
}

// MARK: - Everything drawn on top of the boxes

/// The annotation layers. Bundled because they all travel together from `main`
/// through `write` into `svg`, and because nothing here is geometry — it is what
/// gets drawn over the geometry.
struct Annotations {
    var violations: [String: String] = [:]
    var unpacked: [(id: String, reason: String)] = []
    var nests: [Nest] = []
    /// Cavity cells for `nests`, keyed by nested item id, in screen space.
    var cavities: [String: ProjectedBox] = [:]
    /// Stats lines under the title. Straight from `PlanStats`.
    var header: [String] = []
}

func svg(_ boxes: [ProjectedBox], title: String, _ notes: Annotations = Annotations()) -> String {
    let total = width + (notes.unpacked.isEmpty ? 0 : shelfWidth)
    // The header owns a band above the scene rather than sitting over the bag:
    // stats painted over the container's top wall are unreadable exactly when the
    // bag is full, which is when you are reading them.
    let band: Float = 30 + Float(notes.header.count) * 17
    let canvasHeight = height + band

    var out = """
    <svg xmlns="http://www.w3.org/2000/svg" width="\(Int(total))" height="\(Int(canvasHeight))" \
    viewBox="0 0 \(Int(total)) \(Int(canvasHeight))">
    <rect width="\(Int(total))" height="\(Int(canvasHeight))" fill="#16181c"/>
    <text x="12" y="20" font-family="monospace" font-size="14" fill="#8a8f98">\(escaped(title))</text>

    """
    for (i, line) in notes.header.enumerated() {
        out += String(format: "<text x=\"12\" y=\"%.0f\" ", 40 + Float(i) * 17)
        out += "font-family=\"sans-serif\" font-size=\"13\" fill=\"#c9ced6\">\(escaped(line))</text>\n"
    }
    out += "<g transform=\"translate(0,\(Int(band)))\">\n"

    let hosts = Set(notes.nests.map(\.hostID))
    var flags = ""
    for box in boxes {
        let isContainer = box.step == 0
        let isHost = hosts.contains(box.id)
        let rgb = isContainer ? (150, 156, 165) : palette[(box.step - 1) % palette.count]
        out += "<g id=\"\(escaped(box.id))\">\n"
        for face in box.faces {
            out += "  <polygon points=\"\(points(face.corners))\" fill=\"\(hex(rgb, face.shade))\""
            // A host is drawn as an open shell. `faces` holds only the faces
            // turned toward the camera, so fading them is precisely "the near
            // faces dropped" — you look through the near wall into the cavity —
            // and keeping the silhouette is what preserves the containment read
            // that the whole picture exists to check.
            if isContainer {
                out += " fill-opacity=\"0.45\"/>\n"
            } else if isHost {
                out += " fill-opacity=\"0.28\"/>\n"
            } else {
                out += "/>\n"
            }
        }
        out += "  <polygon points=\"\(points(box.outline))\" fill=\"none\" "
        let stroke = isContainer ? "#5d636d" : (isHost ? "#c9ced6" : "#0d0e10")
        out += "stroke=\"\(stroke)\" stroke-width=\"\(isContainer ? 2 : (isHost ? 2 : 1))\"/>\n"
        if !isContainer {
            // A host's own label moves to its rim: the middle of a host is where
            // the nest and its cavity caption are, and two labels on the same
            // pixels is the one thing that makes this render unreadable.
            let labelY = isHost
                ? (box.outline.map(\.y).min() ?? box.center.y) + 14
                : box.center.y
            let at = String(format: "x=\"%.2f\" y=\"%.2f\"", box.center.x, labelY)
            out += "  <text \(at) font-family=\"sans-serif\" font-size=\"12\" text-anchor=\"middle\" "
            out += "fill=\"#0d0e10\" stroke=\"#ffffff\" stroke-width=\"2.5\" paint-order=\"stroke\">"
            out += escaped("\(box.step). \(box.label)") + "</text>\n"
        }
        out += "</g>\n"

        // Collected and drawn after every box, on purpose: a violation marked in
        // painter order would be half-covered by whatever is packed in front of
        // the offending item, which is exactly the case you need to see.
        if let reason = notes.violations[box.id] {
            flags += "  <polygon points=\"\(points(box.outline))\" fill=\"none\" "
            flags += "stroke=\"#ff4d4d\" stroke-width=\"3\"/>\n"
            let at = String(format: "x=\"%.2f\" y=\"%.2f\"", box.center.x, box.center.y + 16)
            flags += "  <text \(at) font-family=\"monospace\" font-size=\"11\" text-anchor=\"middle\" "
            flags += "fill=\"#ff4d4d\" stroke=\"#0d0e10\" stroke-width=\"2.5\" paint-order=\"stroke\">"
            flags += escaped(reason) + "</text>\n"
        }
    }

    // The cavity outline goes over everything for the same reason: the nested
    // item fills the cell, so an outline drawn in painter order would be hidden
    // by the very item it is there to bound.
    let byID = Dictionary(boxes.map { ($0.id, $0) }, uniquingKeysWith: { a, _ in a })
    if !notes.nests.isEmpty {
        out += "<g id=\"nests\">\n"
        for nest in notes.nests {
            guard let cavity = notes.cavities[nest.itemID] else { continue }
            out += "  <polygon points=\"\(points(cavity.outline))\" fill=\"none\" stroke=\"\(nestStroke)\" "
            out += "stroke-width=\"2\" stroke-dasharray=\"6 4\"/>\n"
            if let host = byID[nest.hostID] {
                out += String(
                    format: "  <line x1=\"%.2f\" y1=\"%.2f\" x2=\"%.2f\" y2=\"%.2f\" ",
                    cavity.center.x, cavity.center.y, host.center.x, host.center.y
                )
                out += "stroke=\"\(nestStroke)\" stroke-width=\"1\" stroke-dasharray=\"3 3\"/>\n"
            }
            // Under the cell, not in it: the nested item's own step label is at
            // the centre and the violation flag just below that.
            let base = (cavity.outline.map(\.y).max() ?? cavity.center.y) + 14
            for (i, line) in ["in \(nest.hostLabel)", "cavity \(PlanStats.sizeText(nest.cavity.size))"]
                .enumerated() {
                let at = String(format: "x=\"%.2f\" y=\"%.2f\"", cavity.center.x, base + Float(i) * 13)
                out += "  <text \(at) font-family=\"monospace\" font-size=\"11\" text-anchor=\"middle\" "
                out += "fill=\"\(nestStroke)\" stroke=\"#0d0e10\" stroke-width=\"2.5\" paint-order=\"stroke\">"
                out += escaped(line) + "</text>\n"
            }
        }
        out += "</g>\n"
    }

    if !flags.isEmpty { out += "<g id=\"violations\">\n" + flags + "</g>\n" }
    out += notes.unpacked.isEmpty ? "" : shelf(notes.unpacked)
    return out + "</g>\n</svg>\n"
}

func write(
    _ boxes: [ProjectedBox],
    to url: URL,
    title: String,
    _ notes: Annotations = Annotations()
) throws {
    try svg(boxes, title: title, notes).write(to: url, atomically: true, encoding: .utf8)

    var low = SIMD2<Float>(.greatestFiniteMagnitude, .greatestFiniteMagnitude)
    var high = -low
    for p in boxes.flatMap(\.outline) {
        low = SIMD2(min(low.x, p.x), min(low.y, p.y))
        high = SIMD2(max(high.x, p.x), max(high.y, p.y))
    }
    let containerFirst = boxes.first?.id == "container"
    let name = url.lastPathComponent.padding(toLength: 18, withPad: " ", startingAt: 0)
    let bbox = String(format: "(%.1f,%.1f)-(%.1f,%.1f)", low.x, low.y, high.x, high.y)
    print("\(name) boxes=\(boxes.count) container-first=\(containerFirst ? "yes" : "NO") bbox=\(bbox)")
}

// MARK: - Cutaway

/// Layers pulled apart vertically, so a full bag stops reading as one brick.
///
/// The grouping is `PlanStats.layerStats()` — the library's own 5 mm floor
/// grouping — so the layers this pulls apart are exactly the layers the captions
/// name. Each layer is lifted until it clears the tallest item of the one below
/// plus a margin: a flat gap would let a lifted layer straddle a bottle standing
/// on the layer under it and hide it worse than before. The container grows by
/// the same total, which keeps the scale-to-fit and the containment read honest:
/// the same bag, opened up. The container is already drawn from the inside (its
/// near walls are culled by the projection), so there is nothing to cut there.
///
/// A nested item rides with its host rather than with its own floor height: it
/// rests in a cavity, not on a shelf, so lifting it by its own layer would tear
/// the nest apart — the one thing a nested render exists to show.
func exploded(_ plan: PackingPlan, layers: [PlanStats.Layer], nests: [Nest]) -> PackingPlan {
    let margin = plan.container.dimensions.y * 0.15
    var lift: [String: Float] = [:]
    var running: Float = 0
    for layer in layers {
        if layer.index > 0 {
            let below = layers[layer.index - 1]
            running += max(0, (below.floorY + below.thickness) - layer.floorY) + margin
        }
        for placement in layer.placements { lift[placement.itemID] = running }
    }

    let hostOf = Dictionary(nests.map { ($0.itemID, $0.hostID) }, uniquingKeysWith: { a, _ in a })
    for item in hostOf.keys {
        var host = hostOf[item]!
        while let next = hostOf[host] { host = next }  // cycles are already dropped
        lift[item] = lift[host] ?? 0
    }

    let placements = plan.placements.map { p -> Placement in
        Placement(
            step: p.step,
            itemID: p.itemID,
            label: p.label,
            zone: p.zone,
            position: Vector3(p.position.x, p.position.y + (lift[p.itemID] ?? 0), p.position.z),
            size: p.size,
            rotation: p.rotation,
            note: p.note
        )
    }
    let container = Container(
        id: plan.container.id,
        label: plan.container.label,
        dimensions: Vector3(
            plan.container.dimensions.x,
            plan.container.dimensions.y + running,
            plan.container.dimensions.z
        ),
        zones: plan.container.zones
    )
    return PackingPlan(
        version: plan.version, units: plan.units, container: container, placements: placements
    )
}

/// The cavity cells moved by the same lifts, so they stay around their items.
func explodedCavities(_ nests: [Nest], original: PackingPlan, opened: PackingPlan) -> [Nest] {
    let lift = Dictionary(
        opened.placements.map { ($0.itemID, $0.position.y) }, uniquingKeysWith: { a, _ in a }
    )
    return nests.map { nest in
        let before = original.placements.first { $0.itemID == nest.itemID }?.position.y ?? 0
        let delta = (lift[nest.itemID] ?? before) - before
        return Nest(
            itemID: nest.itemID,
            hostID: nest.hostID,
            hostLabel: nest.hostLabel,
            cavity: BoundingBox(
                minCorner: nest.cavity.minCorner + Vector3(0, delta, 0), size: nest.cavity.size
            )
        )
    }
}

// MARK: - The 2D layer diagram (--layers)

/// `--layers` draws `PlanDiagramView`'s picture: the top-down layer diagram that
/// is the demo's fallback when there is no AR session, no plane detection and no
/// camera permission. That view is SwiftUI, so on this box it is the one part of
/// the plan pipeline nobody can see — this is how it gets looked at.
///
/// Every number and every rectangle comes from the same `PackingPlanUI` calls the
/// view makes: `plan.layers()` for the grouping, `plan.protrusions(into:)` for the
/// items from lower layers that poke up through this floor, and
/// `FootprintProjection` for the placement rects. No layout is recomputed here; a
/// second copy would drift from the view it exists to show.
///
/// The two things that are *not* shared, because both are behind
/// `#if canImport(SwiftUI)` in the library and therefore do not exist on Linux:
/// `Placement.diagramColor` (so the item colours are this tool's `palette`, the
/// same colour per step as the 3D mode — a cross-check the view does not offer)
/// and `centimetres(_:decimals:)`, which is `internal` as well; `PlanStats`'
/// public formatters stand in.

/// The layer diagram's page geometry: how much room the footprint is given, and
/// where the furniture around it goes.
///
/// Two instances exist. `desktop` is the comfortable picture this mode has always
/// written; `phone(...)` below is the area the device actually gives the diagram.
/// Everything that draws reads `layout`, so `--device` swaps a page size, not a
/// second renderer — the rectangles still come from `FootprintProjection`, which
/// is handed `layout.diagram` instead of a constant.
struct DiagramLayout {
    /// SVG canvas width.
    var canvas: Float
    /// What `FootprintProjection` is handed — on the device, what `GeometryReader`
    /// reports inside `aspectRatio(.fit)`.
    var diagram: CGSize
    /// Where that box sits on the canvas.
    var origin: SIMD2<Float>
    /// Legend column x, or nil to run the legend under the diagram instead of
    /// beside it: at a phone's width there is no beside.
    var legendX: Float?
    /// Height of the band above the picture. nil derives it from the header, as
    /// the desktop page always has; the phone measures its real chrome instead.
    var band: Float?
    /// Views the device stacks above the diagram that this tool does not draw —
    /// the two issue banners and the layer picker. Blocked out at their real
    /// heights, because the room they take is the finding: left blank, the band
    /// reads as a rendering bug rather than as screen the diagram does not get.
    var chrome: [(label: String, y: Float, height: Float)] = []
    /// Where the bottom of the screen falls on this page. Below it the device has
    /// content but no screen: it is reached by scrolling, not by looking.
    var fold: Float?
    /// Font sizes: the item label inside its rect, the dimensions line under it,
    /// the bare step number, and body text. The desktop numbers are this tool's;
    /// the phone's are the view's own (`.caption2` = 11, `.system(size: 9)`).
    var label: Float
    var size: Float
    var step: Float
    var body: Float
    var title: Float
    /// Inset of the in-rect text from the rect's top-left corner.
    var pad: Float
}

/// The footprint gets the left column; the legend gets the rest of the 900.
let desktopLayout = DiagramLayout(
    canvas: width,
    diagram: CGSize(width: 560, height: 680),
    origin: SIMD2<Float>(20, 10),
    legendX: 600,
    band: nil,
    fold: nil,
    label: 12, size: 10, step: 11, body: 13, title: 14, pad: 6
)

/// Mutated once per layer by `--device`, because the phone's scale is per layer:
/// the legend is laid out above the diagram in the same stack and takes its room
/// first. Read by every drawing function below.
var layout = desktopLayout

/// `PlanDiagramView`'s own thresholds, below which the text does not fit the
/// rectangle and the legend carries the label instead — plus a width check the
/// view does not need. SwiftUI wraps the label to two lines and shrinks it to fit
/// its box; SVG `<text>` does neither, so a long label in a small rect spills
/// across its neighbours (`8. Charger pouch` in a 67 px box, in the fixture).
/// ~0.55 em per character at this font, which is close enough for a threshold —
/// and it has to be read off `layout.label`, not a constant: `--device` draws the
/// label at the view's 11 pt rather than this page's 12, so a hardcoded width
/// would call a label too wide for a box it actually fits.
func sizeFits(_ r: CGRect) -> Bool { r.width >= 74 && r.height >= 44 }

/// How the label goes into its rectangle, or nil for "it does not, draw the step
/// number".
///
/// This is `PlanDiagramView`'s `.lineLimit(2).minimumScaleFactor(0.75)`, expressed
/// as the two things SVG `<text>` can actually do: one line at full size, or two
/// at 75%. Before this existed the picture fell back to a bare step number
/// wherever one line overflowed, which on the demo plan meant drawing `2` for the
/// toiletry kit while the phone was showing "2. Toiletry kit" over two lines —
/// the picture under-reporting the device on the very item the scale question is
/// about.
func labelLayout(_ r: CGRect, _ label: String) -> (lines: [String], size: Float)? {
    // The view's own gate. Under this it draws the step number and nothing else,
    // however short the label is.
    guard r.width >= 54, r.height >= 28 else { return nil }
    let room = Float(r.width) - 2 * layout.pad
    if Float(label.count) * layout.label * 0.55 <= room { return ([label], layout.label) }

    let shrunk = layout.label * 0.75
    let lines = wrapped(label, max(1, Int(room / (shrunk * 0.55))))
    guard lines.count <= 2,
          lines.allSatisfy({ Float($0.count) * shrunk * 0.55 <= room }),
          Float(lines.count) * (shrunk + 1) + layout.pad <= Float(r.height)
    else { return nil }
    return (lines, shrunk)
}

/// What the device puts inside an item's rectangle, and what this SVG puts there.
///
/// They differ on purpose and the difference only ever runs one way: the view
/// wraps the label to two lines and shrinks it to 75% to make it fit, SVG `<text>`
/// does neither, so a label the phone squeezes in can come out here as a bare step
/// number. Reported as two columns rather than papered over — `svg` is what you
/// are looking at, `view` is what the judge sees.
func legibility(_ r: CGRect, _ label: String) -> (view: String, svg: String) {
    let view = (r.width >= 54 && r.height >= 28) ? (sizeFits(r) ? "label+size" : "label") : "step only"
    guard let fit = labelLayout(r, label) else { return (view, "step only") }
    let wrap = fit.lines.count > 1 ? "*" : ""
    return (view, (sizeFits(r) ? "label+size" : "label") + wrap)
}

// MARK: - Device scale (--device)

/// The phone the fallback gets demoed on, in points.
///
/// Every number is an assumption, so every number is named and separate: change
/// one and the derived scale moves with it. 390 × 844 pt is the iPhone 12/13/14/15
/// logical screen — the narrowest of the current non-mini sizes (15 Pro and 16 are
/// 393 × 852, the SE 375 × 667), which makes it the honest one to judge legibility
/// at. `PlanDiagramView` is presented as a `.sheet` (`Spike/SpikeApp.swift`), and a
/// `.large` detent leaves the presenter's status bar showing, so the card starts
/// below the top safe inset plus a small peek.
///
/// **`padding` and `diagramHeight` are copies.** `PlanDiagramView` declares both
/// outside its SwiftUI guard, precisely so a Linux build can check them —
/// `planDiagramPadding` and `planDiagramHeight(footprint:width:)` — but both are
/// `internal` to `PackingPlanUI`, and plan3d is a different module. So they are
/// restated here and named, the same way the 5 mm layer rule is stated twice in
/// the library. If the view's numbers move, these have to move with them; the run
/// asserts the one relationship that catches a formula got backwards.
enum Phone {
    static let screen = SIMD2<Float>(390, 844)
    static let safeTop: Float = 47       // status bar / notch, non-Dynamic-Island
    static let safeBottom: Float = 34    // home indicator
    static let sheetPeek: Float = 10     // .large detent's gap above the card
    static let padding: Float = 16       // = planDiagramPadding, explicit in the view
    static let spacing: Float = 14       // PlanDiagramView's VStack spacing
    static let legendSpacing: Float = 6  // its legend VStack spacing

    // Line heights at the default Dynamic Type size (Large).
    static let headline: Float = 22      // .headline, 17 pt
    static let caption: Float = 16       // .caption, 12 pt
    static let caption2: Float = 14      // .caption2, 11 pt
    static let picker: Float = 32        // segmented Picker
    static let details: Float = 28       // collapsed DisclosureGroup row
    static let swatch: Float = 18        // legend step circle
    static let bannerPad: Float = 8      // PlanIssueBanner's padding, top and bottom

    /// The column inside the view's own padding — what `planDiagramHeight` is fed.
    static var content: Float { screen.x - 2 * padding }

    /// The height the view's outer `VStack` is given.
    static var frame: Float { screen.y - safeTop - sheetPeek - safeBottom }

    /// `planDiagramHeight(footprint:width:)`, restated: the footprint is drawn to
    /// scale, so its height is its width times the bag's depth-over-width ratio.
    ///
    /// **A function of width alone**, which is the whole point of it — the diagram
    /// takes its height before anything else is measured, so no amount of chrome
    /// can negotiate it down. Everything that does not fit scrolls.
    static func diagramHeight(_ footprint: Vector3) -> Float {
        guard footprint.x > 0, footprint.z > 0 else { return 0 }
        return content * (footprint.z / footprint.x)
    }
}

/// Characters that fit `points` of width at `size`, at ~0.55 em each — the same
/// estimate `labelFits` already uses, and the only one in this file.
func fits(_ points: Float, _ size: Float) -> Int { max(1, Int(points / (size * 0.55))) }

/// How tall the view's legend is for one layer.
///
/// The view's metrics, not this tool's: a row is the step circle beside a
/// `.caption` label over a `.caption2` note wrapped to the column left of the
/// circle, then the "Dashed: …" sentence and the axis note. This is the number
/// that decides the diagram's scale, so it has to be the view's — plan3d draws its
/// own legend at its own sizes and that one only has to fit on the canvas.
func viewLegendHeight(_ placements: [Placement], _ protrusions: [Placement]) -> Float {
    let noteColumn = Phone.content - Phone.swatch - 8
    var rows: [Float] = placements.map { placement in
        let lines = wrapped(placement.note, fits(noteColumn, 11)).count
        return max(Phone.swatch, Phone.caption + 1 + Float(lines) * Phone.caption2)
    }
    if !protrusions.isEmpty {
        let verb = protrusions.count == 1 ? "stands" : "stand"
        let sentence = "Dashed: \(protrusions.map(\.label).joined(separator: ", ")) "
            + "\(verb) up through this layer."
        rows.append(Float(wrapped(sentence, fits(Phone.content, 11)).count) * Phone.caption2)
    }
    rows.append(Phone.caption2)  // "Seen from above · X across, Z down · origin at top-left"
    return rows.reduce(0, +) + Float(rows.count - 1) * Phone.legendSpacing
}

/// The page the device actually gives one layer, and the arithmetic that got there.
///
/// `PlanDiagramView`'s body is a `GeometryReader` over a `VStack` whose first
/// three rows — header, layer picker, layer caption — are fixed chrome that never
/// scrolls, and whose fourth is a `ScrollView` holding the two issue banners, the
/// diagram, the legend and the collapsed details row.
///
/// **The diagram is not the residual.** It takes `planDiagramHeight`, a function
/// of the content width alone, before anything else is measured; nothing can
/// negotiate it down and the overflow scrolls instead. So the scale is one number
/// for a given footprint and screen width — the same on every layer, banners or no
/// banners — and what the chrome costs is no longer picture, it is *scrolling*.
/// The two things worth reporting are therefore the scale, and how far down the
/// bottom of the bag starts.
func phoneLayout(
    _ plan: PackingPlan,
    layer: PlanLayer,
    layers: Int,
    protrusions: [Placement],
    header: String,
    caption: String,
    issues: [String],
    stability: [String]
) -> (layout: DiagramLayout, rows: [(String, Float)], scale: Float, scrollToSeeItAll: Float) {
    func banner(_ list: [String]) -> Float {
        guard !list.isEmpty else { return 0 }
        let body = list.prefix(3).reduce(Float(0)) {
            $0 + Float(wrapped($1, fits(Phone.content - 2 * Phone.bannerPad - 24, 11)).count)
                * Phone.caption2
        }
        return 2 * Phone.bannerPad + Phone.caption + 2 + body
    }

    // The diagram, first and unconditionally.
    let diagram = SIMD2(Phone.content, Phone.diagramHeight(plan.container.dimensions))
    let scale = diagram.x / Float(plan.container.dimensions.x)
    // The one relationship worth asserting: a height that is width × depth/width
    // makes `FootprintProjection`'s two candidate scales equal, so the footprint
    // fills its box exactly. If this trips, the formula has been copied backwards.
    let byHeight = diagram.y / Float(plan.container.dimensions.z)
    precondition(abs(scale - byHeight) < 0.5, "diagramHeight does not preserve the footprint ratio")

    // Fixed chrome: the rows above the ScrollView. These never scroll away.
    var fixed: [(String, Float)] = [
        ("top padding", Phone.padding),
        ("header", Phone.headline + 2
            + Float(wrapped(header, fits(Phone.content, 12)).count) * Phone.caption),
        ("layer picker", layers > 1 ? Phone.picker : 0),
        ("layer caption", Float(wrapped(caption, fits(Phone.content, 12)).count) * Phone.caption),
    ]
    // One gap per boundary between rows that exist, plus the one before the
    // ScrollView itself.
    let fixedRows = fixed.dropFirst().filter { $0.1 > 0 }.count
    let band = fixed.reduce(0) { $0 + $1.1 } + Float(fixedRows) * Phone.spacing
    let viewport = Phone.frame - band - Phone.padding

    // The scrolling column, in the order the inner VStack stacks it.
    var scrolling: [(String, Float)] = [
        ("geometry banner", banner(issues)),
        ("stability banner", banner(stability)),
        ("diagram (fixed height)", diagram.y),
        ("legend", viewLegendHeight(layer.placements, protrusions)),
        ("details row", Phone.details),
    ]

    // How far down the scroll column the picture starts, and whether its bottom
    // is on screen before you touch it.
    var top: Float = 0
    var blocks: [(String, Float, Float)] = []
    for (name, size) in scrolling where size > 0 && name.hasSuffix("banner") {
        blocks.append((name, band + top, size))
        top += size + Phone.spacing
    }
    let hidden = max(0, top + diagram.y - viewport)

    // The picker is fixed chrome, so its y is absolute; the banners were placed
    // above relative to the band. Both get blocked out at full height.
    var cursor = Phone.padding
    for (name, size) in fixed.dropFirst() where size > 0 {
        if name == "layer picker" { blocks.insert((name, cursor, size), at: 0) }
        cursor += size + Phone.spacing
    }

    let gaps = Float(max(0, scrolling.filter { $0.1 > 0 }.count - 1)) * Phone.spacing
    fixed.append(("fixed chrome subtotal", band))
    scrolling.append(("spacing", gaps))
    scrolling.append(("scroll viewport", viewport))

    let page = DiagramLayout(
        canvas: Phone.screen.x,
        diagram: CGSize(width: CGFloat(diagram.x), height: CGFloat(diagram.y)),
        // y is relative to the band, which the SVG has already translated past:
        // the banners sit between the two.
        origin: SIMD2(Phone.padding, top),
        legendX: nil,
        band: band,
        chrome: blocks,
        fold: band + viewport,
        // The view's own type sizes: `.caption2` in the rect, `.system(size: 9)`
        // for the dimensions line and for the bare step number.
        label: 11, size: 9, step: 9, body: 11, title: 11, pad: 4
    )
    return (page, fixed + scrolling, scale, hidden)
}

/// A projected rect as SVG attributes, shifted into the diagram column.
func rectAttributes(_ r: CGRect) -> String {
    String(
        format: "x=\"%.2f\" y=\"%.2f\" width=\"%.2f\" height=\"%.2f\" rx=\"4\"",
        Float(r.minX) + layout.origin.x, Float(r.minY) + layout.origin.y,
        Float(r.width), Float(r.height)
    )
}

func text(_ x: Float, _ y: Float, _ body: String, size: Float = 12, fill: String = "#c9ced6",
          anchor: String = "start", family: String = "sans-serif") -> String {
    String(format: "  <text x=\"%.2f\" y=\"%.2f\" ", x, y)
        + "font-family=\"\(family)\" font-size=\"\(size)\" text-anchor=\"\(anchor)\" "
        + "fill=\"\(fill)\">\(escaped(body))</text>\n"
}

/// Whole centimetres, the shorter form the view uses inside a rectangle where the
/// millimetre in `PlanStats.lengthText` would not earn its width.
func wholeCentimetres(_ metres: Float) -> String { String(format: "%.0f cm", metres * 100) }

func layerSVG(
    _ plan: PackingPlan,
    layer: PlanLayer,
    of count: Int,
    protrusions: [Placement],
    flags: [String: String],
    title: String,
    header: [String],
    caption: String
) -> String {
    let band = layout.band ?? (30 + Float(header.count) * 17 + 24)
    let projection = FootprintProjection(footprint: plan.container.dimensions, in: layout.diagram)

    // The group's contents are built first: with the legend under the picture
    // rather than beside it, how tall the canvas has to be is not known until the
    // legend has been laid out.
    var out = ""

    // Container footprint, to scale.
    out += "  <rect \(rectAttributes(projection.footprintRect)) "
    out += "fill=\"#2a2e35\" stroke=\"#5d636d\" stroke-width=\"2\"/>\n"

    // Items from lower layers still standing in the way at this height: outlines
    // only, exactly as the view draws them, so the diagram cannot imply free floor
    // where a tall item from below is actually there.
    for placement in protrusions {
        let r = projection.rect(for: placement)
        out += "  <rect \(rectAttributes(r)) fill=\"none\" stroke=\"#8a8f98\" stroke-width=\"1.5\" "
        out += "stroke-dasharray=\"5 4\"/>\n"
        // The step number only, and inside the rect's own top-left corner. The view
        // draws no label at all here — the legend sentence names them — and the
        // full name centred under each outline was worse than nothing: two items
        // standing side by side in the end well have almost the same footprint
        // bottom edge, so the captions landed on each other and the right-hand one
        // ran off the container. The number is enough to match to the legend.
        out += text(
            Float(r.minX) + layout.origin.x + layout.pad,
            Float(r.minY) + layout.origin.y + layout.step + 4,
            "\(placement.step)", size: layout.step, fill: "#8a8f98", family: "monospace"
        )
    }

    // This layer's own items, in packing order, so a later step paints over an
    // earlier one — the same order the view's ZStack gives.
    for placement in layer.placements {
        let r = projection.rect(for: placement)
        let rgb = palette[(placement.step - 1) % palette.count]
        let colour = hex(rgb, 1)
        out += "  <rect \(rectAttributes(r)) fill=\"\(colour)\" fill-opacity=\"0.25\" "
        out += "stroke=\"\(colour)\" stroke-width=\"2\"/>\n"
        let x = Float(r.minX) + layout.origin.x + layout.pad
        let y = Float(r.minY) + layout.origin.y + layout.label + layout.pad
        let label = "\(placement.step). \(placement.label)"
        if let fit = labelLayout(r, label) {
            for (i, line) in fit.lines.enumerated() {
                out += text(x, y + Float(i) * (fit.size + 1), line, size: fit.size, fill: colour)
            }
            if sizeFits(r) {
                let size = "\(wholeCentimetres(placement.size.x)) × \(wholeCentimetres(placement.size.z))"
                let below = y + Float(fit.lines.count - 1) * (fit.size + 1) + layout.size + 4
                out += text(x, below, size, size: layout.size, fill: "#c9ced6", family: "monospace")
            }
        } else {
            out += text(x, y, "\(placement.step)", size: layout.step, fill: colour, family: "monospace")
        }
    }

    // The renderer's own flags, in the same red as the 3D mode and drawn after
    // every item for the same reason: in draw order an offender would be half
    // covered by whatever is packed over it. `PlanDiagramView` has no red outline
    // — it shows an orange `geometryIssues()` banner above the diagram instead —
    // so this, like the header band, is plan3d's annotation over the view's picture.
    for placement in layer.placements {
        guard let reason = flags[placement.itemID] else { continue }
        let r = projection.rect(for: placement)
        out += "  <rect \(rectAttributes(r)) fill=\"none\" stroke=\"#ff4d4d\" stroke-width=\"3\"/>\n"
        out += text(
            Float(r.midX) + layout.origin.x, Float(r.maxY) + layout.origin.y + 14, reason,
            size: layout.step, fill: "#ff4d4d", anchor: "middle", family: "monospace"
        )
    }

    // Legend, one row per item, then the protrusion sentence and the axis note —
    // the same three blocks, in the same order, as the view's legend. Beside the
    // picture on the desktop page; under it at a phone's width, where there is no
    // beside, which is also where the view puts it.
    let legendX = layout.legendX ?? 12
    let columns = layout.legendX == nil ? Int(Phone.content / (layout.size * 0.55)) : 34
    var y: Float = layout.legendX == nil
        ? layout.origin.y + Float(layout.diagram.height) + 30
        : 26
    for placement in layer.placements {
        let colour = hex(palette[(placement.step - 1) % palette.count], 1)
        out += String(format: "  <circle cx=\"%.1f\" cy=\"%.1f\" r=\"9\" ", legendX + 9, y - 4)
        out += "fill=\"\(colour)\"/>\n"
        out += text(legendX + 9, y, "\(placement.step)", size: layout.step, fill: "#16181c",
                    anchor: "middle", family: "monospace")
        out += text(legendX + 26, y - 4, placement.label, size: layout.label)
        for (i, line) in wrapped(placement.note, columns).enumerated() {
            out += text(legendX + 26, y + 10 + Float(i) * 12, line, size: layout.size, fill: "#8a8f98")
        }
        y += 26 + Float(wrapped(placement.note, columns).count) * 12
    }
    if !protrusions.isEmpty {
        let names = protrusions.map(\.label).joined(separator: ", ")
        let verb = protrusions.count == 1 ? "stands" : "stand"
        y += 8
        for line in wrapped("Dashed: \(names) \(verb) up through this layer.", columns + 4) {
            out += text(legendX, y, line, size: layout.step, fill: "#8a8f98")
            y += 13
        }
    }

    // 680 of footprint from y = 10, then a strip for the axis note. The note used
    // to sit at 690 and was half off the canvas and half on the container's bottom
    // wall — a to-scale footprint fills its column by construction, so nothing can
    // share those rows. `max` so a legend running under the picture pushes it down
    // instead of landing on it.
    let footer = max(layout.origin.y + Float(layout.diagram.height) + 18, y + 6)
    out += text(legendX, footer, "Seen from above · X across, Z down", size: 10, fill: "#6d727a")
    out += text(legendX, footer + 12, "origin at the footprint's top-left", size: 10, fill: "#6d727a")
    out += text(12, footer + (layout.legendX == nil ? 24 : 0),
                "layer \(layer.index + 1) of \(count)", size: 10, fill: "#6d727a",
                family: "monospace")

    let canvasHeight = band + footer + (layout.legendX == nil ? 38 : 26)
    var page = """
    <svg xmlns="http://www.w3.org/2000/svg" width="\(Int(layout.canvas))" \
    height="\(Int(canvasHeight))" \
    viewBox="0 0 \(Int(layout.canvas)) \(Int(canvasHeight))">
    <rect width="\(Int(layout.canvas))" height="\(Int(canvasHeight))" fill="#16181c"/>

    """
    page += text(12, 20, title, size: layout.title, fill: "#8a8f98", family: "monospace")
    for (i, line) in header.enumerated() {
        page += text(12, 40 + Float(i) * 17, line, size: layout.body)
    }
    // The layer's own caption sits directly above its picture, last line of the
    // band: the header above it describes the whole bag, this line describes only
    // what is drawn below.
    for block in layout.chrome {
        let tint = block.label == "layer picker" ? "#5d636d" : "#c08a3e"
        page += String(
            format: "<rect x=\"%.0f\" y=\"%.1f\" width=\"%.0f\" height=\"%.1f\" rx=\"6\" ",
            Phone.padding, block.y, Phone.content, block.height
        )
        page += "fill=\"\(tint)\" fill-opacity=\"0.12\" stroke=\"\(tint)\" stroke-width=\"1\" "
        page += "stroke-dasharray=\"4 3\"/>\n"
        page += text(Phone.padding + 8, block.y + 14,
                     "\(block.label) — \(Int(block.height)) pt", size: 10, fill: tint)
    }
    page += text(12, band - 6, caption, size: layout.body, fill: "#e8d654")
    if layout.legendX == nil {
        // Where the fixed chrome stops and the scrolling column starts.
        page += String(
            format: "<line x1=\"12\" y1=\"%.1f\" x2=\"%.1f\" y2=\"%.1f\" ",
            band - 1, layout.canvas - 12, band - 1
        )
        page += "stroke=\"#2c3038\" stroke-width=\"1\"/>\n"
    }
    // The bottom of the screen. Everything under it is real and reachable, but
    // only by scrolling — which is the shape of the new layout, and the one thing
    // a still picture would otherwise hide. Drawn after the group, not before it:
    // in the band it came out behind the container and the first big item, which
    // is exactly where it needs to be visible.
    var edge = ""
    if let fold = layout.fold {
        edge += String(
            format: "<line x1=\"0\" y1=\"%.1f\" x2=\"%.1f\" y2=\"%.1f\" ",
            fold, layout.canvas, fold
        )
        edge += "stroke=\"#e8d654\" stroke-width=\"1.5\" stroke-dasharray=\"8 5\"/>\n"
        // On a chip, because the fold lands wherever it lands — over the bag on a
        // plan with banners, in the middle of the legend without them — and a
        // caption sitting on someone else's sentence is how a picture stops being
        // evidence.
        let note = "bottom of the screen — scroll below here"
        let chip = Float(note.count) * 9 * 0.55 + 10
        edge += String(
            format: "<rect x=\"%.1f\" y=\"%.1f\" width=\"%.1f\" height=\"13\" rx=\"3\" ",
            layout.canvas - 12 - chip, fold - 15, chip
        )
        edge += "fill=\"#16181c\" stroke=\"#e8d654\" stroke-opacity=\"0.5\"/>\n"
        edge += text(layout.canvas - 17, fold - 5, note, size: 9, fill: "#e8d654", anchor: "end")
    }
    page += "<g transform=\"translate(0,\(Int(band)))\">\n"

    return page + out + "</g>\n" + edge + "</svg>\n"
}

// MARK: - The rest of the server document

// `solver.unpacked` and `validation.violations` sit beside the plan in the
// server document, not inside `PackingPlan`, so they are read straight out of
// the JSON. Absent key = nil, and nothing is drawn: an empty shelf on a raw
// solver plan would claim the solver fit everything.

func unpackedItems(_ document: Any?) -> [(id: String, reason: String)]? {
    guard let root = document as? [String: Any],
          let solver = root["solver"] as? [String: Any],
          let list = solver["unpacked"] as? [[String: Any]]
    else { return nil }
    return list.map {
        (id: $0["id"] as? String ?? "?", reason: $0["reason"] as? String ?? "no reason given")
    }
}

/// Violation type keyed by the item it names. An entry naming a pair (a
/// collision) is recorded against both items.
func violationLabels(_ document: Any?) -> [String: String]? {
    guard let root = document as? [String: Any],
          let validation = root["validation"] as? [String: Any],
          let list = validation["violations"] as? [[String: Any]]
    else { return nil }
    var byItem: [String: [String]] = [:]
    for entry in list {
        let type = entry["type"] as? String ?? "VIOLATION"
        var ids = entry["objects"] as? [String] ?? []
        if let single = entry["object"] as? String { ids.append(single) }
        for id in ids { byItem[id, default: []].append(type) }
    }
    return byItem.mapValues { $0.joined(separator: " / ") }
}

// MARK: - main

let args = CommandLine.arguments.dropFirst()
let steps = args.contains("--steps")
let cutaway = args.contains("--cutaway")
let wantUnpacked = args.contains("--unpacked")
let wantViolations = args.contains("--violations")
let wantLayers = args.contains("--layers")
let wantDevice = args.contains("--device")
let positional = args.filter { !$0.hasPrefix("--") }
guard positional.count == 2 else {
    FileHandle.standardError.write(Data(
        ("usage: plan3d <plan.json> <out-dir> [--steps] [--cutaway] [--unpacked] "
            + "[--violations] [--layers [--device]]\n").utf8
    ))
    exit(2)
}

let data = try Data(contentsOf: URL(fileURLWithPath: positional[positional.startIndex]))
// The server wraps the plan under `plan`; the solver can also emit it raw.
let loaded = try (try? PlanLoader.plan(fromServerDocument: data)) ?? PlanLoader.plan(from: data)
let document = try? JSONSerialization.jsonObject(with: data)

let outDir = URL(fileURLWithPath: positional[positional.index(after: positional.startIndex)])
try FileManager.default.createDirectory(at: outDir, withIntermediateDirectories: true)

let cameras: [(String, PlanCamera)] = [
    ("three-quarter", .default),
    ("front", PlanCamera(yaw: 0, pitch: 0, zoom: 1)),
    ("top", PlanCamera(yaw: 0.6, pitch: 1.4, zoom: 1)),
]

// Every number on the picture comes from here. Always built from the plan as
// loaded, never from the exploded copy: `--cutaway` grows the container, which
// would put a smaller fill fraction and a fictitious free block on the render.
let stats = PlanStats(plan: loaded)

let (nests, nestNotes, nestFlags) = declaredNests(document, plan: loaded)
var violations = nestFlags

/// Two violations against the same item read as one line.
func flag(_ itemID: String, _ text: String) {
    violations[itemID] = violations[itemID].map { "\($0) / \(text)" } ?? text
}

// The contract's own check, and the reason it is not optional: the field says
// "this overlap is deliberate", so a consumer that trusts it without verifying
// the cell relabels any collision the solver missed as a feature. Not gated on
// `--violations`: that flag reads someone else's verdict out of the document,
// this is the renderer refusing to draw a claim it just disproved.
for nest in nests {
    guard let placement = loaded.placements.first(where: { $0.itemID == nest.itemID }) else { continue }
    let outside = placement.box.overshoot(outOf: nest.cavity)
    if let worst = Axis.allCases.max(by: { outside[$0] < outside[$1] }), outside[worst] > 0 {
        flag(nest.itemID, "OUTSIDE CAVITY by \(PlanStats.lengthText(outside[worst])) on \(worst)")
    }
    if let host = loaded.placements.first(where: { $0.itemID == nest.hostID }),
       nest.cavity.overshoot(outOf: host.box) != .zero {
        flag(nest.itemID, "CAVITY OUTSIDE \(nest.hostID)")
    }
}

// A nested pair intersects by design, and `geometryIssues()` knows nothing about
// nesting, so its `intersection` for a verified nest is annotated rather than
// dropped — an unexplained overlap at the top of every nested run trains you to
// ignore the line that reports the real ones.
let verified = Set(nests.filter { violations[$0.itemID] == nil }.map { Set([$0.itemID, $0.hostID]) })
let issues = loaded.geometryIssues().map { issue -> String in
    if case let .intersection(a, b, _) = issue, verified.contains(Set([a, b])) {
        return "\(issue.description)  [declared nest, inside the cavity]"
    }
    return issue.description
}
print("plan: \(loaded.placements.count) placements, container \(loaded.container.dimensions), "
    + "geometry issues: \(issues.isEmpty ? "none" : issues.joined(separator: "; "))")

if nests.isEmpty && nestNotes.isEmpty && violations.isEmpty {
    print("nesting: no placement declares nestedIn — no cavities drawn, every item a plain solid")
}
for nest in nests {
    print("nesting: \(nest.itemID) in \(nest.hostID), cavity \(nest.cavity)")
}
for note in nestNotes { print("nesting: \(note)") }

var plan = loaded
var mode = ""
var drawnNests = nests
if cutaway {
    plan = exploded(loaded, layers: stats.layers, nests: nests)
    drawnNests = explodedCavities(nests, original: loaded, opened: plan)
    mode += "  cutaway(\(stats.layers.count) layer\(stats.layers.count == 1 ? "" : "s"))"
    if stats.layers.count < 2 {
        print("cutaway: every placement sits on the floor — one layer, so nothing to pull apart")
    }
    // Only worth saying when it is true, and when it is, it explains why an item
    // is drawn in a slab whose caption does not count it.
    let layerOf = Dictionary(
        stats.layers.flatMap { layer in layer.placements.map { ($0.itemID, layer.index) } },
        uniquingKeysWith: { a, _ in a }
    )
    for nest in nests where layerOf[nest.itemID] != layerOf[nest.hostID] {
        print("cutaway: \(nest.itemID) is counted in layer \((layerOf[nest.itemID] ?? 0) + 1) "
            + "but drawn with its host in layer \((layerOf[nest.hostID] ?? 0) + 1)")
    }
}

var unpacked: [(id: String, reason: String)] = []
if wantUnpacked {
    switch unpackedItems(document) {
    case nil:
        print("unpacked: no solver.unpacked in this document — drawing no shelf")
    case let items? where items.isEmpty:
        print("unpacked: solver left nothing out — drawing no shelf")
    case let items?:
        unpacked = items
        print("unpacked: \(items.count) — \(items.map(\.id).joined(separator: ", "))")
        mode += "  unpacked(\(items.count))"
    }
}

if wantViolations {
    switch violationLabels(document) {
    case nil:
        print("violations: no validation.violations in this document")
    case let byItem? where byItem.isEmpty:
        print("violations: none")
    case let byItem?:
        for (id, label) in byItem { flag(id, label) }
        let named = Set(plan.placements.map(\.itemID))
        let orphans = byItem.keys.filter { !named.contains($0) }.sorted()
        print("violations: \(byItem.count) item(s) — "
            + byItem.keys.sorted().map { "\($0)=\(byItem[$0]!)" }.joined(separator: ", "))
        if !orphans.isEmpty {
            print("violations: not in the plan, so not drawn: \(orphans.joined(separator: ", "))")
        }
    }
}
if !violations.isEmpty { mode += "  violations(\(violations.count))" }

// Formatting is `PlanStats`'s job, not this tool's — every string here is one of
// its display properties.
var header = [
    "\(stats.fullnessText) · \(stats.layerCountText) · \(stats.floorCoverageText)",
    stats.gapText,
]
if !stats.orderText.isEmpty { header.append(stats.orderText.joined(separator: " · ")) }
if !nests.isEmpty {
    header.append("Nested: " + nests.map { "\($0.itemID) in \($0.hostLabel)" }.joined(separator: ", "))
}
// Per-layer captions for `--cutaway`, in the header band rather than pinned to
// each slab. Pinning was tried first and lied: the highest screen point of a
// layer is its *rear* corner, which the layer above stands in front of, so the
// captions came out in the wrong vertical order and two landed on the same
// pixels. Here they read 1..n against a picture that stacks 1..n bottom-up.
if cutaway {
    let nestedItems = Set(nests.map(\.itemID))
    for layer in stats.layers {
        let away = layer.placements.filter { nestedItems.contains($0.itemID) }.count
        // Saying so keeps the list honest: a layer whose items are all nested has
        // no slab of its own in the picture at all.
        header.append(layer.summaryText + (away > 0 ? " · \(away) nested, drawn with the host" : ""))
    }
}

// The 2D fallback. Its own output entirely: one SVG per layer, no cameras, and
// always from the plan as loaded — `--cutaway` grows the container, which a
// to-scale footprint cannot survive, and pulling layers apart is what this mode
// does by construction anyway.
if wantLayers {
    if cutaway {
        print("layers: --cutaway is a 3D mode — the layer diagram already separates the layers, ignored")
    }
    let layers = loaded.layers()
    let layerOf = Dictionary(
        layers.flatMap { layer in layer.placements.map { ($0.itemID, layer.index) } },
        uniquingKeysWith: { a, _ in a }
    )
    // The library groups layers twice — `PackingPlan.layers()` in PackingPlanUI for
    // the view, `PackingPlan.layerStats()` in PackingPlan for the numbers — with
    // the same rule and the same 5 mm tolerance restated in each. Saying so when
    // they disagree is the cheapest possible guard on that duplication.
    if layers.count != stats.layers.count {
        print("layers: WARNING the view's grouping says \(layers.count) layers and "
            + "PlanStats says \(stats.layers.count) — the two 5 mm rules have drifted apart")
    }
    for nest in nests where layerOf[nest.itemID] != layerOf[nest.hostID] {
        print("layers: \(nest.itemID) is drawn in layer \((layerOf[nest.itemID] ?? 0) + 1) and its "
            + "host \(nest.hostID) in layer \((layerOf[nest.hostID] ?? 0) + 1) — the nest is split "
            + "across two pictures, as the view splits it")
    }

    guard !layers.isEmpty else {
        print("layers: no placements — the view shows 'This plan has no placements.' and no diagram")
        exit(0)
    }

    // The one line the view puts above the diagram, built with the same formatter
    // its own `centimetres(_:)` is (`%.1f cm`, which is `PlanStats.lengthText`).
    // In `--device` this replaces plan3d's stats band, because the view has no
    // stats band: those numbers are inside its collapsed "Pack details" row, so
    // drawing four of them at a phone's width would be measuring furniture that is
    // not on the screen.
    let d = loaded.container.dimensions
    let deviceHeader = [
        "\(PlanStats.lengthText(d.x)) × \(PlanStats.lengthText(d.y)) × \(PlanStats.lengthText(d.z))"
            + " interior · \(loaded.placements.count) items · \(stats.fullnessText)"
    ]
    // Both banners, because on the device both take height from the diagram before
    // it is drawn, and a wrong plan is exactly when the fallback is being read.
    let stability = loaded.stabilityIssues().map(\.description)
    if wantDevice {
        print("device: \(Int(Phone.screen.x))×\(Int(Phone.screen.y)) pt screen, "
            + "\(Int(Phone.content)) pt column inside the view's \(Int(Phone.padding)) pt padding, "
            + "\(Int(Phone.frame)) pt of frame height (sheet at .large: "
            + "\(Int(Phone.screen.y)) − \(Int(Phone.safeTop)) top safe − \(Int(Phone.sheetPeek)) peek "
            + "− \(Int(Phone.safeBottom)) home indicator)")
        print("device: banners push the diagram down the scroll column — "
            + "\(issues.count) geometry, \(stability.count) stability — they scroll, "
            + "they no longer shrink the picture (packing-core af90f89)")
    }

    for layer in layers {
        let protrusions = loaded.protrusions(into: layer)
        let items = layer.placements.count == 1 ? "1 item" : "\(layer.placements.count) items"
        let caption = "Layer \(layer.index + 1) of \(layers.count) · floor at "
            + "\(PlanStats.lengthText(layer.floorY)) · "
            + "\(PlanStats.lengthText(layer.thickness)) thick · \(items)"
        // `summaryText` is PlanStats' line, the same one the 3D `--cutaway` header
        // prints, so the two modes cannot describe the same layer differently — and
        // it is the string the view's own caption shows, which is why `--device`
        // draws and measures this one rather than plan3d's longer line above.
        let summary = stats.layers.indices.contains(layer.index)
            ? stats.layers[layer.index].summaryText
            : caption
        let shown = wantDevice ? summary : caption

        var scale = Float(
            FootprintProjection(footprint: d, in: desktopLayout.diagram).scale
        )
        layout = desktopLayout
        if wantDevice {
            let phone = phoneLayout(
                loaded, layer: layer, layers: layers.count, protrusions: protrusions,
                header: deviceHeader[0], caption: shown, issues: issues, stability: stability
            )
            layout = phone.layout
            scale = phone.scale
            print("layer \(layer.index + 1) budget: "
                + phone.rows.filter { $0.1 > 0 }.map { "\($0.0) \(Int($0.1))" }
                    .joined(separator: ", "))
            print("layer \(layer.index + 1) scroll: "
                + (phone.scrollToSeeItAll <= 0
                    ? "the whole diagram is on screen at rest"
                    : "\(Int(phone.scrollToSeeItAll)) pt of the diagram is below the fold — "
                        + "the bag is full size, you scroll to reach the bottom of it"))
        }
        // Per item: the rectangle the projection gives it, and what text survives in
        // it. This is the whole question `--device` exists to answer, so it is a
        // table on the terminal and not only something to squint at in the picture.
        let projection = FootprintProjection(footprint: d, in: layout.diagram)
        print(String(
            format: "layer %d scale: %.0f pt/m — footprint %.0f × %.0f pt in a %.0f × %.0f box",
            layer.index + 1, scale,
            Float(projection.footprintRect.width), Float(projection.footprintRect.height),
            Float(layout.diagram.width), Float(layout.diagram.height)
        ))
        for placement in layer.placements {
            let r = projection.rect(for: placement)
            let verdict = legibility(r, "\(placement.step). \(placement.label)")
            let name = placement.label.padding(toLength: 16, withPad: " ", startingAt: 0)
            let box = String(format: "%5.1f × %5.1f pt", Float(r.width), Float(r.height))
            let seen = verdict.view.padding(toLength: 10, withPad: " ", startingAt: 0)
            print("  \(placement.step). \(name) \(box)  view: \(seen) svg: \(verdict.svg)")
        }

        let name = String(format: "\(wantDevice ? "device-" : "")layer-%02d.svg", layer.index + 1)
        try layerSVG(
            loaded,
            layer: layer,
            of: layers.count,
            protrusions: protrusions,
            flags: violations,
            title: wantDevice
                ? String(format: "device %d×%d pt · %.0f pt/m · %d pt of chrome above",
                         Int(Phone.screen.x), Int(Phone.screen.y), scale, Int(layout.band ?? 0))
                : "top-down layer \(layer.index + 1)/\(layers.count)  "
                    + "\(loaded.container.label)\(violations.isEmpty ? "" : "  violations(\(violations.count))")",
            header: wantDevice ? deviceHeader : header,
            caption: shown
        ).write(to: outDir.appendingPathComponent(name), atomically: true, encoding: .utf8)

        let through = protrusions.isEmpty
            ? "nothing from below"
            : "\(protrusions.count) up through the floor: \(protrusions.map(\.label).joined(separator: ", "))"
        print("\(name)  \(summary) · \(through)")
    }
    exit(0)
}

if wantDevice {
    print("device: --device is a scale for the 2D layer diagram — add --layers; the 3D "
        + "cameras are not what the demo falls back to and are not drawn at device size")
}

for (name, camera) in cameras {
    var notes = Annotations(
        violations: violations,
        unpacked: unpacked,
        nests: drawnNests,
        cavities: projectedCavities(drawnNests, container: plan.container, camera: camera),
        header: header
    )
    let boxes = nestedInFront(plan.projected(camera: camera, size: canvas, upTo: nil), nests)
    try write(
        boxes,
        to: outDir.appendingPathComponent("\(name).svg"),
        title: "\(name)  yaw=\(camera.yaw) pitch=\(camera.pitch) zoom=\(camera.zoom)\(mode)",
        notes
    )

    if steps && name == cameras[0].0 {
        for step in 1...plan.placements.count {
            let packed = Set(plan.placements.filter { $0.step <= step }.map(\.itemID))
            // A cavity whose item is not in yet is not a cell anyone can check.
            notes.nests = drawnNests.filter { packed.contains($0.itemID) }
            try write(
                nestedInFront(plan.projected(camera: camera, size: canvas, upTo: step), nests),
                to: outDir.appendingPathComponent(String(format: "step-%02d.svg", step)),
                title: "three-quarter  steps 1...\(step)\(mode)",
                notes
            )
        }
    }
}
