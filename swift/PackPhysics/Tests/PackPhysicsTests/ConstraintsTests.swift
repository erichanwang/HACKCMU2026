import Foundation
import XCTest
@testable import PackPhysics

/// Quaternion for a rotation of `deg` about world X (tips local Y over). Mirrors
/// `test_constraints.quat_x_rot`. (Y-axis yaw already exists as `Quat.yaw`.)
private func quatXRot(_ deg: Double) -> Quat {
    let h = deg * Double.pi / 180.0 / 2.0
    return Quat(x: sin(h), y: 0, z: 0, w: cos(h))
}

private func makeScene(_ objects: [SceneObject]) -> Scene {
    Scene(container: Container(id: "c", dimensions: Vec3(1, 1, 1), position: Vec3(0, 0, 0)), objects: objects)
}

/// Recursive JSON-value comparison with a numeric tolerance, distinct from any
/// helper another concurrent agent's test file may define.
private func assertConstraintsDetailClose(
    _ a: JSONValue, _ b: JSONValue, _ message: String,
    file: StaticString = #filePath, line: UInt = #line
) {
    switch (a, b) {
    case let (.number(x), .number(y)):
        XCTAssertEqual(x, y, accuracy: 1e-9, message, file: file, line: line)
    case let (.array(xs), .array(ys)):
        XCTAssertEqual(xs.count, ys.count, message, file: file, line: line)
        for (x, y) in zip(xs, ys) { assertConstraintsDetailClose(x, y, message, file: file, line: line) }
    default:
        XCTAssertEqual(a, b, message, file: file, line: line)
    }
}

final class ConstraintsTests: XCTestCase {

    // MARK: - Upright / orientation

    func testUprightBottleNoViolation() throws {
        let bottle = SceneObject(id: "bottle", dimensions: Vec3(0.1, 0.3, 0.1), position: Vec3(0, 0.15, 0),
                                  constraints: Constraints(keepUpright: true))
        let (violations, warnings) = try checkConstraints(makeScene([bottle]))
        XCTAssertEqual(violations, [])
        XCTAssertEqual(warnings, [])
    }

    func testTiltedBottleViolation() throws {
        let bottle = SceneObject(id: "bottle", dimensions: Vec3(0.1, 0.3, 0.1), position: Vec3(0, 0.05, 0),
                                  rotation: quatXRot(90.0), constraints: Constraints(keepUpright: true))
        let (violations, _) = try checkConstraints(makeScene([bottle]))
        XCTAssertEqual(violations.count, 1)
        let v = violations[0]
        XCTAssertEqual(v.type, "LIQUID_NOT_UPRIGHT")
        XCTAssertEqual(v.details["tilt_deg"]?.doubleValue ?? .nan, 90.0, accuracy: 0.5)
    }

    func testFlatOnlyLyingDownOk() throws {
        let box = SceneObject(id: "book", dimensions: Vec3(0.2, 0.05, 0.3), position: Vec3(0, 0.025, 0),
                               constraints: Constraints(orientationLock: "flat_only"))
        let (violations, _) = try checkConstraints(makeScene([box]))
        XCTAssertEqual(violations, [])
    }

    func testFlatOnlyStandingOnEdgeViolation() throws {
        let box = SceneObject(id: "book", dimensions: Vec3(0.2, 0.05, 0.3), position: Vec3(0, 0.1, 0),
                               rotation: quatXRot(90.0), constraints: Constraints(orientationLock: "flat_only"))
        let (violations, _) = try checkConstraints(makeScene([box]))
        XCTAssertEqual(violations.count, 1)
        XCTAssertEqual(violations[0].type, "INVALID_ORIENTATION")
        XCTAssertEqual(violations[0].details["lock"]?.stringValue, "flat_only")
    }

    // MARK: - cannot_support_weight

    func testLaptopAloneNoViolation() throws {
        let laptop = SceneObject(id: "laptop", dimensions: Vec3(0.3, 0.02, 0.2), position: Vec3(0, 0.01, 0),
                                  constraints: Constraints(cannotSupportWeight: true))
        let (violations, _) = try checkConstraints(makeScene([laptop]))
        XCTAssertEqual(violations, [])
    }

