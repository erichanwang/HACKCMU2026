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

private func makeObject(_ id: String, _ dims: Vec3, _ position: Vec3, _ rotation: Quat = .identity) -> SceneObject {
    SceneObject(id: id, dimensions: dims, position: position, rotation: rotation)
}

final class ContainmentTests: XCTestCase {
    func testFullyContainedAxisAligned() throws {
        let container = try obbFrom(makeContainer())
        let obj = try obbFrom(makeObject("a", Vec3(0.5, 0.5, 0.5), Vec3(0, 0, 0)))
        let result = checkContainment(container: container, object: obj)
        XCTAssertTrue(result.contained)
        XCTAssertEqual(result.penetratingVertices, [])
        XCTAssertEqual(result.penetrationDepthM, 0.0)
        XCTAssertEqual(result.violatedWalls, [])
    }

    func testFullyContainedRotatedObject() throws {
        let container = try obbFrom(makeContainer())
        let obj = try obbFrom(makeObject("a", Vec3(0.5, 0.5, 0.5), Vec3(0, 0, 0), quat(axis: Vec3(0, 1, 0), degrees: 45)))
        let result = checkContainment(container: container, object: obj)
        XCTAssertTrue(result.contained)
    }

    func testObjectExactlyTouchingWall() throws {
        let container = try obbFrom(makeContainer())  // half-extents (1,1,1)
        let obj = try obbFrom(makeObject("a", Vec3(1.0, 1.0, 1.0), Vec3(0.5, 0, 0)))  // vertex at x=1.0
        let result = checkContainment(container: container, object: obj)
        XCTAssertTrue(result.contained)
        XCTAssertEqual(result.violatedWalls, [])
    }

    func testObjectPenetratingWallKnownAmount() throws {
        let container = try obbFrom(makeContainer())
        let obj = try obbFrom(makeObject("a", Vec3(1.0, 1.0, 1.0), Vec3(0.51, 0, 0)))  // vertex at x=1.01
        let result = checkContainment(container: container, object: obj)
        XCTAssertFalse(result.contained)
        XCTAssertEqual(result.penetrationDepthM, 0.01, accuracy: 1e-6)
        XCTAssertEqual(result.violatedWalls, ["+x"])
        XCTAssertEqual(result.penetratingVertices.count, 4)  // the 4 vertices on the +x face
    }

    func testCenterInsideCornerPokesOutDueToRotation() throws {
        // Center is well inside the container (not touching any wall), but a
        // 45-degree roll pushes one corner through the +x wall. A naive
        // "is the center inside the box" check would wrongly pass this.
        let container = try obbFrom(makeContainer())  // half-extents (1,1,1)
        let obj = try obbFrom(makeObject("a", Vec3(0.4, 0.4, 0.4), Vec3(0.75, 0, 0), quat(axis: Vec3(0, 0, 1), degrees: 45)))
        let result = checkContainment(container: container, object: obj)
        XCTAssertFalse(result.contained)
        XCTAssertTrue(result.violatedWalls.contains("+x"))
        let rad = 45.0 * .pi / 180.0
        let expectedDepth = 0.75 + 0.2 * cos(rad) + 0.2 * sin(rad) - 1.0
        XCTAssertEqual(result.penetrationDepthM, expectedDepth, accuracy: 1e-6)
        // Single wall involved -- perWallDepthM must report exactly it, at
        // the same depth as the overall max.
        XCTAssertEqual(Set(result.perWallDepthM.keys), ["+x"])
        XCTAssertEqual(result.perWallDepthM["+x"]!, expectedDepth, accuracy: 1e-6)
    }

    func testObjectEntirelyOutside() throws {
        let container = try obbFrom(makeContainer())
        let obj = try obbFrom(makeObject("a", Vec3(0.5, 0.5, 0.5), Vec3(10.0, 0, 0)))
        let result = checkContainment(container: container, object: obj)
        XCTAssertFalse(result.contained)
        XCTAssertEqual(result.penetratingVertices.count, 8)
    }

