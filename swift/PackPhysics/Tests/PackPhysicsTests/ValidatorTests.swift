// Port of tests/test_validator.py (same scenes, same numbers), plus a JSON
// round-trip of a result through resultToJSON/resultFromJSON.

import Foundation
import XCTest
@testable import PackPhysics

/// 1 m cube at the origin: floor world-y = -0.5, ceiling +0.5, walls at x/z = ±0.5.
private let cube = Container(id: "suitcase", dimensions: Vec3(1, 1, 1), position: Vec3(0, 0, 0))

private func obj(_ id: String, _ dims: Vec3, _ position: Vec3,
                 rotation: Quat = .identity, massKg: Double = 1.0,
                 constraints: Constraints = Constraints(),
                 rigidity: Rigidity = .rigid, compressibilityK: Double = 1.0) -> SceneObject {
    SceneObject(id: id, dimensions: dims, position: position, rotation: rotation,
                massKg: massKg, constraints: constraints, rigidity: rigidity,
                compressibilityK: compressibilityK)
}

private func sceneOf(_ objects: SceneObject...) -> Scene {
    Scene(container: cube, objects: objects)
}

private func entry(_ entries: [JSONValue], type: String) -> JSONValue? {
    entries.first { $0["type"]?.stringValue == type }
}

private func types(_ entries: [JSONValue]) -> [String] {
    entries.compactMap { $0["type"]?.stringValue }
}

final class ValidatorTests: XCTestCase {
    func testCleanScene() {
        let a = obj("a", Vec3(0.2, 0.2, 0.2), Vec3(0, -0.4, 0))     // flush on floor
        let b = obj("b", Vec3(0.2, 0.2, 0.2), Vec3(0.3, -0.4, 0))   // side by side
        let result = validateLayout(sceneOf(a, b))
        XCTAssertTrue(result.valid)
        XCTAssertEqual(result.score, 1.0, accuracy: 1e-6)
        XCTAssertEqual(result.violations, [])
    }

    func testObjectCollision() {
        let a = obj("a", Vec3(0.4, 0.2, 0.4), Vec3(0, -0.4, 0))
        let b = obj("b", Vec3(0.4, 0.2, 0.4), Vec3(0.1, -0.4, 0))   // heavy x overlap
        let result = validateLayout(sceneOf(a, b))
        XCTAssertFalse(result.valid)
        XCTAssertTrue(types(result.violations).contains("OBJECT_COLLISION"))
        let coll = entry(result.violations, type: "OBJECT_COLLISION")!
        XCTAssertEqual(coll["objects"], .strings(["a", "b"]))
        XCTAssertGreaterThan(coll["penetration_depth_m"]!.doubleValue!, 0)
        XCTAssertGreaterThanOrEqual(coll["severity"]!.doubleValue!, 0)
    }

    func testContainerPenetration() {
        // half-height 0.4 -> spans y in [-0.6, 0.2], well past the floor at -0.5
        let result = validateLayout(sceneOf(obj("a", Vec3(0.2, 0.8, 0.2), Vec3(0, -0.2, 0))))
        XCTAssertFalse(result.valid)
        XCTAssertTrue(types(result.violations).contains("CONTAINER_PENETRATION"))
        let cp = entry(result.violations, type: "CONTAINER_PENETRATION")!
        XCTAssertEqual(cp["object"]?.stringValue, "a")
        XCTAssertGreaterThan(cp["penetration_depth_m"]!.doubleValue!, 0)
    }

    func testFloatingObject() {
        // gap above the floor, nothing beneath
        let result = validateLayout(sceneOf(obj("a", Vec3(0.2, 0.2, 0.2), Vec3(0, -0.2, 0))))
        XCTAssertFalse(result.valid)
        XCTAssertTrue(types(result.violations).contains("UNSUPPORTED_OBJECT"))
        let u = entry(result.violations, type: "UNSUPPORTED_OBJECT")!
        XCTAssertEqual(u["object"]?.stringValue, "a")
        XCTAssertEqual(u["support_ratio"]?.doubleValue, 0.0)
    }

    func testUnstableButSupportedIsWarningNotViolation() {
        let base = obj("base", Vec3(0.4, 0.2, 0.4), Vec3(0, -0.4, 0))   // top rect [-0.2,0.2]^2
        // bottom rect x[0.15,0.35] z[-0.1,0.1]: only x[0.15,0.2] overlaps -> hangs off
        let hanger = obj("hanger", Vec3(0.2, 0.1, 0.2), Vec3(0.25, -0.25, 0))
        let result = validateLayout(sceneOf(base, hanger))
        XCTAssertFalse(types(result.violations).contains("UNSTABLE_STACK"))
        XCTAssertTrue(types(result.warnings).contains("UNSTABLE_STACK"))
        // documented policy: unstable-but-supported does not invalidate the scene
        XCTAssertTrue(result.valid)
        let stack = entry(result.warnings, type: "UNSTABLE_STACK")!
        XCTAssertEqual(stack["object"]?.stringValue, "hanger")
        XCTAssertLessThan(stack["stability_margin_m"]!.doubleValue!, 0)
    }