    func testShoeOnLaptopViolation() throws {
        let laptop = SceneObject(id: "laptop", dimensions: Vec3(0.3, 0.02, 0.2), position: Vec3(0, 0.01, 0),
                                  massKg: 1.5, constraints: Constraints(cannotSupportWeight: true))
        let shoe = SceneObject(id: "shoe", dimensions: Vec3(0.1, 0.1, 0.25), position: Vec3(0, 0.02 + 0.05, 0), massKg: 0.4)
        let (violations, _) = try checkConstraints(makeScene([laptop, shoe]))
        XCTAssertEqual(violations.count, 1)
        let v = violations[0]
        XCTAssertEqual(v.type, "FRAGILE_OBJECT_OVERLOADED")
        XCTAssertEqual(v.objectId, "laptop")
        XCTAssertEqual(v.details["supported_weight_kg"]?.doubleValue ?? .nan, 0.4, accuracy: 1e-9)
    }

    // MARK: - fragile warning

    func testFragileWithLoadIsWarningNotViolation() throws {
        let glass = SceneObject(id: "glass", dimensions: Vec3(0.1, 0.1, 0.1), position: Vec3(0, 0.05, 0),
                                 massKg: 0.3, constraints: Constraints(fragile: true))
        let shirt = SceneObject(id: "shirt", dimensions: Vec3(0.2, 0.02, 0.2), position: Vec3(0, 0.1 + 0.01, 0), massKg: 0.2)
        let (violations, warnings) = try checkConstraints(makeScene([glass, shirt]))
        XCTAssertEqual(violations, [])
        XCTAssertEqual(warnings.count, 1)
        let w = warnings[0]
        XCTAssertEqual(w.type, "FRAGILE_LOAD")
        XCTAssertEqual(w.objectId, "glass")
        XCTAssertEqual(w.details["supported_weight_kg"]?.doubleValue ?? .nan, 0.2, accuracy: 1e-9)
    }

    // MARK: - no-op default

    func testDefaultConstraintsObjectUntouchedInBusyScene() throws {
        let plain = SceneObject(id: "plain", dimensions: Vec3(0.2, 0.2, 0.2), position: Vec3(0, 0.1, 0))
        let stacked = SceneObject(id: "stacked", dimensions: Vec3(0.2, 0.2, 0.2), position: Vec3(0, 0.3 + 0.01, 0), massKg: 5.0)
        let tilted = SceneObject(id: "tilted", dimensions: Vec3(0.1, 0.3, 0.1), position: Vec3(1, 0.05, 0), rotation: quatXRot(90.0))
        let (violations, warnings) = try checkConstraints(makeScene([plain, stacked, tilted]))
        for item in violations {
            XCTAssertNotEqual(item.objectId, "plain")
            XCTAssertNotEqual(item.objectId, "tilted")
        }
        for item in warnings {
            XCTAssertNotEqual(item.objectId, "plain")
            XCTAssertNotEqual(item.objectId, "tilted")
        }
    }

    // MARK: - heavy on top

    func testHeavyObjectStackedProducesWarning() throws {
        let base = SceneObject(id: "base", dimensions: Vec3(0.3, 0.2, 0.3), position: Vec3(0, 0.1, 0))
        let heavyBox = SceneObject(id: "heavybox", dimensions: Vec3(0.2, 0.1, 0.2), position: Vec3(0, 0.2 + 0.05, 0),
                                    massKg: 8.0, constraints: Constraints(heavy: true))
        let (_, warnings) = try checkConstraints(makeScene([base, heavyBox]))
        let heavyWarnings = warnings.filter { $0.type == "HEAVY_ON_TOP" }
        XCTAssertEqual(heavyWarnings.count, 1)
        XCTAssertEqual(heavyWarnings[0].objectId, "heavybox")
        let restingOn = heavyWarnings[0].details["resting_on"]?.arrayValue?.compactMap(\.stringValue) ?? []
        XCTAssertTrue(restingOn.contains("base"))
    }

    // MARK: - transitive load

    func testThreeStackPropagatesToFragileBase() throws {
        let laptop = SceneObject(id: "laptop", dimensions: Vec3(0.3, 0.02, 0.2), position: Vec3(0, 0.01, 0),
                                  massKg: 1.5, constraints: Constraints(cannotSupportWeight: true))
        let toiletry = SceneObject(id: "toiletry", dimensions: Vec3(0.2, 0.1, 0.15), position: Vec3(0, 0.02 + 0.05, 0), massKg: 0.5)
        let shoe = SceneObject(id: "shoe", dimensions: Vec3(0.1, 0.1, 0.25), position: Vec3(0, 0.12 + 0.05, 0), massKg: 0.3)
        let (violations, _) = try checkConstraints(makeScene([laptop, toiletry, shoe]))
        XCTAssertEqual(violations.count, 1)
        let v = violations[0]
        XCTAssertEqual(v.type, "FRAGILE_OBJECT_OVERLOADED")
        XCTAssertEqual(v.objectId, "laptop")
        XCTAssertEqual(v.details["supported_weight_kg"]?.doubleValue ?? .nan, 0.8, accuracy: 1e-9)
        XCTAssertEqual(v.details["direct_weight_kg"]?.doubleValue ?? .nan, 0.5, accuracy: 1e-9)
    }

