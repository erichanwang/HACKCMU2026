import XCTest
@testable import PackingPlan

/// These run on Linux as well as macOS: `PackingPlanUI`'s SwiftUI and CoreGraphics
/// imports are behind `canImport` guards, so `swift test` builds the whole package
/// here. The assertions were first checked against the solver's own `full.json`
/// and `full_over.json` before that was true.
final class PlanStatsTests: XCTestCase {

    private let accuracy: Float = 1e-5

    /// 1 m³ bag so every fraction is also a volume, and the arithmetic is
    /// checkable by eye.
    private func plan(
        dimensions: Vector3 = Vector3(1, 1, 1),
        _ placements: [Placement]
    ) -> PackingPlan {
        PackingPlan(
            version: 1,
            units: .meters,
            container: Container(
                id: "c",
                label: "c",
                dimensions: dimensions,
                zones: [Zone(id: "interior", label: "Interior", origin: .zero, size: dimensions)]
            ),
            placements: placements
        )
    }

    private func item(
        _ step: Int,
        _ id: String,
        position: Vector3,
        size: Vector3
    ) -> Placement {
        Placement(
            step: step,
            itemID: id,
            label: id,
            zone: "interior",
            position: position,
            size: size,
            rotation: .xyz,
            note: ""
        )
    }

    // MARK: - Fill

    func testFillFractionIsPackedBoxVolumeOverInterior() {
        let stats = PlanStats(plan: plan([
            item(1, "a", position: .zero, size: Vector3(0.5, 0.5, 1)),
        ]))

        XCTAssertEqual(stats.interiorVolume, 1, accuracy: accuracy)
        XCTAssertEqual(stats.packedVolume, 0.25, accuracy: accuracy)
        XCTAssertEqual(stats.fillFraction, 0.25, accuracy: accuracy)
    }

    func testFillFractionMatchesPlanPackedVolumeFraction() throws {
        let plan = try PlanLoader.mockPlan()
        XCTAssertEqual(PlanStats(plan: plan).fillFraction, plan.packedVolumeFraction, accuracy: accuracy)
    }

    func testEmptyPlanIsAllOneGap() {
        let stats = PlanStats(plan: plan([]))

        XCTAssertEqual(stats.fillFraction, 0)
        XCTAssertEqual(stats.largestGapVolume, 1, accuracy: accuracy)
        XCTAssertEqual(stats.largestGapFractionOfFree, 1, accuracy: accuracy)
        XCTAssertEqual(stats.floorCoverage, 0)
        XCTAssertTrue(stats.layers.isEmpty)
        XCTAssertNil(stats.firstIn)
    }

    // MARK: - Largest gap

    func testLargestGapIsTheWholeRemainderOfASingleFlatLayer() {
        // A 1 × 0.2 × 1 slab on the floor leaves one clean 1 × 0.8 × 1 block.
        let stats = PlanStats(plan: plan([
            item(1, "a", position: .zero, size: Vector3(1, 0.2, 1)),
        ]))

        XCTAssertEqual(stats.largestGap, BoundingBox(minCorner: Vector3(0, 0.2, 0), size: Vector3(1, 0.8, 1)))
        XCTAssertEqual(stats.largestGapFractionOfFree, 1, accuracy: accuracy)
    }

    func testGapFractionFallsWhenTheSameVolumeIsScattered() {
        // Two pillars in opposite corners split the free space; no single empty
        // box can hold all of it.
        let stats = PlanStats(plan: plan([
            item(1, "a", position: .zero, size: Vector3(0.5, 1, 0.5)),
            item(2, "b", position: Vector3(0.5, 0, 0.5), size: Vector3(0.5, 1, 0.5)),
        ]))

        XCTAssertEqual(stats.fillFraction, 0.5, accuracy: accuracy)
        // Best remaining box is one of the other two quarters: 0.25 of 0.5 free.
        XCTAssertEqual(stats.largestGapVolume, 0.25, accuracy: accuracy)
        XCTAssertEqual(stats.largestGapFractionOfFree, 0.5, accuracy: accuracy)
    }

    func testGapSearchDoesNotTunnelThroughAnItem() throws {
        // A slab across the middle of the bag: no empty box may span it.
        let stats = PlanStats(plan: plan([
            item(1, "a", position: Vector3(0, 0.4, 0), size: Vector3(1, 0.2, 1)),
        ]))

        XCTAssertEqual(stats.largestGapVolume, 0.4, accuracy: accuracy)
        let gap = try XCTUnwrap(stats.largestGap)
        XCTAssertEqual(gap.size.y, 0.4, accuracy: accuracy)
    }

    // MARK: - Coverage

