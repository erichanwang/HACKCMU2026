// Port of tests/test_support.py, plus fixture-dataset checks against the numbers
// the Python `check_support` reports for the same scenes.

import Foundation
import XCTest
@testable import PackPhysics

private let container = Container(id: "suitcase", dimensions: Vec3(1.0, 1.0, 1.0), position: Vec3(0, 0, 0))
private let sqrt2 = 2.0.squareRoot()

private func makeObject(_ id: String, _ dims: Vec3, _ position: Vec3) -> SceneObject {
    SceneObject(id: id, dimensions: dims, position: position)
}

/// Quaternion (x, y, z, w) for a rotation about world Y.
private func yaw(_ degrees: Double) -> Quat {
    let half = degrees * .pi / 180.0 / 2.0
    return Quat(x: 0, y: sin(half), z: 0, w: cos(half))
}

/// Quaternion (x, y, z, w) for a rotation about world X.
private func pitch(_ degrees: Double) -> Quat {
    let half = degrees * .pi / 180.0 / 2.0
    return Quat(x: sin(half), y: 0, z: 0, w: cos(half))
}

private func sceneOf(_ objects: SceneObject...) -> Scene {
    Scene(container: container, objects: objects)
}

private func byId(_ results: [SupportResult]) -> [String: SupportResult] {
    Dictionary(uniqueKeysWithValues: results.map { ($0.objectId, $0) })
}

final class SupportTests: XCTestCase {
    func testFlushOnFloor() throws {
        // floor world-y = -0.5; half-height 0.1 -> bottom at -0.5 exactly.
        let results = try checkSupport(sceneOf(makeObject("a", Vec3(0.2, 0.2, 0.2), Vec3(0, -0.4, 0))))
        XCTAssertEqual(results.count, 1)
        let r = results[0]
        XCTAssertEqual(r.supportRatio, 1.0, accuracy: 1e-6)
        XCTAssertEqual(r.supportingObjects, ["container_floor"])
        XCTAssertFalse(r.floating)
        XCTAssertFalse(r.unstable)
        XCTAssertGreaterThan(r.stabilityMarginM, 0)
    }

    func testFloatingAboveFloor() throws {
        // bottom at -0.3, gap of 0.2 m above floor (-0.5) -> no contact.
        let results = try checkSupport(sceneOf(makeObject("a", Vec3(0.2, 0.2, 0.2), Vec3(0, -0.2, 0))))
        let r = results[0]
        XCTAssertEqual(r.supportRatio, 0.0)
        XCTAssertEqual(r.supportingObjects, [])
        XCTAssertTrue(r.floating)
        XCTAssertEqual(r.stabilityMarginM, floatingMarginSentinelM)
        XCTAssertTrue(r.unstable)
        XCTAssertEqual(r.contactPolygon, [])
        XCTAssertEqual(r.patchAreasM2, [:])
    }

    func testStackedCleanlyOnOtherObject() throws {
        let base = makeObject("base", Vec3(0.4, 0.2, 0.4), Vec3(0, -0.4, 0))  // top_y = -0.3
        let top = makeObject("top", Vec3(0.2, 0.2, 0.2), Vec3(0, -0.2, 0))  // bottom_y = -0.3
        let r = byId(try checkSupport(sceneOf(base, top)))
        XCTAssertEqual(r["top"]!.supportingObjects, ["base"])
        XCTAssertEqual(r["top"]!.supportRatio, 1.0, accuracy: 1e-6)
        XCTAssertFalse(r["top"]!.unstable)
        // base itself rests on the floor
        XCTAssertEqual(r["base"]!.supportingObjects, ["container_floor"])
    }

    func testPrecariousSmallPositiveMargin() throws {
        // base top footprint: x in [-0.2, 0.2], z in [-0.2, 0.2], top_y = -0.3
        let base = makeObject("base", Vec3(0.4, 0.2, 0.4), Vec3(0, -0.4, 0))
        // perched object: bottom rect x in [0.13, 0.23], z in [-0.05, 0.05]
        let perched = makeObject("p", Vec3(0.1, 0.1, 0.1), Vec3(0.18, -0.25, 0))
        let p = byId(try checkSupport(sceneOf(base, perched)))["p"]!
        XCTAssertEqual(p.supportingObjects, ["base"])
        XCTAssertEqual(p.supportRatio, 0.7, accuracy: 1e-6)
        // by hand: clipped support rect x[0.13,0.20] z[-0.05,0.05], COM at (0.18, 0)
        // margin = min(0.05, 0.02, 0.05, 0.05) = 0.02
        XCTAssertEqual(p.stabilityMarginM, 0.02, accuracy: 1e-6)
        XCTAssertFalse(p.unstable)
    }

