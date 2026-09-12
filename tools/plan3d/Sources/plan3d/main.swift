import Foundation
import PackingPlan

// plan3d — render a PackingPlan to SVG so the 3D projection can be inspected on
// a machine with no Mac and no simulator. See docs/PLAN_3D.md.
//
//   swift run plan3d <plan.json> <out-dir> [--steps] [--cutaway] [--unpacked] [--violations]

let width: Float = 900
let height: Float = 700
let canvas = SIMD2<Float>(width, height)

/// Extra canvas to the right of the bag, used only by `--unpacked`.
let shelfWidth: Float = 300

// Fixed palette by step; the container is grey. Colour identifies the item,
// `shade` from the projection identifies the face direction.
let palette: [(UInt8, UInt8, UInt8)] = [
    (232, 93, 82), (240, 168, 63), (233, 214, 84), (126, 200, 108),
    (86, 180, 205), (110, 133, 224), (176, 116, 214), (226, 118, 172),
]

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

func svg(
    _ boxes: [ProjectedBox],
    title: String,
    violations: [String: String] = [:],
    unpacked: [(id: String, reason: String)] = []
) -> String {
    let total = width + (unpacked.isEmpty ? 0 : shelfWidth)
    var out = """
    <svg xmlns="http://www.w3.org/2000/svg" width="\(Int(total))" height="\(Int(height))" \
    viewBox="0 0 \(Int(total)) \(Int(height))">
    <rect width="\(Int(total))" height="\(Int(height))" fill="#16181c"/>
    <text x="12" y="24" font-family="monospace" font-size="14" fill="#8a8f98">\(escaped(title))</text>

    """
    var flags = ""
    for box in boxes {
        let isContainer = box.step == 0
        let rgb = isContainer ? (150, 156, 165) : palette[(box.step - 1) % palette.count]
        out += "<g id=\"\(escaped(box.id))\">\n"
        for face in box.faces {
            out += "  <polygon points=\"\(points(face.corners))\" fill=\"\(hex(rgb, face.shade))\""
            out += isContainer ? " fill-opacity=\"0.45\"/>\n" : "/>\n"
        }
        out += "  <polygon points=\"\(points(box.outline))\" fill=\"none\" "
        out += "stroke=\"\(isContainer ? "#5d636d" : "#0d0e10")\" stroke-width=\"\(isContainer ? 2 : 1)\"/>\n"
        if !isContainer {
            let at = String(format: "x=\"%.2f\" y=\"%.2f\"", box.center.x, box.center.y)
            out += "  <text \(at) font-family=\"sans-serif\" font-size=\"12\" text-anchor=\"middle\" "
            out += "fill=\"#0d0e10\" stroke=\"#ffffff\" stroke-width=\"2.5\" paint-order=\"stroke\">"
            out += escaped("\(box.step). \(box.label)") + "</text>\n"
        }
        out += "</g>\n"

        // Collected and drawn after every box, on purpose: a violation marked in
        // painter order would be half-covered by whatever is packed in front of
        // the offending item, which is exactly the case you need to see.
        if let reason = violations[box.id] {
            flags += "  <polygon points=\"\(points(box.outline))\" fill=\"none\" "
            flags += "stroke=\"#ff4d4d\" stroke-width=\"3\"/>\n"
            let at = String(format: "x=\"%.2f\" y=\"%.2f\"", box.center.x, box.center.y + 16)
            flags += "  <text \(at) font-family=\"monospace\" font-size=\"11\" text-anchor=\"middle\" "
            flags += "fill=\"#ff4d4d\" stroke=\"#0d0e10\" stroke-width=\"2.5\" paint-order=\"stroke\">"
            flags += escaped(reason) + "</text>\n"
        }
    }
    if !flags.isEmpty { out += "<g id=\"violations\">\n" + flags + "</g>\n" }
    return out + (unpacked.isEmpty ? "" : shelf(unpacked)) + "</svg>\n"
}