    func testMalformedGeometryNaNDimensionsDoesNotThrow() {
        let result = validateLayout(sceneOf(obj("a", Vec3(0.2, .nan, 0.2), Vec3(0, -0.4, 0))))
        XCTAssertFalse(result.valid)
        XCTAssertEqual(result.score, 0.0)
        XCTAssertEqual(result.violations.count, 1)
        XCTAssertEqual(result.violations[0]["type"]?.stringValue, "MALFORMED_GEOMETRY")
        XCTAssertEqual(result.violations[0]["object"]?.stringValue, "a")
        XCTAssertEqual(result.warnings, [])
        XCTAssertEqual(result.metrics, .object([:]))
    }

    func testDuplicateIdsDoesNotThrow() {
        let a = obj("dup", Vec3(0.2, 0.2, 0.2), Vec3(0, -0.4, 0))
        let b = obj("dup", Vec3(0.2, 0.2, 0.2), Vec3(0.3, -0.4, 0))
        let result = validateLayout(sceneOf(a, b))
        XCTAssertFalse(result.valid)
        XCTAssertEqual(result.score, 0.0)
        XCTAssertEqual(result.violations[0]["type"]?.stringValue, "MALFORMED_GEOMETRY")
    }

    func testDeterminism() throws {
        let a = obj("a", Vec3(0.4, 0.2, 0.4), Vec3(0, -0.4, 0))
        let b = obj("b", Vec3(0.4, 0.2, 0.4), Vec3(0.1, -0.4, 0))   // colliding
        let c = obj("c", Vec3(0.2, 0.2, 0.2), Vec3(0, -0.2, 0))     // floating
        let scene = sceneOf(a, b, c)
        let r1 = validateLayout(scene), r2 = validateLayout(scene)
        XCTAssertEqual(r1, r2)
        XCTAssertEqual(try resultToJSON(r1), try resultToJSON(r2))
    }

    func testRotatedButValidScene() {
        let a = obj("a", Vec3(0.2, 0.2, 0.2), Vec3(0, -0.4, 0),
                    rotation: .yaw(radians: 45.0 * .pi / 180.0))
        let result = validateLayout(sceneOf(a))
        XCTAssertTrue(result.valid)
        XCTAssertEqual(result.score, 1.0, accuracy: 1e-6)
        XCTAssertEqual(result.violations, [])
    }

    func testResultJSONRoundTrip() throws {
        let a = obj("a", Vec3(0.4, 0.2, 0.4), Vec3(0, -0.4, 0))
        let b = obj("b", Vec3(0.4, 0.2, 0.4), Vec3(0.1, -0.4, 0))
        let result = validateLayout(sceneOf(a, b))
        let back = try resultFromJSON(try resultToJSON(result))
        XCTAssertEqual(back, result)
        XCTAssertEqual(back.asJSON(), result.asJSON())
    }

    func testValidateAppliesPlacements() {
        let a = obj("a", Vec3(0.2, 0.2, 0.2), Vec3(0, 0, 0))  // floating as given
        XCTAssertFalse(validate(sceneOf(a)).valid)
        // placed flush on the floor by the solver
        XCTAssertTrue(validate(sceneOf(a), placements: [Placement(id: "a", position: Vec3(0, -0.4, 0))]).valid)
    }

    func testValidateUnknownPlacementIdIsMalformed() {
        let a = obj("a", Vec3(0.2, 0.2, 0.2), Vec3(0, -0.4, 0))
        let result = validate(sceneOf(a), placements: [Placement(id: "ghost", position: .zero)])
        XCTAssertFalse(result.valid)
        XCTAssertEqual(result.score, 0.0)
        XCTAssertEqual(result.violations.count, 1)
        XCTAssertEqual(result.violations[0]["type"]?.stringValue, "MALFORMED_GEOMETRY")
        XCTAssertEqual(result.violations[0]["object"]?.stringValue, "ghost")
        XCTAssertFalse((result.violations[0]["detail"]?.stringValue ?? "").isEmpty)
    }
}

