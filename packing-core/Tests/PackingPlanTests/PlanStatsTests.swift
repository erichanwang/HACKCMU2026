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
        size: Vector3,
        nestedIn: Nesting? = nil
    ) -> Placement {
        Placement(
            step: step,
            itemID: id,
            label: id,
            zone: "interior",
            position: position,
            size: size,
            rotation: .xyz,
            note: "",
            nestedIn: nestedIn
        )
    }

    private func nesting(
        in host: String,
        cavity position: Vector3,
        size: Vector3
    ) -> Nesting {
        Nesting(itemID: host, cavity: Nesting.Cavity(position: position, size: size))
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

    /// Nothing nests in the bundled plan, so the two agree — and the figure every
    /// `#Preview` renders is pinned: its six boxes total 0.019124 m³ in a
    /// 0.4064 × 0.1524 × 0.6096 = 0.0377558 m³ interior.
    func testFillFractionMatchesPlanPackedVolumeFraction() throws {
        let plan = try PlanLoader.mockPlan()
        XCTAssertEqual(PlanStats(plan: plan).fillFraction, plan.packedVolumeFraction, accuracy: accuracy)
        XCTAssertEqual(PlanStats(plan: plan).packedVolume, 0.019124, accuracy: 1e-7)
        XCTAssertEqual(PlanStats(plan: plan).fillFraction, 0.5065182, accuracy: accuracy)
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

    // MARK: - Fill with nesting
    //
    // A nested item's box lies inside its host's, so summing both counts the same
    // cubic metres twice. Every figure below is the box arithmetic done by hand
    // from the positions and sizes in the test, not read off the implementation.

    /// Host 0.5³ = 0.125 m³, guest 0.2³ = 0.008 m³ wholly inside it. Summing gives
    /// 0.133; the bag holds 0.125 m³ of stuff.
    func testNestedItemVolumeIsCountedOnce() {
        let nested = plan([
            item(1, "host", position: .zero, size: Vector3(0.5, 0.5, 0.5)),
            item(
                2, "guest",
                position: Vector3(0.1, 0.1, 0.1),
                size: Vector3(0.2, 0.2, 0.2),
                nestedIn: nesting(in: "host", cavity: Vector3(0.05, 0.05, 0.05), size: Vector3(0.3, 0.3, 0.3))
            ),
        ])
        let stats = PlanStats(plan: nested)

        XCTAssertEqual(stats.packedVolume, 0.125, accuracy: accuracy)
        XCTAssertEqual(stats.fillFraction, 0.125, accuracy: accuracy)
        // The plan's own fraction still sums the boxes, so the two now differ by
        // exactly the shared volume. Whoever compares them should know why.
        XCTAssertEqual(nested.packedVolumeFraction, 0.133, accuracy: accuracy)
    }

    /// Only the shared part is discounted. The guest spans y 0.4...0.6 and the host
    /// y 0...0.5, so they share 0.2 × 0.1 × 0.2 = 0.004 m³: 0.133 − 0.004 = 0.129.
    func testOnlyTheVolumeInsideTheHostIsDiscounted() {
        let stats = PlanStats(plan: plan([
            item(1, "host", position: .zero, size: Vector3(0.5, 0.5, 0.5)),
            item(
                2, "guest",
                position: Vector3(0.1, 0.4, 0.1),
                size: Vector3(0.2, 0.2, 0.2),
                nestedIn: nesting(in: "host", cavity: Vector3(0.05, 0.3, 0.05), size: Vector3(0.3, 0.3, 0.3))
            ),
        ]))

        XCTAssertEqual(stats.packedVolume, 0.129, accuracy: accuracy)
    }

    /// Nesting the plan does not honour buys no discount: a `nestedIn` naming an
    /// item that is not in the plan is not nesting, so the full 0.133 stands.
    func testNestingWithAMissingHostDoesNotDiscountVolume() {
        let stats = PlanStats(plan: plan([
            item(1, "host", position: .zero, size: Vector3(0.5, 0.5, 0.5)),
            item(
                2, "guest",
                position: Vector3(0.1, 0.1, 0.1),
                size: Vector3(0.2, 0.2, 0.2),
                nestedIn: nesting(in: "ghost", cavity: Vector3(0.05, 0.05, 0.05), size: Vector3(0.3, 0.3, 0.3))
            ),
        ]))

        XCTAssertEqual(stats.packedVolume, 0.133, accuracy: accuracy)
    }

    /// The bundled bowl-and-cup fixture, worked out from its JSON:
    /// bowl 0.2 × 0.1 × 0.2 = 0.004, cup 0.08 × 0.09 × 0.08 = 0.000576, shared
    /// (x 0.10...0.18, y 0.03...0.10, z 0.10...0.18) = 0.000448, so 0.004128 m³ of
    /// a 0.34 × 0.20 × 0.50 = 0.034 m³ interior — 12.14%, not the 13.46% the sum
    /// of the boxes claims.
    func testNestedFixtureFillCountsTheSharedVolumeOnce() throws {
        let stats = PlanStats(plan: try PlanLoader.plan(resourceNamed: "nested-plan", in: .module))

        XCTAssertEqual(stats.packedVolume, 0.004128, accuracy: 1e-7)
        XCTAssertEqual(stats.fillFraction, 0.1214118, accuracy: accuracy)
        // Bowl footprint 0.2 × 0.2 over a 0.34 × 0.50 floor. The cup stands inside
        // it, covering no floor the bowl was not already over — a union, so this
        // needed no nesting fix and does not change.
        XCTAssertEqual(stats.floorCoverage, 0.2352941, accuracy: accuracy)
    }

    /// The free-block search needed no nesting fix either: the bowl and cup end at
    /// z = 0.25, so the clean 0.34 × 0.20 × 0.25 = 0.017 m³ slab behind them is the
    /// biggest empty box (the next best is the 0.34 × 0.08 × 0.50 = 0.0136 m³ space
    /// above the cup). What does change is the share: 0.034 − 0.004128 = 0.029872 m³
    /// is free, so 0.017 is 56.9% of it, not the 57.8% an over-counted fill implies.
    func testNestedFixtureFreeBlockIsMeasuredAgainstTheTrueFreeSpace() throws {
        let stats = PlanStats(plan: try PlanLoader.plan(resourceNamed: "nested-plan", in: .module))
        let gap = try XCTUnwrap(stats.largestGap)

        XCTAssertEqual(gap.minCorner.z, 0.25, accuracy: accuracy)
        XCTAssertEqual(gap.size.x, 0.34, accuracy: accuracy)
        XCTAssertEqual(gap.size.y, 0.20, accuracy: accuracy)
        XCTAssertEqual(gap.size.z, 0.25, accuracy: accuracy)
        XCTAssertEqual(stats.largestGapVolume, 0.017, accuracy: accuracy)
        XCTAssertEqual(stats.largestGapFractionOfFree, 0.5690948, accuracy: accuracy)
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