    func testFloorCoverageCountsStackedItemsOnce() {
        let stats = PlanStats(plan: plan([
            item(1, "a", position: .zero, size: Vector3(0.5, 0.5, 1)),
            item(2, "b", position: Vector3(0, 0.5, 0), size: Vector3(0.5, 0.5, 1)),
        ]))

        XCTAssertEqual(stats.floorCoverage, 0.5, accuracy: accuracy)
    }

    // MARK: - Layers

    func testLayersGroupByFloorHeightWithinTolerance() {
        let stats = PlanStats(plan: plan([
            item(1, "floor-a", position: .zero, size: Vector3(0.4, 0.1, 0.4)),
            // 2 mm above the floor: still the same layer (5 mm tolerance).
            item(2, "floor-b", position: Vector3(0.5, 0.002, 0), size: Vector3(0.4, 0.3, 0.4)),
            item(3, "upper", position: Vector3(0, 0.3, 0), size: Vector3(0.4, 0.2, 0.4)),
        ]))

        XCTAssertEqual(stats.layers.map(\.index), [0, 1])
        XCTAssertEqual(stats.layers[0].placements.map(\.itemID), ["floor-a", "floor-b"])
        XCTAssertEqual(stats.layers[0].floorY, 0, accuracy: accuracy)
        // Thickness is the layer's tallest reach, not its first item's.
        XCTAssertEqual(stats.layers[0].thickness, 0.302, accuracy: accuracy)
        XCTAssertEqual(stats.layers[0].floorCoverage, 0.32, accuracy: accuracy)
        XCTAssertEqual(stats.layers[1].placements.map(\.itemID), ["upper"])
        XCTAssertEqual(stats.layers[1].floorY, 0.3, accuracy: accuracy)
        XCTAssertEqual(stats.layers[1].itemCount, 1)
    }

    func testLayerGroupingDoesNotChainUpAStaircase() {
        // Each step is within tolerance of the previous item but not always of
        // the group's floor, so the run breaks instead of chaining into one
        // 8 mm-thick layer.
        let stats = PlanStats(plan: plan([
            item(1, "a", position: .zero, size: Vector3(0.2, 0.1, 0.2)),
            item(2, "b", position: Vector3(0.3, 0.004, 0), size: Vector3(0.2, 0.1, 0.2)),
            item(3, "c", position: Vector3(0.6, 0.008, 0), size: Vector3(0.2, 0.1, 0.2)),
        ]))

        XCTAssertEqual(stats.layers.count, 2)
        XCTAssertEqual(stats.layers[0].placements.map(\.itemID), ["a", "b"])
        XCTAssertEqual(stats.layers[1].placements.map(\.itemID), ["c"])
    }

    // MARK: - Order

    func testOrderReportsFirstLastTopAndLoadBearing() {
        let stats = PlanStats(plan: plan([
            item(1, "base", position: .zero, size: Vector3(0.5, 0.5, 0.5)),
            item(2, "stacked", position: Vector3(0, 0.5, 0), size: Vector3(0.5, 0.2, 0.5)),
            item(3, "tall-loner", position: Vector3(0.6, 0, 0), size: Vector3(0.3, 0.9, 0.3)),
        ]))

        XCTAssertEqual(stats.firstIn?.itemID, "base")
        XCTAssertEqual(stats.lastIn?.itemID, "tall-loner")
        XCTAssertEqual(stats.topmost?.itemID, "tall-loner")
        XCTAssertEqual(stats.loadBearing.map(\.itemID), ["base"])
    }

    func testSideBySideItemsAreNotLoadBearing() {
        // Same heights, no vertical contact: nothing is carrying anything.
        let stats = PlanStats(plan: plan([
            item(1, "a", position: .zero, size: Vector3(0.4, 0.5, 0.4)),
            item(2, "b", position: Vector3(0.5, 0, 0), size: Vector3(0.4, 0.5, 0.4)),
        ]))

        XCTAssertTrue(stats.loadBearing.isEmpty)
    }

    // MARK: - Display

    func testDisplayStringsRoundWithoutRecomputing() {
        let stats = PlanStats(plan: plan([
            item(1, "a", position: .zero, size: Vector3(1, 0.2, 1)),
        ]))

        XCTAssertEqual(stats.fullnessText, "20% full")
        XCTAssertEqual(stats.floorCoverageText, "100% of the floor used")
        XCTAssertEqual(stats.layerCountText, "1 layer")
        XCTAssertEqual(stats.gapText, "Biggest free block 100.0 × 80.0 × 100.0 cm — 100% of the free space")
        XCTAssertEqual(stats.layers[0].summaryText, "Layer 1 · 1 item · 20.0 cm thick · 100% of the floor")
        XCTAssertEqual(stats.orderText, ["First in: a", "Highest in the bag: a"])
    }
}