    func testHangingOffEdgeUnstable() throws {
        let base = makeObject("base", Vec3(0.4, 0.2, 0.4), Vec3(0, -0.4, 0))  // top rect [-0.2,0.2]^2
        // bottom rect x[0.15,0.35], z[-0.1,0.1]; only x[0.15,0.2] overlaps base
        let hanger = makeObject("h", Vec3(0.2, 0.1, 0.2), Vec3(0.25, -0.25, 0))
        let h = byId(try checkSupport(sceneOf(base, hanger)))["h"]!
        XCTAssertEqual(h.supportRatio, 0.25, accuracy: 1e-6)
        // by hand: COM (0.25, 0) vs support rect x[0.15,0.2] z[-0.1,0.1]
        // dx = 0.05, dz = 0 -> margin = -0.05
        XCTAssertEqual(h.stabilityMarginM, -0.05, accuracy: 1e-6)
        XCTAssertTrue(h.unstable)
    }

    func testPartialOverlapTwoSupportsCombine() throws {
        let a1 = makeObject("a1", Vec3(0.2, 0.2, 0.2), Vec3(-0.1, -0.4, 0))  // top rect x[-0.2,0.0]
        let a2 = makeObject("a2", Vec3(0.2, 0.2, 0.2), Vec3(0.1, -0.4, 0))  // top rect x[0.0,0.2]
        let bridge = makeObject("bridge", Vec3(0.3, 0.1, 0.2), Vec3(0, -0.25, 0))  // bottom x[-0.15,0.15]
        let b = byId(try checkSupport(sceneOf(a1, a2, bridge)))["bridge"]!
        XCTAssertEqual(Set(b.supportingObjects), Set(["a1", "a2"]))
        XCTAssertEqual(b.supportRatio, 1.0, accuracy: 1e-6)
        XCTAssertFalse(b.unstable)
    }

    func testMarginalContactAroundFloatingThreshold() throws {
        let base = makeObject("base", Vec3(0.4, 0.2, 0.4), Vec3(0, -0.4, 0))  // top rect [-0.2,0.2]^2
        // footprint width/height 0.2x0.2 = area 0.04
        let below = makeObject("below", Vec3(0.2, 0.1, 0.2), Vec3(0.291, -0.25, 0))
        let above = makeObject("above", Vec3(0.2, 0.1, 0.2), Vec3(0.289, -0.25, 0))
        let r = byId(try checkSupport(sceneOf(base, below, above)))
        XCTAssertLessThan(r["below"]!.supportRatio, 0.05)
        XCTAssertTrue(r["below"]!.floating)
        XCTAssertGreaterThanOrEqual(r["above"]!.supportRatio, 0.05)
        XCTAssertFalse(r["above"]!.floating)
    }

    // MARK: - Rotated footprints
    // Exact polygon footprints: cases the old bounding-rectangle approximation
    // could not represent.

    func testYawedCubeOnFloor() throws {
        // 0.2 cube yawed 45 deg, flat on the floor: footprint is a diamond of area
        // 0.04 (not its 0.08 bounding rectangle), fully on the floor.
        let obj = SceneObject(id: "d", dimensions: Vec3(0.2, 0.2, 0.2), position: Vec3(0, -0.4, 0),
                              rotation: yaw(45))
        let r = try checkSupport(Scene(container: container, objects: [obj]))[0]
        XCTAssertEqual(r.supportRatio, 1.0, accuracy: 1e-9)
        XCTAssertEqual(r.patchAreasM2["container_floor"]!, 0.04, accuracy: 1e-9)
        XCTAssertEqual(r.contactPolygon.count, 4)
        // A square footprint of side s has inradius s/2 at ANY yaw.
        XCTAssertEqual(r.stabilityMarginM, 0.1, accuracy: 1e-9)
        XCTAssertFalse(r.unstable)
    }

    func testYawedCubePartiallyOnSupporter() throws {
        // base top face: x,z in [-0.2, 0.2], top_y = -0.3.
        let base = makeObject("base", Vec3(0.4, 0.2, 0.4), Vec3(0, -0.4, 0))
        // 0.2 cube yawed 45 -> diamond, half-diagonal r = 0.1*sqrt(2), area 2r^2 = 0.04.
        // Centre it at x = 0.2 + r/2 so the base's x <= 0.2 edge keeps exactly the
        // small triangle with legs (r - r/2): area (r/2)^2 = r^2/4 = 0.005.
        // support_ratio = 0.005 / 0.04 = 0.125 exactly.
        let rHalfDiag = 0.1 * sqrt2
        let diamond = SceneObject(id: "d", dimensions: Vec3(0.2, 0.2, 0.2),
                                  position: Vec3(0.2 + rHalfDiag / 2.0, -0.2, 0), rotation: yaw(45))
        let d = byId(try checkSupport(Scene(container: container, objects: [base, diamond])))["d"]!
        XCTAssertEqual(d.supportingObjects, ["base"])
        XCTAssertEqual(d.patchAreasM2["base"]!, 0.005, accuracy: 1e-9)
        XCTAssertEqual(d.supportRatio, 0.125, accuracy: 1e-9)
        // The removed bounding-rectangle method: bottom rect 0.2828^2 = 0.08, clipped
        // to x <= 0.2 -> 0.0707 x 0.2828 = 0.02 -> ratio 0.25, i.e. it over-reported
        // support by 2x here.
        XCTAssertGreaterThan(abs(d.supportRatio - 0.25), 1e-3)
        // COM at x = 0.2 + r/2 is off the support triangle -> unstable.
        XCTAssertTrue(d.unstable)
        XCTAssertLessThan(d.stabilityMarginM, 0.0)
    }

