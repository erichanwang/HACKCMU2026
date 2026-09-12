import Foundation
import XCTest
@testable import PackPhysics

final class Packer3DTests: XCTestCase {

    // MARK: - Fixture loading

    private func resourceData(_ name: String) throws -> Data {
        let url = Bundle.module.url(forResource: name, withExtension: "json", subdirectory: "Resources")!
        return try Data(contentsOf: url)
    }

    private func suitcaseResultData() throws -> Data { try resourceData("packer3d_suitcase_result") }
    private func dragonResultData() throws -> Data { try resourceData("packer3d_dragon_result") }
    private func suitcaseScenarioData() throws -> Data { try resourceData("packer3d_suitcase_scenario") }

    /// Count of `type` entries in a violations/warnings array.
    private func typeCounts(_ entries: [JSONValue]) -> [String: Int] {
        var counts: [String: Int] = [:]
        for e in entries {
            let t = e["type"]?.stringValue ?? "?"
            counts[t, default: 0] += 1
        }
        return counts
    }

    // MARK: - Decoding both strategies + counts

    func testDecodeSuitcaseNaiveCounts() throws {
        let result = try packer3dResult(from: try suitcaseResultData(), strategy: "naive")
        XCTAssertEqual(result.strategy, "naive")
        XCTAssertEqual(result.placements.count, 12)
        XCTAssertEqual(result.unpacked.count, 6)
        XCTAssertEqual(result.metrics["items_packed"]?.doubleValue, 12)
        XCTAssertEqual(result.metrics["items_unpacked"]?.doubleValue, 6)
    }

    func testDecodeSuitcaseOptimizedCounts() throws {
        let result = try packer3dResult(from: try suitcaseResultData(), strategy: "optimized")
        XCTAssertEqual(result.strategy, "optimized")
        XCTAssertEqual(result.placements.count, 13)
        XCTAssertEqual(result.unpacked.count, 5)
        XCTAssertEqual(result.metrics["items_packed"]?.doubleValue, 13)
        XCTAssertEqual(result.metrics["items_unpacked"]?.doubleValue, 5)
    }

    func testDecodeDragonBothStrategies() throws {
        let data = try dragonResultData()
        let naive = try packer3dResult(from: data, strategy: "naive")
        let optimized = try packer3dResult(from: data, strategy: "optimized")
        XCTAssertEqual(naive.placements.count, 22)
        XCTAssertEqual(naive.unpacked.count, 0)
        XCTAssertEqual(optimized.placements.count, 22)
        XCTAssertEqual(optimized.unpacked.count, 0)
        XCTAssertEqual(naive.container.obstacles.count, 1)
        XCTAssertEqual(naive.container.obstacles[0].id, "hatch")
    }

    func testDecodeMissingStrategyThrows() throws {
        // The compare wrapper only has naive/optimized -- anything else is a clear error.
        XCTAssertThrowsError(try packer3dResult(from: try suitcaseResultData(), strategy: "bogus"))
    }

    func testDecodeBareResultIgnoresStrategyArgument() throws {
        // A bare (non-compare) result is unambiguous -- decode it regardless of `strategy`.
        let bare = try packer3dResult(from: try suitcaseResultData(), strategy: "optimized")
        let data = try JSONEncoder().encode(bare)
        let roundTripped = try packer3dResult(from: data, strategy: "naive")
        XCTAssertEqual(roundTripped.strategy, "optimized")
        XCTAssertEqual(roundTripped.placements.count, bare.placements.count)
    }

    // MARK: - Frame mapping round trip (one box, both directions)