func write(
    _ boxes: [ProjectedBox],
    to url: URL,
    title: String,
    violations: [String: String] = [:],
    unpacked: [(id: String, reason: String)] = []
) throws {
    try svg(boxes, title: title, violations: violations, unpacked: unpacked)
        .write(to: url, atomically: true, encoding: .utf8)

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
/// Every distinct floor height in the plan is a layer, and each layer is lifted
/// until it clears the tallest item of the one below plus a margin — a flat gap
/// would let a lifted layer straddle a bottle standing on the layer under it and
/// hide it worse than before. The container grows by the same total, which keeps
/// the scale-to-fit and the containment read honest: the same bag, opened up.
/// The container is already drawn from the inside (its near walls are culled by
/// the projection), so there is nothing further to cut away there.
func exploded(_ plan: PackingPlan) -> (plan: PackingPlan, layers: Int) {
    let floors = Set(plan.placements.map(\.position.y)).sorted()
    let margin = plan.container.dimensions.y * 0.15
    var lifts: [Float: Float] = [:]
    var running: Float = 0
    for (k, floor) in floors.enumerated() where k > 0 {
        let below = floors[k - 1]
        let top = plan.placements
            .filter { $0.position.y == below }
            .map { $0.position.y + $0.size.y }
            .max() ?? below
        running += max(0, top - floor) + margin
        lifts[floor] = running
    }

    let placements = plan.placements.map { p -> Placement in
        let lift = lifts[p.position.y] ?? 0
        return Placement(
            step: p.step,
            itemID: p.itemID,
            label: p.label,
            zone: p.zone,
            position: Vector3(p.position.x, p.position.y + lift, p.position.z),
            size: p.size,
            rotation: p.rotation,
            note: p.note
        )
    }
    let dimensions = Vector3(
        plan.container.dimensions.x,
        plan.container.dimensions.y + running,
        plan.container.dimensions.z
    )
    let container = Container(
        id: plan.container.id,
        label: plan.container.label,
        dimensions: dimensions,
        zones: plan.container.zones
    )
    return (
        PackingPlan(
            version: plan.version,
            units: plan.units,
            container: container,
            placements: placements
        ),
        floors.count
    )
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
let positional = args.filter { !$0.hasPrefix("--") }
guard positional.count == 2 else {
    FileHandle.standardError.write(Data(
        "usage: plan3d <plan.json> <out-dir> [--steps] [--cutaway] [--unpacked] [--violations]\n".utf8
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

let issues = loaded.geometryIssues()
print("plan: \(loaded.placements.count) placements, container \(loaded.container.dimensions), "
    + "geometry issues: \(issues.isEmpty ? "none" : issues.map(\.description).joined(separator: "; "))")

var plan = loaded
var mode = ""
if cutaway {
    let (opened, layers) = exploded(loaded)
    plan = opened
    mode += "  cutaway(\(layers) layer\(layers == 1 ? "" : "s"))"
    if layers < 2 {
        print("cutaway: every placement sits on the floor — one layer, so nothing to pull apart")
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

var violations: [String: String] = [:]
if wantViolations {
    switch violationLabels(document) {
    case nil:
        print("violations: no validation.violations in this document")
    case let byItem? where byItem.isEmpty:
        print("violations: none")
    case let byItem?:
        violations = byItem
        let named = Set(plan.placements.map(\.itemID))
        let orphans = byItem.keys.filter { !named.contains($0) }.sorted()
        print("violations: \(byItem.count) item(s) — "
            + byItem.keys.sorted().map { "\($0)=\(byItem[$0]!)" }.joined(separator: ", "))
        if !orphans.isEmpty {
            print("violations: not in the plan, so not drawn: \(orphans.joined(separator: ", "))")
        }
        mode += "  violations(\(byItem.count))"
    }
}

for (name, camera) in cameras {
    try write(
        plan.projected(camera: camera, size: canvas, upTo: nil),
        to: outDir.appendingPathComponent("\(name).svg"),
        title: "\(name)  yaw=\(camera.yaw) pitch=\(camera.pitch) zoom=\(camera.zoom)\(mode)",
        violations: violations,
        unpacked: unpacked
    )
}

if steps {
    for step in 1...plan.placements.count {
        try write(
            plan.projected(camera: .default, size: canvas, upTo: step),
            to: outDir.appendingPathComponent(String(format: "step-%02d.svg", step)),
            title: "three-quarter  steps 1...\(step)\(mode)",
            violations: violations,
            unpacked: unpacked
        )
    }
}
