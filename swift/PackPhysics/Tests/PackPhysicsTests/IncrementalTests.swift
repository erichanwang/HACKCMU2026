// Port of tests/test_incremental.py.
//
// Strategy: place each fixture scene's objects one by one, bottom-Y-first (so
// supporters are always committed before whatever rests on them), and check the
// per-object results match what validateLayout finds for the whole scene --
// same vocabulary, same violation on the same object(s).

import Foundation
import XCTest
@testable import PackPhysics

private func bottomY(_ obj: SceneObject) throws -> Double {
    try obbVertices(obbFrom(obj)).map(\.y).min()!
}

/// Bottom-Y-first, ties keeping the fixture's original order (Python's `sorted`
/// is stable; Swift's `sort` is not, hence the index tie-break).
private func bottomUpOrder(_ objects: [SceneObject]) throws -> [SceneObject] {
    let keyed = try objects.enumerated().map { (i, o) in (i, o, try bottomY(o)) }
    return keyed.sorted { $0.2 == $1.2 ? $0.0 < $1.0 : $0.2 < $1.2 }.map(\.1)
}

/// Place every object in `scene`, bottom-Y-first. Returns the validator and the
/// per-id `place` results.
private func placeAll(_ scene: Scene) throws -> (PlacementValidator, [String: ValidationResult]) {
    let pv = try PlacementValidator(container: scene.container)
    var results: [String: ValidationResult] = [:]
    for obj in try bottomUpOrder(scene.objects) {
        results[obj.id] = pv.place(obj)
    }
    return (pv, results)
}

private func incEntry(_ entries: [JSONValue], type: String) -> JSONValue? {
    entries.first { $0["type"]?.stringValue == type }
}

private func incTypes(_ entries: [JSONValue]) -> [String] {
    entries.compactMap { $0["type"]?.stringValue }
}

private func cube1m(_ id: String = "c") -> Container {
    Container(id: id, dimensions: Vec3(1, 1, 1), position: Vec3(0, 0, 0))
}

final class IncrementalTests: XCTestCase {
    // MARK: - fixture equivalence

    func testValidPackedSceneAllAccepted() throws {
        let (pv, results) = try placeAll(Parity.scene(named: "fixture:valid_packed_scene"))
        for (oid, r) in results {
            XCTAssertTrue(r.valid, "\(oid): \(r.violations)")
        }
        XCTAssertTrue(validateLayout(pv.toScene()).valid)
    }

    func testCollisionRejectsSecondOfCollidingPair() throws {
        // laptop ties with shoe/headphones_case/toiletry_bag at bottom-y=0; the
        // fixture order puts laptop before shoe, so laptop commits first and
        // shoe is the one that collides with it.
        let (pv, results) = try placeAll(Parity.scene(named: "fixture:scene_with_collision"))
        XCTAssertTrue(results["laptop"]!.valid)
        XCTAssertFalse(results["shoe"]!.valid)
        let coll = incEntry(results["shoe"]!.violations, type: "OBJECT_COLLISION")!
        XCTAssertEqual(coll["objects"], .strings(["laptop", "shoe"]))
        XCTAssertEqual(coll["penetration_depth_m"]!.doubleValue!, 0.02, accuracy: 1e-6)
        XCTAssertFalse(pv.placedIds.contains("shoe"))
    }

    func testWallPenetrationRejectsLaptop() throws {
        let (pv, results) = try placeAll(Parity.scene(named: "fixture:scene_with_wall_penetration"))
        XCTAssertFalse(results["laptop"]!.valid)
        let cp = incEntry(results["laptop"]!.violations, type: "CONTAINER_PENETRATION")!
        XCTAssertEqual(cp["violated_walls"], .strings(["-x"]))
        XCTAssertFalse(pv.placedIds.contains("laptop"))
    }

    func testFloatingObjectRejected() throws {
        let (_, results) = try placeAll(Parity.scene(named: "fixture:scene_with_floating_object"))
        XCTAssertFalse(results["charger"]!.valid)
        XCTAssertTrue(incTypes(results["charger"]!.violations).contains("UNSUPPORTED_OBJECT"))
    }

    func testStackedObjectsBothAcceptedSecondHasNoWarnings() throws {
        let (pv, results) = try placeAll(Parity.scene(named: "fixture:scene_stacked_objects"))
        for (oid, r) in results { XCTAssertTrue(r.valid, "\(oid): \(r.violations)") }
        // toiletry_bag (bottom-y=0) commits before headphones_case (bottom-y=0.08).
        XCTAssertEqual(pv.placedIds, ["toiletry_bag", "headphones_case"])
        XCTAssertEqual(results["headphones_case"]!.warnings, [])
    }

