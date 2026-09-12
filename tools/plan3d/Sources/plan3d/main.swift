import Foundation
import PackingPlan

// plan3d — render a PackingPlan to SVG so the 3D projection can be inspected on
// a machine with no Mac and no simulator. See docs/PLAN_3D.md.
//
//   swift run plan3d <plan.json> <out-dir> [--steps]

let width: Float = 900
let height: Float = 700
let canvas = SIMD2<Float>(width, height)

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

func svg(_ boxes: [ProjectedBox], title: String) -> String {
    var out = """
    <svg xmlns="http://www.w3.org/2000/svg" width="\(Int(width))" height="\(Int(height))" \
    viewBox="0 0 \(Int(width)) \(Int(height))">
    <rect width="\(Int(width))" height="\(Int(height))" fill="#16181c"/>
    <text x="12" y="24" font-family="monospace" font-size="14" fill="#8a8f98">\(escaped(title))</text>

    """
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
    }
    return out + "</svg>\n"
}

func write(_ boxes: [ProjectedBox], to url: URL, title: String) throws {
    try svg(boxes, title: title).write(to: url, atomically: true, encoding: .utf8)

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

// MARK: - main

let args = CommandLine.arguments.dropFirst()
let steps = args.contains("--steps")
let positional = args.filter { !$0.hasPrefix("--") }
guard positional.count == 2 else {
    FileHandle.standardError.write(Data("usage: plan3d <plan.json> <out-dir> [--steps]\n".utf8))
    exit(2)
}

let data = try Data(contentsOf: URL(fileURLWithPath: positional[positional.startIndex]))
// The server wraps the plan under `plan`; the solver can also emit it raw.
let plan = try (try? PlanLoader.plan(fromServerDocument: data)) ?? PlanLoader.plan(from: data)

let outDir = URL(fileURLWithPath: positional[positional.index(after: positional.startIndex)])
try FileManager.default.createDirectory(at: outDir, withIntermediateDirectories: true)

let cameras: [(String, PlanCamera)] = [
    ("three-quarter", .default),
    ("front", PlanCamera(yaw: 0, pitch: 0, zoom: 1)),
    ("top", PlanCamera(yaw: 0.6, pitch: 1.4, zoom: 1)),
]

let issues = plan.geometryIssues()
print("plan: \(plan.placements.count) placements, container \(plan.container.dimensions), "
    + "geometry issues: \(issues.isEmpty ? "none" : issues.map(\.description).joined(separator: "; "))")

for (name, camera) in cameras {
    try write(
        plan.projected(camera: camera, size: canvas, upTo: nil),
        to: outDir.appendingPathComponent("\(name).svg"),
        title: "\(name)  yaw=\(camera.yaw) pitch=\(camera.pitch) zoom=\(camera.zoom)"
    )
}

if steps {
    for step in 1...plan.placements.count {
        try write(
            plan.projected(camera: .default, size: canvas, upTo: step),
            to: outDir.appendingPathComponent(String(format: "step-%02d.svg", step)),
            title: "three-quarter  steps 1...\(step)"
        )
    }
}
