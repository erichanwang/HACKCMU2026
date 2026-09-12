// packphysics: thin CLI over the PackPhysics library. Mirrors
// physics/__main__.py (the Python prototype's CLI) so either can be swapped
// for the other by teammates. No physics logic lives here -- every
// subcommand just parses args, calls into PackPhysics, and prints JSON.
//
//     packphysics validate scene.json [--placements placements.json] [--pretty]
//     packphysics example
//     packphysics scan-to-object item.json [--position x y z]
//     packphysics bench [--objects N] [--repeat R]

import Foundation
import PackPhysics

private let usage = """
Usage: packphysics <command> [args]

Commands:
  validate <scene.json> [--placements <p.json>] [--pretty]
      Validate a scene, applying placements first if given. Prints the
      ValidationResult as JSON on stdout.
      Exit 0 if valid, 1 if invalid, 2 if input could not be read.
  example
      Print the carry-on example scene (7 objects) as JSON.
  scan-to-object <item.json> [--position x y z]
      Decode a cm-based ScannedItem JSON and print the resulting
      SceneObject JSON (metres).
  bench [--objects N] [--repeat R]
      Benchmark validateLayout on sparse/touching/dense grids of N objects
      (default N in 5, 10, 20, 40; default 30 repeats) and print ms/call.
  --help
      Print this message.
"""

private func die(_ message: String, code: Int32) -> Never {
    FileHandle.standardError.write((message + "\n").data(using: .utf8)!)
    exit(code)
}

private func readData(_ path: String) throws -> Data {
    try Data(contentsOf: URL(fileURLWithPath: path))
}

// MARK: - validate

private func cmdValidate(_ args: [String]) -> Int32 {
    var scenePath: String?
    var placementsPath: String?
    var pretty = false
    var i = 0
    while i < args.count {
        switch args[i] {
        case "--placements":
            i += 1
            guard i < args.count else { die("--placements requires a path", code: 2) }
            placementsPath = args[i]
        case "--pretty":
            pretty = true
        default:
            guard scenePath == nil else { die("unexpected argument: \(args[i])", code: 2) }
            scenePath = args[i]
        }
        i += 1
    }
    guard let scenePath else { die("validate requires a scene path", code: 2) }

    let scene: Scene
    var placements: [Placement]?
    do {
        scene = try sceneFromJSON(readData(scenePath))
        if let placementsPath {
            placements = try placementsFromJSON(readData(placementsPath))
        }
    } catch {
        die("error reading input: \(error)", code: 2)
    }

    let result = validate(scene, placements: placements)
    guard let data = try? resultToJSON(result, pretty: pretty),
          let text = String(data: data, encoding: .utf8)
    else { die("error encoding result", code: 2) }
    print(text)
    return result.valid ? 0 : 1
}

// MARK: - example

/// Same 7 objects/container as the Python prototype's
/// `tests/fixtures.py::valid_packed_scene` (a carry-on, everything valid).
private func exampleScene() -> Scene {
    let container = Container(id: "carry_on", dimensions: Vec3(0.56, 0.23, 0.36), position: Vec3(0, 0.115, 0))
    let laptopConstraints = Constraints(fragile: true, cannotSupportWeight: true, orientationLock: "flat_only")
    let bottleConstraints = Constraints(keepUpright: true)
    let objects = [
        SceneObject(id: "laptop", dimensions: Vec3(0.30, 0.02, 0.21), position: Vec3(-0.125, 0.01, -0.07),
                    massKg: 1.3, constraints: laptopConstraints),
        SceneObject(id: "headphones_case", dimensions: Vec3(0.18, 0.07, 0.18), position: Vec3(0.14, 0.035, -0.07),
                    massKg: 0.3),
        SceneObject(id: "charger", dimensions: Vec3(0.09, 0.03, 0.06), position: Vec3(0.10, 0.085, -0.10),
                    massKg: 0.1),
        SceneObject(id: "toiletry_bottle", dimensions: Vec3(0.06, 0.15, 0.06), position: Vec3(0.18, 0.145, -0.02),
                    massKg: 0.2, constraints: bottleConstraints),
        SceneObject(id: "shoe", dimensions: Vec3(0.29, 0.11, 0.12), position: Vec3(-0.13, 0.055, 0.10),
                    massKg: 0.3),
        SceneObject(id: "toiletry_bag", dimensions: Vec3(0.20, 0.08, 0.135), position: Vec3(0.125, 0.04, 0.1075),
                    massKg: 0.5),
        SceneObject(id: "camera", dimensions: Vec3(0.13, 0.09, 0.10), position: Vec3(0.125, 0.125, 0.1075),
                    massKg: 0.4),
    ]
    return Scene(container: container, objects: objects)
}

private func cmdExample() -> Int32 {
    guard let data = try? sceneToJSON(exampleScene(), pretty: true),
          let text = String(data: data, encoding: .utf8)
    else { die("error encoding example scene", code: 2) }
    print(text)
    return 0
}

// MARK: - scan-to-object

