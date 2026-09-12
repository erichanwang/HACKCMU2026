import XCTest
@testable import PackingPlan

/// `stabilityIssues()` — the checks that catch a plan the solver considers legal
/// but physics does not. These are separate from `geometryIssues()` on purpose,
/// so every test here also asserts the hard-error list stays empty: a floating
/// item is not an overlap.
final class PlanGeometryStabilityTests: XCTestCase {

    private let tolerance: Float = 1e-6

    // A 0.34 × 0.20 × 0.50 m interior — the container the live server emits.
    private static let interior = Vector3(0.34, 0.20, 0.50)

    private func plan(_ placements: [Placement], height: Float = interior.y) -> PackingPlan {
        PackingPlan(
            version: 1,
            units: .meters,
            container: Container(
                id: "c",
                label: "Test",
                dimensions: Vector3(Self.interior.x, height, Self.interior.z),
                zones: [
                    Zone(
                        id: "interior",
                        label: "Interior",
                        origin: .zero,
                        size: Vector3(Self.interior.x, height, Self.interior.z)
                    )
                ]
            ),
            placements: placements
        )
    }

    private func item(
        _ id: String,
        step: Int,
        at position: Vector3,
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

    // MARK: - Floating

    func testItemOnTheFloorIsNotFloating() {
        let p = plan([item("box", step: 1, at: .zero, size: Vector3(0.1, 0.1, 0.1))])
        XCTAssertTrue(p.stabilityIssues(tolerance: tolerance).isEmpty)
    }

    func testItemRestingExactlyOnAnotherIsNotFloating() {
        let p = plan([
            item("base", step: 1, at: .zero, size: Vector3(0.2, 0.05, 0.2)),
            item("top", step: 2, at: Vector3(0, 0.05, 0), size: Vector3(0.2, 0.04, 0.2)),
        ])
        XCTAssertTrue(p.geometryIssues(tolerance: tolerance).isEmpty)
        XCTAssertTrue(p.stabilityIssues(tolerance: tolerance).isEmpty)
    }

    /// The failure the mock plan actually contains: a "second layer" sitting at
    /// the zone's floor height rather than on the item below it.
    func testItemHangingAboveTheLayerBelowIsFloating() {
        let p = plan([
            item("base", step: 1, at: .zero, size: Vector3(0.2, 0.05, 0.2)),
            item("top", step: 2, at: Vector3(0, 0.075, 0), size: Vector3(0.2, 0.045, 0.2)),
        ])
        // Nothing overlaps; the hard checks see a perfectly good plan.
        XCTAssertTrue(p.geometryIssues(tolerance: tolerance).isEmpty)

        let issues = p.stabilityIssues(tolerance: tolerance)
        XCTAssertEqual(issues.count, 1)
        guard case let .floating(itemID, gap) = issues[0] else {
            return XCTFail("expected .floating, got \(issues)")
        }
        XCTAssertEqual(itemID, "top")
        XCTAssertEqual(gap, 0.025, accuracy: 1e-5)
    }

    /// A box under the item but not under its footprint holds nothing up.
    func testSupportMustBeUnderTheFootprint() {
        let p = plan([
            item("aside", step: 1, at: Vector3(0.25, 0, 0), size: Vector3(0.05, 0.05, 0.05)),
            item("top", step: 2, at: Vector3(0, 0.05, 0), size: Vector3(0.2, 0.05, 0.2)),
        ])
        let issues = p.stabilityIssues(tolerance: tolerance)
        XCTAssertEqual(issues.count, 1)
        guard case let .floating(itemID, gap) = issues[0] else {
            return XCTFail("expected .floating, got \(issues)")
        }
        XCTAssertEqual(itemID, "top")
        XCTAssertEqual(gap, 0.05, accuracy: 1e-5)
    }

    // MARK: - Unsupported overhang

    /// A quarter of the footprint hanging off is fine — the centre of mass is
    /// still over the support.
    func testSmallOverhangIsAccepted() {
        let p = plan([
            item("base", step: 1, at: .zero, size: Vector3(0.2, 0.05, 0.2)),
            item("top", step: 2, at: Vector3(0.05, 0.05, 0), size: Vector3(0.2, 0.04, 0.2)),
        ])
        XCTAssertTrue(p.stabilityIssues(tolerance: tolerance).isEmpty)
    }

    /// Two thirds off the edge: the centre of mass clears the support and it tips.
    func testCentreOfMassPastTheSupportIsReported() {
        let p = plan([
            item("base", step: 1, at: .zero, size: Vector3(0.2, 0.05, 0.2)),
            item("top", step: 2, at: Vector3(0.14, 0.05, 0), size: Vector3(0.18, 0.05, 0.2)),
        ])
        XCTAssertTrue(p.geometryIssues(tolerance: tolerance).isEmpty)

        let issues = p.stabilityIssues(tolerance: tolerance)
        XCTAssertEqual(issues.count, 1)
        guard case let .unsupportedOverhang(itemID, fraction) = issues[0] else {
            return XCTFail("expected .unsupportedOverhang, got \(issues)")
        }
        XCTAssertEqual(itemID, "top")
        // 0.06 m of 0.18 m of width is over the base.
        XCTAssertEqual(fraction, 1.0 / 3.0, accuracy: 1e-4)
    }

    /// A bridge: both ends supported, nothing under the middle where the centre of
    /// mass is.
    func testBridgingTwoSupportsWithNothingUnderTheMiddleIsReported() {
        let p = plan([
            item("left", step: 1, at: .zero, size: Vector3(0.1, 0.15, 0.2)),
            item("right", step: 2, at: Vector3(0.2, 0, 0), size: Vector3(0.1, 0.15, 0.2)),
            item("top", step: 3, at: Vector3(0, 0.15, 0), size: Vector3(0.3, 0.02, 0.2)),
        ])
        XCTAssertTrue(p.geometryIssues(tolerance: tolerance).isEmpty)
        // The centre of mass at x = 0.15 is over neither support: a bridge, not a
        // stack. Reported, and correctly so — nothing is under the middle.
        let issues = p.stabilityIssues(tolerance: tolerance)
        XCTAssertEqual(issues.count, 1)
        guard case let .unsupportedOverhang(_, fraction) = issues[0] else {
            return XCTFail("expected .unsupportedOverhang, got \(issues)")
        }
        // Both contact rectangles count toward the supported share.
        XCTAssertEqual(fraction, 2.0 / 3.0, accuracy: 1e-4)
    }

    // MARK: - Stack load

    func testColumnAboveIsMeasuredInVolume() {
        // 2 L under 6 L: three times the item's own volume stacked over it.
        let p = plan([
            item("under", step: 1, at: .zero, size: Vector3(0.2, 0.05, 0.2)),
            item("over", step: 2, at: Vector3(0, 0.05, 0), size: Vector3(0.2, 0.15, 0.2)),
        ])
        let loads = p.stabilityIssues(tolerance: tolerance).compactMap { issue -> (String, Float)? in
            guard case let .overloadedStack(id, volume) = issue else { return nil }
            return (id, volume)
        }
        XCTAssertEqual(loads.count, 1)
        XCTAssertEqual(loads.first?.0, "under")
        XCTAssertEqual(loads.first?.1 ?? 0, 0.006, accuracy: 1e-6)
    }

    /// Twice the item's own volume above it is the default ceiling; a real
    /// multi-layer plan sits just over 1×, so 1× would warn on every good plan.
    func testALighterStackAboveIsNotReported() {
        let p = plan([
            item("under", step: 1, at: .zero, size: Vector3(0.2, 0.1, 0.2)),
            item("over", step: 2, at: Vector3(0, 0.1, 0), size: Vector3(0.1, 0.05, 0.1)),
        ])
        XCTAssertTrue(p.stabilityIssues(tolerance: tolerance).isEmpty)
    }

    func testMaxLoadRatioTunesTheThreshold() {
        let p = plan([
            item("under", step: 1, at: .zero, size: Vector3(0.2, 0.1, 0.2)),
            item("over", step: 2, at: Vector3(0, 0.1, 0), size: Vector3(0.1, 0.05, 0.1)),
        ])
        // Load is 1/8 of the item's own volume; demand better than 1/10.
        XCTAssertEqual(p.stabilityIssues(tolerance: tolerance, maxLoadRatio: 0.1).count, 1)
    }

    // MARK: - Headroom

    func testStackTallerThanTheInteriorIsReported() {
        let p = plan(
            [
                item("base", step: 1, at: .zero, size: Vector3(0.1, 0.08, 0.1)),
                item("top", step: 2, at: Vector3(0, 0.08, 0), size: Vector3(0.1, 0.05, 0.1)),
            ],
            height: 0.12
        )
        let headroom = p.stabilityIssues(tolerance: tolerance).compactMap { issue -> Float? in
            guard case let .exceedsHeadroom(id, overshoot) = issue, id == "top" else { return nil }
            return overshoot
        }
        XCTAssertEqual(headroom.count, 1)
        XCTAssertEqual(headroom[0], 0.01, accuracy: 1e-5)
    }

    // MARK: - Real plans

    /// The bundled mock plan is what every `#Preview`, `PlanLoader.mockPlan()`
    /// caller and both diagram views render, so it has to be a picture of the
    /// product working: geometrically clean *and* physically possible. Every item
    /// rests on the bag floor or on the item beneath it — nothing floats, nothing
    /// tips, nothing is buried under a mountain, and the lid closes.
    func testMockPlanIsCleanAndStable() throws {
        let plan = try PlanLoader.mockPlan()

        let geometry = plan.geometryIssues(tolerance: tolerance)
        XCTAssertTrue(geometry.isEmpty, "\(geometry)")

        let stability = plan.stabilityIssues(tolerance: tolerance)
        XCTAssertTrue(stability.isEmpty, "\(stability)")
    }

    /// Why `maxLoadRatio` defaults to 2.0 and not 1.0. The mock plan's heaviest
    /// column is 1.18x the volume of the item under it and a real three-layer
    /// solver plan runs 1.10x, so at 1.0 every honest multi-layer plan would show
    /// a warning banner — which is how users learn to ignore banners.
    func testLoadRatioOfOneWouldWarnOnTheMockPlan() throws {
        let plan = try PlanLoader.mockPlan()
        XCTAssertTrue(plan.stabilityIssues(tolerance: tolerance, maxLoadRatio: 2.0).isEmpty)

        let overloaded = plan.stabilityIssues(tolerance: tolerance, maxLoadRatio: 1.0)
            .compactMap { issue -> String? in
                guard case let .overloadedStack(id, _) = issue else { return nil }
                return id
            }
        XCTAssertEqual(Set(overloaded), ["jeans-folded", "shirt-stack"])
    }
}