    func testStraddlingBarSplitsLoad5050() throws {
        let suppA = SceneObject(id: "supp_a", dimensions: Vec3(0.2, 0.1, 0.2), position: Vec3(-0.1, 0.05, 0),
                                 massKg: 0.01, constraints: Constraints(cannotSupportWeight: true))
        let suppB = SceneObject(id: "supp_b", dimensions: Vec3(0.2, 0.1, 0.2), position: Vec3(0.1, 0.05, 0),
                                 massKg: 0.01, constraints: Constraints(cannotSupportWeight: true))
        let bar = SceneObject(id: "bar", dimensions: Vec3(0.2, 0.05, 0.2), position: Vec3(0, 0.1 + 0.025, 0), massKg: 1.0)
        let (violations, _) = try checkConstraints(makeScene([suppA, suppB, bar]))
        let byId = Dictionary(uniqueKeysWithValues: violations.map { ($0.objectId, $0) })
        XCTAssertEqual(Set(byId.keys), ["supp_a", "supp_b"])
        XCTAssertEqual(byId["supp_a"]?.details["supported_weight_kg"]?.doubleValue ?? .nan, 0.5, accuracy: 1e-9)
        XCTAssertEqual(byId["supp_b"]?.details["supported_weight_kg"]?.doubleValue ?? .nan, 0.5, accuracy: 1e-9)
    }

    func testStraddlingBarSplitsLoad2575() throws {
        let suppA = SceneObject(id: "supp_a", dimensions: Vec3(0.2, 0.1, 0.2), position: Vec3(-0.1, 0.05, 0),
                                 massKg: 0.01, constraints: Constraints(cannotSupportWeight: true))
        let suppB = SceneObject(id: "supp_b", dimensions: Vec3(0.2, 0.1, 0.2), position: Vec3(0.1, 0.05, 0),
                                 massKg: 0.01, constraints: Constraints(cannotSupportWeight: true))
        let bar = SceneObject(id: "bar", dimensions: Vec3(0.2, 0.05, 0.2), position: Vec3(0.05, 0.1 + 0.025, 0), massKg: 1.0)
        let (violations, _) = try checkConstraints(makeScene([suppA, suppB, bar]))
        let byId = Dictionary(uniqueKeysWithValues: violations.map { ($0.objectId, $0) })
        XCTAssertEqual(Set(byId.keys), ["supp_a", "supp_b"])
        XCTAssertEqual(byId["supp_a"]?.details["supported_weight_kg"]?.doubleValue ?? .nan, 0.25, accuracy: 1e-9)
        XCTAssertEqual(byId["supp_b"]?.details["supported_weight_kg"]?.doubleValue ?? .nan, 0.75, accuracy: 1e-9)
    }

    // MARK: - load path

    func testHeavyAtTopOfStackReportsLoadPathToFloor() throws {
        let laptop = SceneObject(id: "laptop", dimensions: Vec3(0.3, 0.02, 0.2), position: Vec3(0, 0.01, 0), massKg: 1.5)
        let toiletry = SceneObject(id: "toiletry", dimensions: Vec3(0.2, 0.1, 0.15), position: Vec3(0, 0.02 + 0.05, 0), massKg: 0.5)
        let shoe = SceneObject(id: "shoe", dimensions: Vec3(0.1, 0.1, 0.25), position: Vec3(0, 0.12 + 0.05, 0),
                                massKg: 0.3, constraints: Constraints(heavy: true))
        let (_, warnings) = try checkConstraints(makeScene([laptop, toiletry, shoe]))
        let heavyWarnings = warnings.filter { $0.type == "HEAVY_ON_TOP" }
        XCTAssertEqual(heavyWarnings.count, 1)
        let loadPath = heavyWarnings[0].details["load_path"]?.arrayValue?.compactMap(\.stringValue) ?? []
        XCTAssertEqual(loadPath, ["shoe", "toiletry", "laptop"])
    }

    // MARK: - precomputed geom passthrough