private func cmdScanToObject(_ args: [String]) -> Int32 {
    var itemPath: String?
    var position = Vec3.zero
    var i = 0
    while i < args.count {
        switch args[i] {
        case "--position":
            guard i + 3 < args.count,
                  let x = Double(args[i + 1]), let y = Double(args[i + 2]), let z = Double(args[i + 3])
            else { die("--position requires 3 numbers: x y z", code: 2) }
            position = Vec3(x, y, z)
            i += 3
        default:
            guard itemPath == nil else { die("unexpected argument: \(args[i])", code: 2) }
            itemPath = args[i]
        }
        i += 1
    }
    guard let itemPath else { die("scan-to-object requires an item path", code: 2) }

    let item: ScannedItem
    do {
        item = try JSONDecoder().decode(ScannedItem.self, from: readData(itemPath))
    } catch {
        die("error reading input: \(error)", code: 2)
    }

    let object = sceneObject(from: item, position: position)
    let encoder = JSONEncoder()
    encoder.outputFormatting = [.sortedKeys]
    guard let data = try? encoder.encode(object), let text = String(data: data, encoding: .utf8)
    else { die("error encoding result", code: 2) }
    print(text)
    return 0
}

// MARK: - bench

/// Port of the Python prototype's `tests/benchmark_validator.py::build_scene`:
/// an n-object grid of `box`-sided cubes on the floor of a square suitcase
/// sized to `cell` spacing (cell > box: sparse/no AABB overlap; cell == box:
/// touching/flush; cell < box: dense/overlapping).
private func buildBenchScene(n: Int, cell: Double, box: Double = 0.20) -> Scene {
    let cols = Int(ceil(sqrt(Double(n))))
    let size = Double(cols) * cell + 0.5
    let container = Container(id: "suitcase", dimensions: Vec3(size, 1.0, size))
    let floorY = -0.5 + box / 2.0
    var objects: [SceneObject] = []
    for i in 0..<n {
        let row = i / cols
        let col = i % cols
        let x = -size / 2 + cell / 2 + Double(col) * cell
        let z = -size / 2 + cell / 2 + Double(row) * cell
        objects.append(SceneObject(id: String(format: "obj%02d", i),
                                    dimensions: Vec3(box, box, box),
                                    position: Vec3(x, floorY, z)))
    }
    return Scene(container: container, objects: objects)
}

private func padRight(_ s: String, _ width: Int) -> String {
    s.count >= width ? s : s + String(repeating: " ", count: width - s.count)
}

private func padLeft(_ s: String, _ width: Int) -> String {
    s.count >= width ? s : String(repeating: " ", count: width - s.count) + s
}

private func cmdBench(_ args: [String]) -> Int32 {
    var objectsOverride: Int?
    var repeatOverride: Int?
    var i = 0
    while i < args.count {
        switch args[i] {
        case "--objects":
            i += 1
            guard i < args.count, let n = Int(args[i]), n >= 1 else { die("--objects requires a positive integer", code: 2) }
            objectsOverride = n
        case "--repeat":
            i += 1
            guard i < args.count, let r = Int(args[i]), r >= 1 else { die("--repeat requires a positive integer", code: 2) }
            repeatOverride = r
        default:
            die("unexpected argument: \(args[i])", code: 2)
        }
        i += 1
    }

    #if DEBUG
    print("warning: debug build -- timings are not representative of release performance")
    #endif

    let ns = objectsOverride.map { [$0] } ?? [5, 10, 20, 40]
    let reps = max(1, repeatOverride ?? 30)
    let families: [(name: String, cell: Double)] = [
        ("sparse (no AABB overlap)", 0.30),
        ("touching (flush, SAT separates)", 0.20),
        ("dense (overlapping)", 0.19),
    ]

    print(padRight("scene", 34) + " " + padLeft("n", 3) + " " + padLeft("ms/call", 8) + "  collisions")
    for family in families {
        for n in ns {
            let scene = buildBenchScene(n: n, cell: family.cell)
            _ = validateLayout(scene)  // warm
            let start = Date()
            var result = validateLayout(scene)
            for _ in 1..<reps { result = validateLayout(scene) }
            let msPerCall = Date().timeIntervalSince(start) * 1000.0 / Double(reps)
            let collisions = result.violations.filter { $0["type"]?.stringValue == "OBJECT_COLLISION" }.count
            let msText = String(format: "%.2f", msPerCall)
            print(padRight(family.name, 34) + " " + padLeft(String(n), 3) + " " + padLeft(msText, 8)
                + "  \(collisions)")
        }
    }
    return 0
}

// MARK: - entry point

let arguments = Array(CommandLine.arguments.dropFirst())

guard let command = arguments.first else {
    print(usage)
    exit(2)
}

switch command {
case "--help", "-h", "help":
    print(usage)
    exit(0)
case "validate":
    exit(cmdValidate(Array(arguments.dropFirst())))
case "example":
    exit(cmdExample())
case "scan-to-object":
    exit(cmdScanToObject(Array(arguments.dropFirst())))
case "bench":
    exit(cmdBench(Array(arguments.dropFirst())))
default:
    print(usage)
    exit(2)
}