    func testMappingRoundTripShoes1() throws {
        let result = try packer3dResult(from: try suitcaseResultData(), strategy: "naive")
        let shoes1 = result.placements.first { $0.itemId == "shoes_1" }!
        XCTAssertEqual(shoes1.position, Vec3(0, 0, 0))
        XCTAssertEqual(shoes1.dims, Vec3(0.32, 0.2, 0.12))
        XCTAssertEqual(shoes1.center, Vec3(0.16, 0.1, 0.06))

        let obj = sceneObject(fromPlacement: shoes1)
        // physicsPoint(x,y,z) = (x,z,-y)
        XCTAssertEqual(obj.position, Vec3(0.16, 0.06, -0.1))
        XCTAssertEqual(obj.dimensions, Vec3(0.32, 0.12, 0.2))

        // Inverse: physics center/dims back to packer3d center and min corner.
        let recoveredPackerCenter = packer3dPoint(obj.position)
        let recoveredPackerDims = swapYZ(obj.dimensions)
        let recoveredPackerMinCorner = recoveredPackerCenter - recoveredPackerDims / 2.0
        XCTAssertEqual(recoveredPackerCenter.x, shoes1.center.x, accuracy: 1e-12)
        XCTAssertEqual(recoveredPackerCenter.y, shoes1.center.y, accuracy: 1e-12)
        XCTAssertEqual(recoveredPackerCenter.z, shoes1.center.z, accuracy: 1e-12)
        XCTAssertEqual(recoveredPackerDims.x, shoes1.dims.x, accuracy: 1e-12)
        XCTAssertEqual(recoveredPackerDims.y, shoes1.dims.y, accuracy: 1e-12)
        XCTAssertEqual(recoveredPackerDims.z, shoes1.dims.z, accuracy: 1e-12)
        XCTAssertEqual(recoveredPackerMinCorner.x, shoes1.position.x, accuracy: 1e-12)
        XCTAssertEqual(recoveredPackerMinCorner.y, shoes1.position.y, accuracy: 1e-12)
        XCTAssertEqual(recoveredPackerMinCorner.z, shoes1.position.z, accuracy: 1e-12)
    }

    func testContainerMapping() throws {
        // suitcase container: dims [0.75, 0.50, 0.28] (L, W, H).
        let result = try packer3dResult(from: try suitcaseResultData(), strategy: "naive")
        let c = container(fromPacker3D: result.container)
        XCTAssertEqual(c.dimensions, Vec3(0.75, 0.28, 0.50), "dims (L,W,H) -> (L,H,W)")
        XCTAssertEqual(c.position, Vec3(0.375, 0.14, -0.25), "center (L/2,W/2,H/2) -> (L/2,H/2,-W/2)")
    }

    // MARK: - Containment + validator verdicts on both suitcase strategies

    func testSuitcaseNaiveContainmentAndValidation() throws {
        try checkSuitcaseStrategy(strategy: "naive")
    }

    func testSuitcaseOptimizedContainmentAndValidation() throws {
        try checkSuitcaseStrategy(strategy: "optimized")
    }

    private func checkSuitcaseStrategy(strategy: String) throws {
        let result = try packer3dResult(from: try suitcaseResultData(), strategy: strategy)
        let built = scene(fromPacker3D: result)
        let containment = try checkSceneContainment(built.scene)
        XCTAssertTrue(containment.isEmpty, "\(strategy): every solver placement should be inside the mapped container, got \(containment)")

        let validation = validateLayout(built.scene)
        let violationCounts = typeCounts(validation.violations)
        let warningCounts = typeCounts(validation.warnings)
        print("packer3d suitcase \(strategy): violations=\(violationCounts) warnings=\(warningCounts)")

        // UNSTABLE_STACK-class warnings are expected (the solver optimizes for
        // volume/CoM, not our stricter static-stability margin) and fine.
        XCTAssertNil(violationCounts["OBJECT_COLLISION"], "unexpected OBJECT_COLLISION in \(strategy): \(validation.violations)")
        XCTAssertNil(violationCounts["CONTAINER_PENETRATION"], "unexpected CONTAINER_PENETRATION in \(strategy): \(validation.violations)")
    }

    // MARK: - Dragon: obstacles + fake collision with the hatch

    func testDragonObstaclesAppearInScene() throws {
        let result = try packer3dResult(from: try dragonResultData(), strategy: "optimized")
        let built = scene(fromPacker3D: result)
        let obstacleObjects = built.scene.objects.filter { $0.id.hasPrefix("obstacle:") }
        XCTAssertEqual(obstacleObjects.count, 1)
        XCTAssertEqual(obstacleObjects[0].id, "obstacle:hatch")
        XCTAssertEqual(obstacleObjects[0].massKg, 0)

        // hatch: position (min corner) [1.35,1.35,0.95], dims [0.5,0.5,0.2] ->
        // packer center (1.6,1.6,1.05) -> physics (1.6, 1.05, -1.6).
        XCTAssertEqual(obstacleObjects[0].position.x, 1.6, accuracy: 1e-9)
        XCTAssertEqual(obstacleObjects[0].position.y, 1.05, accuracy: 1e-9)
        XCTAssertEqual(obstacleObjects[0].position.z, -1.6, accuracy: 1e-9)
        XCTAssertEqual(obstacleObjects[0].dimensions, Vec3(0.5, 0.2, 0.5))
    }