    func testPrecariousBalanceAcceptedWithUnstableWarning() throws {
        let (_, results) = try placeAll(Parity.scene(named: "fixture:scene_with_precarious_balance"))
        XCTAssertTrue(results["camera"]!.valid)
        XCTAssertTrue(incTypes(results["camera"]!.warnings).contains("UNSTABLE_STACK"))
    }

    func testSoftItemCompressionWarnsNotViolates() throws {
        let (pv, results) = try placeAll(Parity.scene(named: "fixture:scene_with_soft_item_compression"))
        for (oid, r) in results { XCTAssertTrue(r.valid, "\(oid): \(r.violations)") }
        // clothes_bag and toiletry_bag both have bottom-y=0; the fixture order
        // puts clothes_bag first, so toiletry_bag is the second placement.
        XCTAssertEqual(pv.placedIds, ["clothes_bag", "toiletry_bag"])
        let second = results["toiletry_bag"]!
        XCTAssertEqual(second.violations, [])
        XCTAssertTrue(incTypes(second.warnings).contains("SOFT_COMPRESSION"))
    }

    // MARK: - fragile overload

    func testShoeOnFragileLaptopFlagsLaptopNotShoeGeometry() throws {
        let pv = try PlacementValidator(container: cube1m())
        let laptop = SceneObject(id: "laptop", dimensions: Vec3(0.4, 0.02, 0.3),
                                 position: Vec3(0, -0.49, 0), massKg: 1.3,
                                 constraints: Constraints(cannotSupportWeight: true))
        XCTAssertTrue(pv.place(laptop).valid)

        // flush on the laptop's top (y = -0.48), footprint inside the laptop's.
        let shoe = SceneObject(id: "shoe", dimensions: Vec3(0.2, 0.1, 0.15),
                               position: Vec3(0, -0.43, 0), massKg: 0.3)
        let result = pv.tryPlace(shoe)
        let overload = incEntry(result.violations, type: "FRAGILE_OBJECT_OVERLOADED")!
        XCTAssertEqual(overload["object"]?.stringValue, "laptop")
        XCTAssertEqual(overload["supported_weight_kg"]?.doubleValue, shoe.massKg)
        XCTAssertEqual(overload["direct_weight_kg"]?.doubleValue, shoe.massKg)
    }

    // MARK: - API contract

    func testTryPlaceDoesNotChangePlacedIds() throws {
        let pv = try PlacementValidator(container: cube1m())
        _ = pv.tryPlace(SceneObject(id: "a", dimensions: Vec3(0.2, 0.2, 0.2), position: Vec3(0, -0.4, 0)))
        XCTAssertEqual(pv.placedIds, [])
    }

    func testPlaceForceCommitsInvalidObject() throws {
        let pv = try PlacementValidator(container: cube1m())
        let a = SceneObject(id: "a", dimensions: Vec3(0.4, 0.2, 0.4), position: Vec3(0, -0.4, 0))
        let b = SceneObject(id: "b", dimensions: Vec3(0.4, 0.2, 0.4), position: Vec3(0.1, -0.4, 0))  // overlaps a
        XCTAssertTrue(pv.place(a).valid)
        let result = pv.place(b, force: true)
        XCTAssertFalse(result.valid)
        XCTAssertTrue(pv.placedIds.contains("b"))
    }

    func testRemoveThenReplace() throws {
        let pv = try PlacementValidator(container: cube1m())
        let a = SceneObject(id: "a", dimensions: Vec3(0.2, 0.2, 0.2), position: Vec3(0, -0.4, 0))
        _ = pv.place(a)
        pv.remove(id: "a")
        XCTAssertEqual(pv.placedIds, [])
        XCTAssertTrue(pv.place(a).valid)
        XCTAssertEqual(pv.placedIds, ["a"])
    }

    func testDuplicateIdIsMalformedNoThrow() throws {
        let pv = try PlacementValidator(container: cube1m())
        let a = SceneObject(id: "a", dimensions: Vec3(0.2, 0.2, 0.2), position: Vec3(0, -0.4, 0))
        _ = pv.place(a)
        let result = pv.tryPlace(SceneObject(id: "a", dimensions: Vec3(0.2, 0.2, 0.2), position: Vec3(0.5, -0.4, 0)))
        XCTAssertFalse(result.valid)
        XCTAssertEqual(result.violations[0]["type"]?.stringValue, "MALFORMED_GEOMETRY")
        XCTAssertEqual(result.violations[0]["object"]?.stringValue, "a")
    }

