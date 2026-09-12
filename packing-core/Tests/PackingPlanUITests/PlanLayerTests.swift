import XCTest
@testable import PackingPlanUI
@testable import PackingPlan

final class PlanLayerTests: XCTestCase {

    private func mockPlan() throws -> PackingPlan {
        try PlanLoader.mockPlan()
    }

    private func placement(
        step: Int,
        id: String,
        y: Float,
        height: Float = 0.02
    ) -> Placement {
        Placement(
            step: step,
            itemID: id,
            label: id,
            zone: "base",
            position: Vector3(0, y, Float(step) * 0.05),
            size: Vector3(0.10, height, 0.04),
            rotation: .xyz,
            note: ""
        )
    }

    private func plan(with placements: [Placement]) -> PackingPlan {
        PackingPlan(
            version: 1,
            units: .meters,
            container: Container(
                id: "c",
                label: "c",
                dimensions: Vector3(0.4064, 0.1524, 0.6096),
                zones: []
            ),
            placements: placements
        )
    }

    // MARK: - Grouping

    func testMockPlanGroupsIntoTwoLayers() throws {
        let layers = try mockPlan().layers()

        XCTAssertEqual(layers.count, 2, "The demo bag is two layers, not three")
        XCTAssertEqual(layers.map(\.index), [0, 1])
        XCTAssertEqual(layers[0].floorY, 0.0, accuracy: 1e-6)
        XCTAssertEqual(layers[1].floorY, 0.075, accuracy: 1e-6)
        XCTAssertEqual(
            layers[0].placements.map(\.itemID),
            ["shoes-pair", "toiletry-kit", "jeans-folded", "sweater-roll"]
        )
        XCTAssertEqual(layers[1].placements.map(\.itemID), ["shirt-stack", "laptop-sleeve"])
    }

    func testLayersPartitionEveryPlacementExactlyOnce() throws {
        let plan = try mockPlan()
        let grouped = plan.layers().flatMap(\.placements).map(\.itemID)

        XCTAssertEqual(grouped.count, plan.placements.count)
        XCTAssertEqual(Set(grouped), Set(plan.placements.map(\.itemID)))
    }

    func testLayersAreOrderedBottomFirstAndItemsByStep() throws {
        let layers = try mockPlan().layers()

        XCTAssertEqual(layers.map(\.floorY), layers.map(\.floorY).sorted())
        for layer in layers {
            XCTAssertEqual(layer.placements.map(\.step), layer.placements.map(\.step).sorted())
        }
    }

    func testCeilingAndThicknessComeFromTheTallestItem() throws {
        let layers = try mockPlan().layers()

        // The dopp kit is the tallest thing on the floor: 0.14 m.
        XCTAssertEqual(layers[0].ceilingY, 0.14, accuracy: 1e-6)
        XCTAssertEqual(layers[0].thickness, 0.14, accuracy: 1e-6)
        // Shirts top out at 0.075 + 0.045.
        XCTAssertEqual(layers[1].ceilingY, 0.12, accuracy: 1e-6)
        XCTAssertEqual(layers[1].thickness, 0.045, accuracy: 1e-6)
    }

    func testNearlyEqualFloorsCollapseIntoOneLayer() {
        let plan = plan(with: [
            placement(step: 1, id: "a", y: 0.0),
            placement(step: 2, id: "b", y: 0.002),   // within 5 mm
            placement(step: 3, id: "c", y: 0.0749),  // separate layer
        ])

        let layers = plan.layers()
        XCTAssertEqual(layers.count, 2)
        XCTAssertEqual(layers[0].placements.map(\.itemID), ["a", "b"])
        XCTAssertEqual(layers[1].placements.map(\.itemID), ["c"])
    }

    /// Each item is compared against its group's floor, not its predecessor, so a
    /// staircase of 4 mm steps cannot chain into one 12 mm-thick "layer".
    func testDriftingFloorsDoNotChainIntoOneLayer() {
        let plan = plan(with: [
            placement(step: 1, id: "a", y: 0.000),
            placement(step: 2, id: "b", y: 0.004),
            placement(step: 3, id: "c", y: 0.008),
            placement(step: 4, id: "d", y: 0.012),
        ])

        let layers = plan.layers()
        XCTAssertEqual(layers.count, 2, "0.008 and 0.012 are more than 5 mm above the 0.0 floor")
        XCTAssertEqual(layers[0].placements.map(\.itemID), ["a", "b"])
        XCTAssertEqual(layers[1].placements.map(\.itemID), ["c", "d"])
    }

    func testEmptyPlanHasNoLayers() {
        XCTAssertTrue(plan(with: []).layers().isEmpty)
    }

    // MARK: - Protrusions

    func testTallFloorItemsProtrudeIntoTheUpperLayer() throws {
        let plan = try mockPlan()
        let layers = plan.layers()

        // Nothing is below the floor layer.
        XCTAssertTrue(plan.protrusions(into: layers[0]).isEmpty)

        // Shoes (0.115) and dopp kit (0.14) cross the 0.075 floor; the jeans
        // (0.035) and sweater (0.07) do not.
        XCTAssertEqual(
            plan.protrusions(into: layers[1]).map(\.itemID),
            ["shoes-pair", "toiletry-kit"]
        )
    }

    func testAnItemEndingExactlyAtTheFloorDoesNotProtrude() {
        let plan = plan(with: [
            placement(step: 1, id: "flush", y: 0.0, height: 0.075),
            placement(step: 2, id: "above", y: 0.075, height: 0.02),
        ])
        let layers = plan.layers()

        XCTAssertEqual(layers.count, 2)
        XCTAssertTrue(
            plan.protrusions(into: layers[1]).isEmpty,
            "Touching the floor of the next layer is contact, not protrusion"
        )
    }
}
