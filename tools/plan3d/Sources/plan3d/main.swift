import Foundation
import PackingPlan
import PackingPlanUI

// plan3d — render a PackingPlan to SVG so the 3D projection and the 2D fallback
// diagram can be inspected on a machine with no Mac and no simulator. See
// docs/PLAN_3D.md.
//
//   swift run plan3d <plan.json> <out-dir> [--steps] [--cutaway] [--unpacked]
//                                          [--violations] [--layers]

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

/// The footprint gets the left column; the legend gets the rest of the 900.
let diagramSize = CGSize(width: 560, height: 680)
let diagramOrigin = SIMD2<Float>(20, 10)
let legendX: Float = 600

/// `PlanDiagramView`'s own thresholds, below which the text does not fit the
/// rectangle and the legend carries the label instead — plus a width check the
/// view does not need. SwiftUI wraps the label to two lines and shrinks it to fit
/// its box; SVG `<text>` does neither, so a long label in a small rect spills
/// across its neighbours (`8. Charger pouch` in a 67 px box, in the fixture).
/// ~0.55 em per character at this font, which is close enough for a threshold.
func labelFits(_ r: CGRect, _ label: String) -> Bool {
    r.width >= 54 && r.height >= 28 && Float(label.count) * 6.6 <= Float(r.width) - 12
}
func sizeFits(_ r: CGRect) -> Bool { r.width >= 74 && r.height >= 44 }

/// A projected rect as SVG attributes, shifted into the diagram column.
func rectAttributes(_ r: CGRect) -> String {
    String(
        format: "x=\"%.2f\" y=\"%.2f\" width=\"%.2f\" height=\"%.2f\" rx=\"4\"",
        Float(r.minX) + diagramOrigin.x, Float(r.minY) + diagramOrigin.y,
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
    let band: Float = 30 + Float(header.count) * 17 + 24
    // 680 of footprint from y = 10, then a strip for the axis note. The note used
    // to sit at 690 and was half off the canvas and half on the container's bottom
    // wall — a to-scale footprint fills its column by construction, so nothing can
    // share those rows.
    let footer = diagramOrigin.y + Float(diagramSize.height) + 18
    let canvasHeight = band + footer + 26
    let projection = FootprintProjection(footprint: plan.container.dimensions, in: diagramSize)

    var out = """
    <svg xmlns="http://www.w3.org/2000/svg" width="\(Int(width))" height="\(Int(canvasHeight))" \
    viewBox="0 0 \(Int(width)) \(Int(canvasHeight))">
    <rect width="\(Int(width))" height="\(Int(canvasHeight))" fill="#16181c"/>

    """
    out += text(12, 20, title, size: 14, fill: "#8a8f98", family: "monospace")
    for (i, line) in header.enumerated() {
        out += text(12, 40 + Float(i) * 17, line, size: 13)
    }
    // The layer's own caption sits directly above its picture, last line of the
    // band: the header above it describes the whole bag, this line describes only
    // what is drawn below.
    out += text(12, band - 6, caption, size: 13, fill: "#e8d654")
    out += "<g transform=\"translate(0,\(Int(band)))\">\n"

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
            Float(r.minX) + diagramOrigin.x + 6, Float(r.minY) + diagramOrigin.y + 15,
            "\(placement.step)", size: 11, fill: "#8a8f98", family: "monospace"
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
        let x = Float(r.minX) + diagramOrigin.x + 6
        let y = Float(r.minY) + diagramOrigin.y + 16
        let label = "\(placement.step). \(placement.label)"
        if labelFits(r, label) {
            out += text(x, y, label, size: 12, fill: colour)
            if sizeFits(r) {
                let size = "\(wholeCentimetres(placement.size.x)) × \(wholeCentimetres(placement.size.z))"
                out += text(x, y + 14, size, size: 10, fill: "#c9ced6", family: "monospace")
            }
        } else {
            out += text(x, y, "\(placement.step)", size: 11, fill: colour, family: "monospace")
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
            Float(r.midX) + diagramOrigin.x, Float(r.maxY) + diagramOrigin.y + 14, reason,
            size: 11, fill: "#ff4d4d", anchor: "middle", family: "monospace"
        )
    }

    // Legend, one row per item, then the protrusion sentence and the axis note —
    // the same three blocks, in the same order, as the view's legend.
    var y: Float = 26
    for placement in layer.placements {
        let colour = hex(palette[(placement.step - 1) % palette.count], 1)
        out += String(format: "  <circle cx=\"%.1f\" cy=\"%.1f\" r=\"9\" ", legendX + 9, y - 4)
        out += "fill=\"\(colour)\"/>\n"
        out += text(legendX + 9, y, "\(placement.step)", size: 11, fill: "#16181c",
                    anchor: "middle", family: "monospace")
        out += text(legendX + 26, y - 4, placement.label, size: 12)
        for (i, line) in wrapped(placement.note, 34).enumerated() {
            out += text(legendX + 26, y + 10 + Float(i) * 12, line, size: 10, fill: "#8a8f98")
        }
        y += 26 + Float(wrapped(placement.note, 34).count) * 12
    }
    if !protrusions.isEmpty {
        let names = protrusions.map(\.label).joined(separator: ", ")
        let verb = protrusions.count == 1 ? "stands" : "stand"
        y += 8
        for line in wrapped("Dashed: \(names) \(verb) up through this layer.", 38) {
            out += text(legendX, y, line, size: 11, fill: "#8a8f98")
            y += 13
        }
    }
    out += text(legendX, footer, "Seen from above · X across, Z down", size: 10, fill: "#6d727a")
    out += text(legendX, footer + 12, "origin at the footprint's top-left", size: 10, fill: "#6d727a")
    out += text(12, footer, "layer \(layer.index + 1) of \(count)", size: 10, fill: "#6d727a",
                family: "monospace")

    return out + "</g>\n</svg>\n"
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
let positional = args.filter { !$0.hasPrefix("--") }
guard positional.count == 2 else {
    FileHandle.standardError.write(Data(
        ("usage: plan3d <plan.json> <out-dir> [--steps] [--cutaway] [--unpacked] "
            + "[--violations] [--layers]\n").utf8
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

    for layer in layers {
        let protrusions = loaded.protrusions(into: layer)
        let items = layer.placements.count == 1 ? "1 item" : "\(layer.placements.count) items"
        let caption = "Layer \(layer.index + 1) of \(layers.count) · floor at "
            + "\(PlanStats.lengthText(layer.floorY)) · "
            + "\(PlanStats.lengthText(layer.thickness)) thick · \(items)"
        let name = String(format: "layer-%02d.svg", layer.index + 1)
        try layerSVG(
            loaded,
            layer: layer,
            of: layers.count,
            protrusions: protrusions,
            flags: violations,
            title: "top-down layer \(layer.index + 1)/\(layers.count)  "
                + "\(loaded.container.label)\(violations.isEmpty ? "" : "  violations(\(violations.count))")",
            header: header,
            caption: caption
        ).write(to: outDir.appendingPathComponent(name), atomically: true, encoding: .utf8)

        // `summaryText` is PlanStats' line, the same one the 3D `--cutaway` header
        // prints, so the two modes cannot describe the same layer differently.
        let summary = stats.layers.indices.contains(layer.index)
            ? stats.layers[layer.index].summaryText
            : caption
        let through = protrusions.isEmpty
            ? "nothing from below"
            : "\(protrusions.count) up through the floor: \(protrusions.map(\.label).joined(separator: ", "))"
        print("\(name)  \(summary) · \(through)")
    }
    exit(0)
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