    func testNaNDimsMalformedDoesNotThrow() throws {
        let pv = try PlacementValidator(container: cube1m())
        let result = pv.tryPlace(SceneObject(id: "a", dimensions: Vec3(0.2, .nan, 0.2), position: Vec3(0, -0.4, 0)))
        XCTAssertFalse(result.valid)
        XCTAssertEqual(result.score, 0.0)
        XCTAssertEqual(result.violations.count, 1)
        XCTAssertEqual(result.violations[0]["type"]?.stringValue, "MALFORMED_GEOMETRY")
        XCTAssertEqual(result.warnings, [])
    }

    func testDeterminism() throws {
        let scene = Parity.scene(named: "fixture:valid_packed_scene")
        let pv = try PlacementValidator(container: scene.container)
        let ordered = try bottomUpOrder(scene.objects)
        for obj in ordered.dropLast() { _ = pv.place(obj) }
        let last = ordered.last!
        let r1 = pv.tryPlace(last), r2 = pv.tryPlace(last)
        XCTAssertEqual(r1, r2)
        XCTAssertEqual(try resultToJSON(r1), try resultToJSON(r2))
    }

    // MARK: - incremental metrics

    func testNoCommittedObjectsGapIsNull() throws {
        let pv = try PlacementValidator(container: cube1m())
        let metrics = try pv.incrementalMetrics(
            SceneObject(id: "a", dimensions: Vec3(0.2, 0.2, 0.2), position: Vec3(0, 0, 0)))
        XCTAssertEqual(metrics["nearest_neighbor_gap_m"], .null)
        XCTAssertGreaterThan(metrics["wall_clearance_m"]!.doubleValue!, 0.0)
    }

    func testOverlappingGapIsZeroAndClearanceShrinksWithDistance() throws {
        let pv = try PlacementValidator(container: cube1m())
        // floating at the scene center; fine, only AABB gaps are under test.
        _ = pv.place(SceneObject(id: "a", dimensions: Vec3(0.2, 0.2, 0.2), position: Vec3(0, 0, 0)), force: true)

        let overlapping = SceneObject(id: "b", dimensions: Vec3(0.2, 0.2, 0.2), position: Vec3(0.05, 0, 0))
        XCTAssertEqual(try pv.incrementalMetrics(overlapping)["nearest_neighbor_gap_m"]?.doubleValue, 0.0)

        let far = SceneObject(id: "c2", dimensions: Vec3(0.2, 0.2, 0.2), position: Vec3(0.3, 0, 0))
        let gap = try pv.incrementalMetrics(far)["nearest_neighbor_gap_m"]!.doubleValue!
        XCTAssertEqual(gap, 0.1, accuracy: 1e-6)
    }

    // MARK: - timing

    /// 20 committed boxes, 1000 `tryPlace` calls -- prints µs/call.
    func testTryPlaceTiming() throws {
        let container = Container(id: "box", dimensions: Vec3(2, 2, 2), position: Vec3(0, 0, 0))
        let pv = try PlacementValidator(container: container)
        for k in 0..<20 {
            let (col, row) = (k % 5, k / 5)
            _ = pv.place(SceneObject(
                id: "b\(k)", dimensions: Vec3(0.15, 0.15, 0.15),
                position: Vec3(-0.5 + 0.25 * Double(col), -1.0 + 0.075, -0.4 + 0.25 * Double(row))),
                force: true)
        }
        XCTAssertEqual(pv.placedIds.count, 20)
        let candidate = SceneObject(id: "cand", dimensions: Vec3(0.15, 0.15, 0.15),
                                    position: Vec3(0.7, -1.0 + 0.075, 0.6))
        XCTAssertTrue(pv.tryPlace(candidate).valid)  // warm up + sanity

        let iterations = 1000
        let t0 = Date().timeIntervalSince1970
        for _ in 0..<iterations { _ = pv.tryPlace(candidate) }
        let usPerCall = (Date().timeIntervalSince1970 - t0) * 1_000_000.0 / Double(iterations)
        print("[timing] PlacementValidator.tryPlace at k=20: \(String(format: "%.1f", usPerCall)) us/call")
        XCTAssertLessThan(usPerCall, 5000.0)
    }
}