/// Port of test_validator.py's TestCompressibility.
///
/// Note: for dims (0.4, 0.2, 0.4) at x-offset 0.1 SAT's minimum-overlap (MTV)
/// axis is Y (extent 0.2, zero center offset -> overlap 0.2), not X (extent 0.4,
/// overlap 0.3). So the raw penetration is 0.2 m along Y and the allowance below
/// comes from each object's Y extent (0.2 m), not its X extent.
/// Named so `swift test --filter ValidatorTests` picks this class up too.
final class ValidatorTestsCompressibility: XCTestCase {
    func testSoftOverlapWithinAllowanceIsWarningNotViolation() {
        let a = obj("a", Vec3(0.4, 0.2, 0.4), Vec3(0, -0.4, 0), rigidity: .soft, compressibilityK: 3.0)
        let b = obj("b", Vec3(0.4, 0.2, 0.4), Vec3(0.1, -0.4, 0), rigidity: .soft, compressibilityK: 3.0)
        let result = validateLayout(sceneOf(a, b))
        XCTAssertFalse(types(result.violations).contains("OBJECT_COLLISION"))
        XCTAssertTrue(result.valid)
        XCTAssertTrue(types(result.warnings).contains("SOFT_COMPRESSION"))
        let w = entry(result.warnings, type: "SOFT_COMPRESSION")!
        XCTAssertEqual(w["objects"], .strings(["a", "b"]))
        XCTAssertEqual(w["raw_penetration_depth_m"]!.doubleValue!, 0.2, accuracy: 1e-6)
        XCTAssertEqual(w["compressed_depth_m"]!.doubleValue!, 0.2, accuracy: 1e-6)
    }

    func testSoftOverlapPastAllowanceStillViolationButReducedSeverity() {
        let aSoft = obj("a", Vec3(0.4, 0.2, 0.4), Vec3(0, -0.4, 0), rigidity: .soft, compressibilityK: 1.2)
        let bSoft = obj("b", Vec3(0.4, 0.2, 0.4), Vec3(0.1, -0.4, 0), rigidity: .soft, compressibilityK: 1.2)
        let softResult = validateLayout(sceneOf(aSoft, bSoft))
        XCTAssertTrue(types(softResult.violations).contains("OBJECT_COLLISION"))
        let softColl = entry(softResult.violations, type: "OBJECT_COLLISION")!
        // raw penetration is 0.2 m (same geometry as testObjectCollision); the
        // allowance only partially absorbs it.
        XCTAssertLessThan(softColl["penetration_depth_m"]!.doubleValue!, 0.2)
        XCTAssertGreaterThan(softColl["penetration_depth_m"]!.doubleValue!, 0.0)

        let rigidResult = validateLayout(sceneOf(
            obj("a", Vec3(0.4, 0.2, 0.4), Vec3(0, -0.4, 0)),
            obj("b", Vec3(0.4, 0.2, 0.4), Vec3(0.1, -0.4, 0))))
        let rigidColl = entry(rigidResult.violations, type: "OBJECT_COLLISION")!
        XCTAssertLessThan(softColl["severity"]!.doubleValue!, rigidColl["severity"]!.doubleValue!)
    }

    func testRigidPairUnaffectedByCompressibilityWiring() {
        let result = validateLayout(sceneOf(
            obj("a", Vec3(0.4, 0.2, 0.4), Vec3(0, -0.4, 0)),
            obj("b", Vec3(0.4, 0.2, 0.4), Vec3(0.1, -0.4, 0))))
        let coll = entry(result.violations, type: "OBJECT_COLLISION")!
        XCTAssertEqual(coll["penetration_depth_m"]!.doubleValue!, 0.2, accuracy: 1e-6)
        XCTAssertEqual(result.warnings, [])
    }

    func testSoftObjectBulgingPastWallWithinAllowanceIsWarning() {
        // half-height 0.25 -> bottom at y=-0.65, floor at -0.5: raw penetration 0.15 m.
        let a = obj("a", Vec3(0.2, 0.5, 0.2), Vec3(0, -0.4, 0), rigidity: .soft, compressibilityK: 2.0)
        let result = validateLayout(sceneOf(a))
        XCTAssertFalse(types(result.violations).contains("CONTAINER_PENETRATION"))
        XCTAssertTrue(types(result.warnings).contains("SOFT_COMPRESSION"))
        let w = entry(result.warnings, type: "SOFT_COMPRESSION")!
        XCTAssertEqual(w["object"]?.stringValue, "a")
        XCTAssertEqual(w["raw_penetration_depth_m"]!.doubleValue!, 0.15, accuracy: 1e-6)
    }

    func testDeterminismWithCompressibility() throws {
        let a = obj("a", Vec3(0.4, 0.2, 0.4), Vec3(0, -0.4, 0), rigidity: .soft, compressibilityK: 1.2)
        let b = obj("b", Vec3(0.4, 0.2, 0.4), Vec3(0.1, -0.4, 0), rigidity: .soft, compressibilityK: 1.2)
        let scene = sceneOf(a, b)
        let r1 = validateLayout(scene), r2 = validateLayout(scene)
        XCTAssertEqual(r1, r2)
        XCTAssertEqual(try resultToJSON(r1), try resultToJSON(r2))
    }
}