    func testObjectBiggerThanContainerInOneDimension() throws {
        let container = try obbFrom(makeContainer())  // half-extents (1,1,1)
        let obj = try obbFrom(makeObject("a", Vec3(3.0, 0.5, 0.5), Vec3(0, 0, 0)))  // half-extent x=1.5
        let result = checkContainment(container: container, object: obj)
        XCTAssertFalse(result.contained)
        XCTAssertTrue(result.violatedWalls.contains("+x"))
        XCTAssertTrue(result.violatedWalls.contains("-x"))
        XCTAssertEqual(result.penetrationDepthM, 0.5, accuracy: 1e-6)
    }

    func testRotatedContainerWithFittingObject() throws {
        // Container itself rotated 45 degrees about Y and offset from origin.
        let rot = quat(axis: Vec3(0, 1, 0), degrees: 45)
        let containerEntity = makeContainer(dims: Vec3(2, 2, 2), position: Vec3(5, 1, -3), rotation: rot)
        let container = try obbFrom(containerEntity)
        // Object shares the container's rotation and is centered inside it,
        // so in the container's local frame it's just a small centered box.
        let obj = try obbFrom(makeObject("a", Vec3(0.5, 0.5, 0.5), Vec3(5, 1, -3), rot))
        let result = checkContainment(container: container, object: obj)
        XCTAssertTrue(result.contained)
    }

    func testNanDimensionsRaises() throws {
        let scene = Scene(container: makeContainer(), objects: [makeObject("bad", Vec3(.nan, 1, 1), Vec3(0, 0, 0))])
        XCTAssertThrowsError(try checkSceneContainment(scene)) { err in
            XCTAssertEqual((err as? MalformedSceneError)?.objectId, "bad")
        }
    }

    func testNanPositionRaises() throws {
        let scene = Scene(container: makeContainer(), objects: [makeObject("bad", Vec3(1, 1, 1), Vec3(.nan, 0, 0))])
        XCTAssertThrowsError(try checkSceneContainment(scene)) { err in
            XCTAssertEqual((err as? MalformedSceneError)?.objectId, "bad")
        }
    }

    func testDuplicateIdsRaise() throws {
        let scene = Scene(container: makeContainer(), objects: [
            makeObject("dup", Vec3(0.1, 0.1, 0.1), Vec3(0, 0, 0)),
            makeObject("dup", Vec3(0.1, 0.1, 0.1), Vec3(0.2, 0, 0)),
        ])
        XCTAssertThrowsError(try checkNoDuplicateIds(scene)) { err in
            XCTAssertEqual((err as? MalformedSceneError)?.objectId, "dup")
        }
    }

    func testUniqueIdsOk() throws {
        let scene = Scene(container: makeContainer(), objects: [
            makeObject("a", Vec3(0.1, 0.1, 0.1), Vec3(0, 0, 0)),
            makeObject("b", Vec3(0.1, 0.1, 0.1), Vec3(0.2, 0, 0)),
        ])
        XCTAssertNoThrow(try checkNoDuplicateIds(scene))
    }

    func testReturnsOnlyViolations() throws {
        let scene = Scene(container: makeContainer(), objects: [
            makeObject("fits", Vec3(0.5, 0.5, 0.5), Vec3(0, 0, 0)),
            makeObject("too_big", Vec3(3.0, 0.5, 0.5), Vec3(0, 0, 0)),
        ])
        let results = try checkSceneContainment(scene)
        XCTAssertEqual(results.count, 1)
        XCTAssertEqual(results[0].objectId, "too_big")
        XCTAssertFalse(results[0].contained)
    }

    func testAllFitReturnsEmptyList() throws {
        let scene = Scene(container: makeContainer(), objects: [makeObject("fits", Vec3(0.5, 0.5, 0.5), Vec3(0, 0, 0))])
        XCTAssertEqual(try checkSceneContainment(scene), [])
    }

    func testZeroObjectsReturnsEmptyList() throws {
        let scene = Scene(container: makeContainer(), objects: [])
        XCTAssertEqual(try checkSceneContainment(scene), [])
    }