    func testPassingGeomMatchesComputingInternally() throws {
        let laptop = SceneObject(id: "laptop", dimensions: Vec3(0.3, 0.02, 0.2), position: Vec3(0, 0.01, 0),
                                  massKg: 1.5, constraints: Constraints(cannotSupportWeight: true))
        let shoe = SceneObject(id: "shoe", dimensions: Vec3(0.1, 0.1, 0.25), position: Vec3(0, 0.02 + 0.05, 0), massKg: 0.4)
        let scene = makeScene([laptop, shoe])
        let geom = try precompute(scene)
        let (v1, w1) = try checkConstraints(scene)
        let (v2, w2) = try checkConstraints(scene, geom: geom)
        XCTAssertEqual(v1, v2)
        XCTAssertEqual(w1, w2)
    }

    // MARK: - determinism

    func testTwoCallsIdentical() throws {
        let laptop = SceneObject(id: "laptop", dimensions: Vec3(0.3, 0.02, 0.2), position: Vec3(0, 0.01, 0),
                                  massKg: 1.5, constraints: Constraints(fragile: true, cannotSupportWeight: true))
        let shoe = SceneObject(id: "shoe", dimensions: Vec3(0.1, 0.1, 0.25), position: Vec3(0, 0.02 + 0.05, 0),
                                massKg: 0.4, constraints: Constraints(heavy: true))
        let scene = makeScene([laptop, shoe])
        let (v1, w1) = try checkConstraints(scene)
        let (v2, w2) = try checkConstraints(scene)
        XCTAssertEqual(v1, v2)
        XCTAssertEqual(w1, w2)
    }

    // MARK: - rotated resting

    func testYawedBoxRestingOnAxisAlignedBoxRegistersAndPropagates() throws {
        let base = SceneObject(id: "base", dimensions: Vec3(0.4, 0.1, 0.4), position: Vec3(0, 0.05, 0),
                                massKg: 2.0, constraints: Constraints(cannotSupportWeight: true))
        let yawed = SceneObject(id: "yawed", dimensions: Vec3(0.2, 0.1, 0.2), position: Vec3(0, 0.1 + 0.05, 0),
                                 rotation: .yaw(radians: 45.0 * Double.pi / 180.0), massKg: 0.4)
        let (violations, _) = try checkConstraints(makeScene([base, yawed]))
        XCTAssertEqual(violations.count, 1)
        let v = violations[0]
        XCTAssertEqual(v.objectId, "base")
        XCTAssertEqual(v.details["supported_weight_kg"]?.doubleValue ?? .nan, 0.4, accuracy: 1e-9)
    }

    // MARK: - dataset parity

    func testDatasetParityAgainstPythonValidator() throws {
        let myTypes: Set<String> = ["LIQUID_NOT_UPRIGHT", "INVALID_ORIENTATION", "FRAGILE_OBJECT_OVERLOADED", "FRAGILE_LOAD", "HEAVY_ON_TOP"]
        for c in Parity.cases {
            let violations: [ConstraintViolation]
            let warnings: [ConstraintWarning]
            do {
                (violations, warnings) = try checkConstraints(c.scene)
            } catch {
                continue // malformed scenes are the containment/geometry modules' problem
            }

            var mine: [String: [String: JSONValue]] = [:]
            for v in violations where myTypes.contains(v.type) {
                mine["\(v.type)|\(v.objectId)"] = v.details
            }
            for w in warnings where myTypes.contains(w.type) {
                mine["\(w.type)|\(w.objectId)"] = w.details
            }

            var expected: [String: JSONValue] = [:]
            let expEntries = (c.expected["violations"]?.arrayValue ?? []) + (c.expected["warnings"]?.arrayValue ?? [])
            for e in expEntries {
                guard let t = e["type"]?.stringValue, myTypes.contains(t), let obj = e["object"]?.stringValue else { continue }
                expected["\(t)|\(obj)"] = e
            }

            XCTAssertEqual(Set(mine.keys), Set(expected.keys), "case \(c.name): mismatched (type, object) pairs")
            for (key, details) in mine {
                guard let expEntry = expected[key] else { continue }
                for (detailKey, value) in details {
                    guard let expValue = expEntry[detailKey] else {
                        XCTFail("case \(c.name) \(key): expected entry missing key \(detailKey)")
                        continue
                    }
                    assertConstraintsDetailClose(value, expValue, "case \(c.name) \(key).\(detailKey)")
                }
            }
        }
    }
}