    func testFakePlacementOverlappingHatchObstacleCollides() throws {
        let result = try packer3dResult(from: try dragonResultData(), strategy: "optimized")
        var built = scene(fromPacker3D: result)
        // Sits well inside the hatch obstacle's mapped box (center (1.6,1.05,-1.6), half-extents (0.25,0.1,0.25)).
        let intruder = SceneObject(id: "fake_intruder", dimensions: Vec3(0.1, 0.05, 0.1), position: Vec3(1.6, 1.05, -1.6), massKg: 1.0)
        built.scene.objects.append(intruder)

        let validation = validateLayout(built.scene)
        let collisions = validation.violations.filter { $0["type"]?.stringValue == "OBJECT_COLLISION" }
        XCTAssertFalse(collisions.isEmpty, "expected an OBJECT_COLLISION with the hatch obstacle")
        let namesHatch = collisions.contains { entry in
            (entry["objects"]?.arrayValue ?? []).contains { $0.stringValue == "obstacle:hatch" }
        }
        XCTAssertTrue(namesHatch, "collision should name obstacle:hatch, got \(collisions)")
    }

    // MARK: - sceneMetrics on a mass-0-only (obstacles-only) scene

    func testSceneMetricsOnObstaclesOnlySceneIsFiniteAndEncodable() throws {
        let result = try packer3dResult(from: try dragonResultData(), strategy: "optimized")
        let built = scene(fromPacker3D: result, includeObstacles: true)
        // Isolate the obstacle-only scene: container + the mass-0 hatch object, no placements.
        let obstaclesOnly = Scene(container: built.scene.container, objects: built.scene.objects.filter { $0.id.hasPrefix("obstacle:") })
        XCTAssertFalse(obstaclesOnly.objects.isEmpty)
        XCTAssertTrue(obstaclesOnly.objects.allSatisfy { $0.massKg == 0 })

        // Finding: Metrics.swift's `sceneMetrics` already guards total-mass-0
        // scenes (falls back to the unweighted centroid, see its comment above
        // `centerOfMass`), so this is finite without any adapter-side workaround
        // -- no NaN/inf, no Metrics.swift edit needed.
        let metrics = sceneMetrics(try precompute(obstaclesOnly))
        XCTAssertEqual(metrics["total_mass_kg"]?.doubleValue, 0)
        for component in metrics["center_of_mass"]!.arrayValue! {
            XCTAssertTrue(component.doubleValue!.isFinite)
        }
        for component in metrics["com_offset_m"]!.arrayValue! {
            XCTAssertTrue(component.doubleValue!.isFinite)
        }
        XCTAssertNoThrow(try JSONEncoder().encode(metrics))
    }

    // MARK: - Scenario expansion + unpacked ("before") state

    func testScenarioExpansionProducesNumberedIds() throws {
        let scenario = try packer3dScenario(from: try suitcaseScenarioData())
        let shoes = scenario.items.first { $0.id == "shoes" }!
        XCTAssertEqual(shoes.count, 2)
        XCTAssertEqual(expandedIds(shoes), ["shoes_1", "shoes_2"])

        let laptop = scenario.items.first { $0.id == "laptop" }!
        XCTAssertEqual(laptop.count, 1)
        XCTAssertEqual(expandedIds(laptop), ["laptop"], "count == 1 keeps the bare id, no _1 suffix")
    }

    func testUnpackedStateRestsOutsideContainerOnTheFloor() throws {
        let scenario = try packer3dScenario(from: try suitcaseScenarioData())
        let before = unpackedState(fromScenario: scenario)
        let expandedItemCount = scenario.items.reduce(0) { $0 + expandedIds($1).count }
        XCTAssertEqual(before.objects.count, expandedItemCount)

        let L = scenario.container.dims.x
        for obj in before.objects {
            XCTAssertGreaterThan(obj.position.x, L, "\(obj.id) should be laid out past the container's length")
            let floorY = obj.position.y - obj.dimensions.y / 2.0
            XCTAssertEqual(floorY, 0, accuracy: 1e-9, "\(obj.id) should rest on the floor (Y=0)")
        }
        // Specific numbered ids from a "count" item.
        XCTAssertNotNil(before.object(id: "shoes_1"))
        XCTAssertNotNil(before.object(id: "shoes_2"))
    }

    // MARK: - validatePacker3D + resultToJSON round trip

    func testValidatePacker3DEncodesViaResultToJSON() throws {
        let scenario = try packer3dScenario(from: try suitcaseScenarioData())
        let result = try packer3dResult(from: try suitcaseResultData(), strategy: "optimized")
        let validation = validatePacker3D(result, scenario: scenario)
        let data = try resultToJSON(validation)
        let back = try resultFromJSON(data)
        XCTAssertEqual(back, validation)
    }
}