    func testPrecomputedGeomMatchesDefault() throws {
        // scene_with_wall_penetration: laptop violates -x by 0.03m, shoe is
        // fully contained -- exactly one violation, with a known depth.
        let scene = Parity.scene(named: "fixture:scene_with_wall_penetration")
        let geom = try precompute(scene)
        let direct = try checkSceneContainment(scene)
        let viaGeom = try checkSceneContainment(scene, geom: geom)
        XCTAssertEqual(direct.count, viaGeom.count)
        for (a, b) in zip(direct, viaGeom) {
            XCTAssertEqual(a.objectId, b.objectId)
            XCTAssertEqual(a.contained, b.contained)
            XCTAssertEqual(a.penetrationDepthM, b.penetrationDepthM, accuracy: 1e-9)
            XCTAssertEqual(a.violatedWalls, b.violatedWalls)
            XCTAssertEqual(Set(a.perWallDepthM.keys), Set(b.perWallDepthM.keys))
            for (wall, depth) in a.perWallDepthM {
                XCTAssertEqual(depth, b.perWallDepthM[wall]!, accuracy: 1e-9)
            }
            XCTAssertEqual(a.penetratingVertices, b.penetratingVertices)
        }
    }

    func testTwoWallsViolatedByDifferentKnownAmounts() throws {
        // Container half-extents (1,1,1). Object half-extents (0.5,0.5,0.5)
        // at center (0.53, 0, -0.51):
        //   x-range = [0.03, 1.03]  -> +x overshoot = 1.03 - 1 = 0.03
        //   y-range = [-0.5, 0.5]   -> fully inside
        //   z-range = [-1.01, -0.01] -> -z overshoot = |-1.01| - 1 = 0.01
        let container = try obbFrom(makeContainer())
        let obj = try obbFrom(makeObject("a", Vec3(1.0, 1.0, 1.0), Vec3(0.53, 0.0, -0.51)))
        let result = checkContainment(container: container, object: obj)
        XCTAssertFalse(result.contained)
        XCTAssertEqual(result.violatedWalls, ["+x", "-z"])
        XCTAssertEqual(result.perWallDepthM["+x"]!, 0.03, accuracy: 1e-6)
        XCTAssertEqual(result.perWallDepthM["-z"]!, 0.01, accuracy: 1e-6)
        XCTAssertEqual(Set(result.perWallDepthM.keys), ["+x", "-z"])
        XCTAssertEqual(result.penetrationDepthM, 0.03, accuracy: 1e-6)
        XCTAssertEqual(result.penetrationDepthM, result.perWallDepthM.values.max()!, accuracy: 1e-9)
    }

    // MARK: - Dataset parity: checkSceneContainment reproduces the Python
    // violations' depths and wall names for the wall-penetration fixtures.

    private func assertContainmentParity(fixtureName: String) throws {
        let c = Parity.cases.first { $0.name == fixtureName }!
        let g = try precompute(c.scene)
        let results = try checkSceneContainment(c.scene, geom: g)
        let expectedViolations = c.expected["violations"]!.arrayValue!
        XCTAssertEqual(results.count, expectedViolations.count, fixtureName)
        for v in expectedViolations {
            let objectId = v["object"]!.stringValue!
            let result = results.first { $0.objectId == objectId }!
            // penetration_depth_m in fixtures is post-compressibility; for
            // rigid objects (all fixtures here) that equals the raw depth.
            XCTAssertEqual(result.penetrationDepthM, v["penetration_depth_m"]!.doubleValue!, accuracy: 1e-9, fixtureName)
            let expectedWalls = Set(v["violated_walls"]!.arrayValue!.map { $0.stringValue! })
            XCTAssertEqual(Set(result.violatedWalls), expectedWalls, fixtureName)
            let expectedPerWall = v["per_wall_depth_m"]!.objectValue!
            XCTAssertEqual(Set(result.perWallDepthM.keys), Set(expectedPerWall.keys), fixtureName)
            for (wall, depth) in expectedPerWall {
                XCTAssertEqual(result.perWallDepthM[wall]!, depth.doubleValue!, accuracy: 1e-9, "\(fixtureName) \(wall)")
            }
        }
    }

    func testDatasetParityWallPenetration() throws {
        try assertContainmentParity(fixtureName: "fixture:scene_with_wall_penetration")
    }

    func testDatasetParityOversizedObject() throws {
        try assertContainmentParity(fixtureName: "fixture:scene_oversized_object")
    }
}
