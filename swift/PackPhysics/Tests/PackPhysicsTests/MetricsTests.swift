import Foundation
import XCTest
@testable import PackPhysics

private func quat(axis: Vec3, degrees: Double) -> Quat {
    let a = axis / length(axis)
    let half = degrees * .pi / 180.0 / 2.0
    let s = sin(half)
    return Quat(x: a.x * s, y: a.y * s, z: a.z * s, w: cos(half))
}

private func makeContainer(dims: Vec3 = Vec3(2, 2, 2), position: Vec3 = Vec3(0, 0, 0), rotation: Quat = .identity) -> Container {
    Container(id: "suitcase", dimensions: dims, position: position, rotation: rotation)
}

private func makeObject(_ id: String, _ dims: Vec3, _ position: Vec3, massKg: Double = 1.0, rotation: Quat = .identity) -> SceneObject {
    SceneObject(id: id, dimensions: dims, position: position, rotation: rotation, massKg: massKg)
}

/// Structural JSON equality with a numeric tolerance -- other suites may
/// reuse this to compare `sceneMetrics`/validator output against the Python
/// parity dataset.
func assertJSONClose(_ a: JSONValue, _ b: JSONValue, tol: Double = 1e-9, path: String = "$", file: StaticString = #filePath, line: UInt = #line) {
    switch (a, b) {
    case (.null, .null):
        break
    case let (.bool(x), .bool(y)):
        XCTAssertEqual(x, y, "\(path): \(x) != \(y)", file: file, line: line)
    case let (.number(x), .number(y)):
        XCTAssertEqual(x, y, accuracy: tol, "\(path): \(x) != \(y) (tol \(tol))", file: file, line: line)
    case let (.string(x), .string(y)):
        XCTAssertEqual(x, y, "\(path)", file: file, line: line)
    case let (.array(x), .array(y)):
        XCTAssertEqual(x.count, y.count, "\(path): array length \(x.count) != \(y.count)", file: file, line: line)
        for (i, (xi, yi)) in zip(x, y).enumerated() {
            assertJSONClose(xi, yi, tol: tol, path: "\(path)[\(i)]", file: file, line: line)
        }
    case let (.object(x), .object(y)):
        XCTAssertEqual(Set(x.keys), Set(y.keys), "\(path): key sets differ", file: file, line: line)
        for key in Set(x.keys).intersection(y.keys) {
            assertJSONClose(x[key]!, y[key]!, tol: tol, path: "\(path).\(key)", file: file, line: line)
        }
    default:
        XCTFail("\(path): mismatched JSON shape (\(a) vs \(b))", file: file, line: line)
    }
}

private func hasMalformedGeometry(_ expected: JSONValue) -> Bool {
    guard let violations = expected["violations"]?.arrayValue else { return false }
    return violations.contains { $0["type"]?.stringValue == "MALFORMED_GEOMETRY" }
}

final class MetricsTests: XCTestCase {
    func testEqualMassSymmetricBoxesComAtContainerCenter() throws {
        let scene = Scene(container: makeContainer(), objects: [
            makeObject("a", Vec3(0.2, 0.2, 0.2), Vec3(-0.5, 0, 0), massKg: 2.0),
            makeObject("b", Vec3(0.2, 0.2, 0.2), Vec3(0.5, 0, 0), massKg: 2.0),
        ])
        let m = sceneMetrics(try precompute(scene))
        for v in m["center_of_mass"]!.arrayValue! { XCTAssertEqual(v.doubleValue!, 0.0, accuracy: 1e-9) }
        for v in m["com_offset_m"]!.arrayValue! { XCTAssertEqual(v.doubleValue!, 0.0, accuracy: 1e-9) }
    }

    func testUnequalMassShiftsComTowardHeavierObject() throws {
        // COM_x = (1kg*(-1) + 3kg*(1)) / 4kg = 0.5
        let scene = Scene(container: makeContainer(dims: Vec3(4, 4, 4)), objects: [
            makeObject("light", Vec3(0.5, 0.5, 0.5), Vec3(-1.0, 0, 0), massKg: 1.0),
            makeObject("heavy", Vec3(0.5, 0.5, 0.5), Vec3(1.0, 0, 0), massKg: 3.0),
        ])
        let m = sceneMetrics(try precompute(scene))
        let com = m["center_of_mass"]!.arrayValue!
        XCTAssertEqual(com[0].doubleValue!, 0.5, accuracy: 1e-9)
        XCTAssertEqual(com[1].doubleValue!, 0.0, accuracy: 1e-9)
        XCTAssertEqual(com[2].doubleValue!, 0.0, accuracy: 1e-9)
        XCTAssertEqual(m["com_offset_m"]!.arrayValue![0].doubleValue!, 0.5, accuracy: 1e-9)
    }

    func testRotatedContainerObjectAtCenterGivesZeroOffset() throws {
        // A rotated/offset container with a single object placed exactly at
        // the container's own position: world offset is 0 regardless of
        // rotation, so this proves com_offset_m's container-local projection
        // doesn't itself introduce a spurious offset.
        let rot = quat(axis: Vec3(0, 1, 0), degrees: 37.0)
        let container = makeContainer(dims: Vec3(2, 2, 2), position: Vec3(5, 1, -3), rotation: rot)
        let scene = Scene(container: container, objects: [makeObject("a", Vec3(0.3, 0.3, 0.3), Vec3(5, 1, -3), rotation: rot)])
        let m = sceneMetrics(try precompute(scene))
        for v in m["com_offset_m"]!.arrayValue! { XCTAssertEqual(v.doubleValue!, 0.0, accuracy: 1e-9) }
    }