    func testPitchedBoxRestsOnAnEdge() throws {
        // Pitched 30 deg about X, so only the bottom local edge (y=-h/2, z=+d/2)
        // touches the floor: the bottom hull is a 2-point segment. Choosing
        // d = h * tan(30) puts that edge's world z at exactly 0, i.e. directly under
        // the COM -> the COM lies ON the (degenerate) support polygon, the
        // balanced-on-an-edge boundary case.
        let theta = 30.0 * .pi / 180.0
        let h = 0.2, d = 0.2 * tan(theta)
        let drop = 0.5 * h * cos(theta) + 0.5 * d * sin(theta)
        let obj = SceneObject(id: "p", dimensions: Vec3(0.2, h, d),
                              position: Vec3(0, -0.5 + drop, 0), rotation: pitch(30))
        let r = try checkSupport(Scene(container: container, objects: [obj]))[0]
        XCTAssertEqual(r.supportingObjects, ["container_floor"])
        XCTAssertEqual(r.contactPolygon.count, 2)  // a segment, zero area
        XCTAssertEqual(r.patchAreasM2["container_floor"]!, 0.0, accuracy: 1e-12)
        // Degenerate rule: both bottom contact points are on the floor -> 1.0.
        XCTAssertEqual(r.supportRatio, 1.0, accuracy: 1e-9)
        XCTAssertFalse(r.floating)
        // Tie-break: COM exactly on the support segment -> margin snapped to 0.0
        // (boundary counts as supported), so not unstable.
        XCTAssertLessThanOrEqual(abs(r.stabilityMarginM), 1e-9)
        XCTAssertFalse(r.unstable)
    }

    func testPitchedCubeComOffContactEdgeIsUnstable() throws {
        // Same pitch, but a cube: its contact edge lands at world
        // z = 0.1*(cos30 - sin30) = 0.0366, so the COM (z=0) is off the segment ->
        // margin = -0.0366, unstable.
        let theta = 30.0 * .pi / 180.0
        let drop = 0.1 * cos(theta) + 0.1 * sin(theta)
        let obj = SceneObject(id: "p", dimensions: Vec3(0.2, 0.2, 0.2),
                              position: Vec3(0, -0.5 + drop, 0), rotation: pitch(30))
        let r = try checkSupport(Scene(container: container, objects: [obj]))[0]
        XCTAssertEqual(r.supportRatio, 1.0, accuracy: 1e-9)
        XCTAssertEqual(r.stabilityMarginM, -0.1 * (cos(theta) - sin(theta)), accuracy: 1e-9)
        XCTAssertTrue(r.unstable)
    }

    // MARK: - Chain and diagnostics

    func testChainInstabilityPropagatesUpward() throws {
        let a = makeObject("A", Vec3(0.4, 0.2, 0.4), Vec3(0, -0.4, 0))  // floor, stable
        let b = makeObject("B", Vec3(0.2, 0.1, 0.2), Vec3(0.25, -0.25, 0))  // hangs off A -> unstable
        let c = makeObject("C", Vec3(0.2, 0.1, 0.2), Vec3(0.25, -0.15, 0))  // flush on B
        let r = byId(try checkSupport(sceneOf(a, b, c)))
        XCTAssertFalse(r["A"]!.unstable)
        XCTAssertFalse(r["A"]!.supportedByUnstable)
        XCTAssertTrue(r["B"]!.unstable)
        XCTAssertFalse(r["B"]!.supportedByUnstable)  // A itself is fine
        XCTAssertFalse(r["C"]!.unstable)  // C sits squarely on B
        XCTAssertEqual(r["C"]!.supportRatio, 1.0, accuracy: 1e-9)
        XCTAssertTrue(r["C"]!.supportedByUnstable)  // ...but B is not
    }

    func testThreeSupporters() throws {
        let blocks = [-0.3, 0.0, 0.3].enumerated().map { i, x in
            makeObject("b\(i)", Vec3(0.1, 0.1, 0.1), Vec3(x, -0.45, 0))
        }
        let plank = makeObject("plank", Vec3(0.8, 0.05, 0.1), Vec3(0, -0.375, 0))
        let p = byId(try checkSupport(Scene(container: container, objects: blocks + [plank])))["plank"]!
        XCTAssertEqual(p.supportingObjects, ["b0", "b1", "b2"])
        XCTAssertEqual(p.patchAreasM2.keys.sorted(), ["b0", "b1", "b2"])
        for area in p.patchAreasM2.values {
            XCTAssertEqual(area, 0.01, accuracy: 1e-9)
        }
        // 3 x 0.01 covered of a 0.8 x 0.1 bottom
        XCTAssertEqual(p.supportRatio, 0.03 / 0.08, accuracy: 1e-9)
        // hull spans the outer blocks' far edges
        let xs = p.contactPolygon.map { $0[0] }
        XCTAssertEqual(xs.min()!, -0.35, accuracy: 1e-9)
        XCTAssertEqual(xs.max()!, 0.35, accuracy: 1e-9)
        XCTAssertEqual(p.stabilityMarginM, 0.05, accuracy: 1e-9)  // z half-width
        XCTAssertFalse(p.unstable)
        XCTAssertFalse(p.supportedByUnstable)
    }

    func testDeterministic() throws {
        let scene = sceneOf(
            makeObject("base", Vec3(0.4, 0.2, 0.4), Vec3(0, -0.4, 0)),
            SceneObject(id: "d", dimensions: Vec3(0.2, 0.2, 0.2), position: Vec3(0.05, -0.2, 0),
                        rotation: yaw(37)),
            makeObject("h", Vec3(0.2, 0.1, 0.2), Vec3(0.25, -0.25, 0))
        )
        XCTAssertEqual(try checkSupport(scene), try checkSupport(scene))
    }

    // MARK: - Fixture dataset (expectations from the Python `check_support`)

    func testFixtureValidPackedSceneIsFullySupported() throws {
        let results = try checkSupport(Parity.scene(named: "fixture:valid_packed_scene"))
        XCTAssertEqual(results.count, 7)
        for r in results {
            XCTAssertEqual(r.supportRatio, 1.0, accuracy: 1e-9, r.objectId)
            XCTAssertFalse(r.floating, r.objectId)
            XCTAssertFalse(r.unstable, r.objectId)
            XCTAssertFalse(r.supportedByUnstable, r.objectId)
            XCTAssertGreaterThan(r.stabilityMarginM, 0, r.objectId)
        }
        // Per the fixture docstring: charger and the bottle are flush on the
        // headphones case, the camera on the toiletry bag, the rest on the floor.
        let supporters = byId(results).mapValues(\.supportingObjects)
        XCTAssertEqual(supporters, [
            "laptop": ["container_floor"],
            "headphones_case": ["container_floor"],
            "charger": ["headphones_case"],
            "toiletry_bottle": ["headphones_case"],
            "shoe": ["container_floor"],
            "toiletry_bag": ["container_floor"],
            "camera": ["toiletry_bag"],
        ])
    }

    func testFixtureFloatingObject() throws {
        let r = byId(try checkSupport(Parity.scene(named: "fixture:scene_with_floating_object")))
        let charger = r["charger"]!
        XCTAssertTrue(charger.floating)
        XCTAssertTrue(charger.unstable)
        XCTAssertEqual(charger.supportRatio, 0.0)
        XCTAssertEqual(charger.supportingObjects, [])
        XCTAssertEqual(charger.stabilityMarginM, floatingMarginSentinelM)
        XCTAssertEqual(charger.contactPolygon, [])
        for id in ["laptop", "shoe"] {
            XCTAssertFalse(r[id]!.floating)
            XCTAssertEqual(r[id]!.supportingObjects, ["container_floor"])
        }
    }

    func testFixturePrecariousBalance() throws {
        let r = byId(try checkSupport(Parity.scene(named: "fixture:scene_with_precarious_balance")))
        let camera = r["camera"]!
        XCTAssertEqual(camera.supportingObjects, ["toiletry_bag"])
        XCTAssertTrue(camera.unstable)
        XCTAssertFalse(camera.floating)
        // Python check_support on this fixture, to the bit:
        XCTAssertEqual(camera.stabilityMarginM, -0.01999999999999999, accuracy: 1e-9)
        XCTAssertEqual(camera.supportRatio, 0.3461538461538463, accuracy: 1e-9)
        XCTAssertEqual(camera.patchAreasM2["toiletry_bag"]!, 0.004500000000000002, accuracy: 1e-9)
        XCTAssertFalse(r["toiletry_bag"]!.unstable)
        XCTAssertEqual(r["toiletry_bag"]!.stabilityMarginM, 0.075, accuracy: 1e-9)
    }
}