    func testFlushObjectHasNearZeroClearance() throws {
        let scene = Parity.scene(named: "fixture:scene_object_touching_wall")
        let m = sceneMetrics(try precompute(scene))
        // laptop is flush against the -x wall (and also rests on the floor).
        XCTAssertEqual(m["per_object"]!["laptop"]!["wall_clearance_m"]!.doubleValue!, 0.0, accuracy: 1e-6)
    }

    func testInteriorObjectHasPositiveClearance() throws {
        // charger (from valid_packed_scene, stacked on headphones_case, away
        // from every wall): half-extents=(0.045,0.015,0.03),
        // center=(0.10,0.085,-0.10) in a container with half-extents
        // (0.28,0.115,0.18). Tightest axis is z: |center_z| + half_z =
        // 0.10+0.03=0.13, clearance = 0.18-0.13 = 0.05 (x gives 0.135, y
        // gives 0.07, both larger).
        let scene = Parity.scene(named: "fixture:valid_packed_scene")
        let m = sceneMetrics(try precompute(scene))
        XCTAssertEqual(m["per_object"]!["charger"]!["wall_clearance_m"]!.doubleValue!, 0.05, accuracy: 1e-6)
    }

    func testPenetratingObjectHasNegativeClearance() throws {
        let scene = Scene(container: makeContainer(), objects: [  // half-extents (1,1,1)
            makeObject("a", Vec3(1.0, 1.0, 1.0), Vec3(0.51, 0, 0)),  // pokes 0.01 past +x
        ])
        let m = sceneMetrics(try precompute(scene))
        let clearance = m["per_object"]!["a"]!["wall_clearance_m"]!.doubleValue!
        XCTAssertLessThan(clearance, 0.0)
        XCTAssertEqual(clearance, -0.01, accuracy: 1e-6)
    }

    func testKnownGapAlongX() throws {
        // box A: x in [-0.5, 0.5]. box B center_x=1.02, half=0.5 -> x in
        // [0.52, 1.52]. gap = 0.52 - 0.5 = 0.02. y/z fully overlap (both
        // centered at 0 with the same half-extents), so those axes are
        // negative (overlap) and don't win the max.
        let scene = Scene(container: makeContainer(dims: Vec3(10, 10, 10)), objects: [
            makeObject("a", Vec3(1, 1, 1), Vec3(0, 0, 0)),
            makeObject("b", Vec3(1, 1, 1), Vec3(1.02, 0, 0)),
        ])
        let m = sceneMetrics(try precompute(scene))
        XCTAssertEqual(m["per_object"]!["a"]!["nearest_neighbor_gap_m"]!.doubleValue!, 0.02, accuracy: 1e-6)
        XCTAssertEqual(m["per_object"]!["b"]!["nearest_neighbor_gap_m"]!.doubleValue!, 0.02, accuracy: 1e-6)
        XCTAssertEqual(m["per_object"]!["a"]!["nearest_neighbor_id"]!.stringValue!, "b")
        XCTAssertEqual(m["per_object"]!["b"]!["nearest_neighbor_id"]!.stringValue!, "a")
    }

    func testOverlappingBoxesGapIsZero() throws {
        let scene = Scene(container: makeContainer(dims: Vec3(10, 10, 10)), objects: [
            makeObject("a", Vec3(1, 1, 1), Vec3(0, 0, 0)),
            makeObject("b", Vec3(1, 1, 1), Vec3(0.3, 0, 0)),
        ])
        let m = sceneMetrics(try precompute(scene))
        XCTAssertEqual(m["per_object"]!["a"]!["nearest_neighbor_gap_m"]!.doubleValue!, 0.0)
        XCTAssertEqual(m["per_object"]!["b"]!["nearest_neighbor_gap_m"]!.doubleValue!, 0.0)
    }

    func testSingleObjectHasNoNeighbor() throws {
        let scene = Scene(container: makeContainer(), objects: [makeObject("a", Vec3(0.2, 0.2, 0.2), Vec3(0, 0, 0))])
        let m = sceneMetrics(try precompute(scene))
        XCTAssertEqual(m["per_object"]!["a"]!["nearest_neighbor_gap_m"]!, .null)
        XCTAssertEqual(m["per_object"]!["a"]!["nearest_neighbor_id"]!, .null)
    }

    func testSmallBoxInUnitContainerFillRatio() throws {
        let scene = Scene(container: makeContainer(dims: Vec3(1, 1, 1)), objects: [
            makeObject("a", Vec3(0.1, 0.1, 0.1), Vec3(0, 0, 0)),
        ])
        let m = sceneMetrics(try precompute(scene))
        XCTAssertEqual(m["fill_ratio"]!.doubleValue!, 0.001, accuracy: 1e-9)
    }

    func testZeroObjectScene() throws {
        let scene = Scene(container: makeContainer(), objects: [])
        let m = sceneMetrics(try precompute(scene))
        XCTAssertEqual(m["total_mass_kg"]!.doubleValue!, 0.0)
        XCTAssertEqual(m["fill_ratio"]!.doubleValue!, 0.0)
        XCTAssertEqual(m["per_object"]!.objectValue!.count, 0)
    }

    // MARK: - Dataset parity: sceneMetrics matches the Python validator's
    // metrics dict, structurally, to 1e-9, for every well-formed case.

    func testDatasetParityMetrics() throws {
        var checked = 0
        for c in Parity.cases {
            if hasMalformedGeometry(c.expected) { continue }
            let g = try precompute(c.scene)
            assertJSONClose(sceneMetrics(g), c.expected["metrics"]!, tol: 1e-9, path: "$.\(c.name).metrics")
            checked += 1
        }
        XCTAssertEqual(checked, Parity.cases.count - 1)  // only malformed:dup_ids excluded
    }
}
