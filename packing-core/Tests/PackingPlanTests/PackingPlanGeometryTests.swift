import XCTest
@testable import PackingPlan

final class PackingPlanGeometryTests: XCTestCase {

    // Tolerance for face contact and float noise: 1 µm. Tight enough that any
    // real (millimetre-scale) overlap still fails.
    private let tolerance: Float = 1e-6

    private func loadMockPlan() throws -> PackingPlan {
        try PlanLoader.mockPlan()
    }

    // MARK: - Loading

    func testMockPlanLoads() throws {
        let plan = try loadMockPlan()

        XCTAssertEqual(plan.units, .meters)
        XCTAssertEqual(plan.placements.count, 6)
        XCTAssertEqual(plan.container.dimensions, Vector3(0.4064, 0.1524, 0.6096))
        XCTAssertEqual(plan.container.zones.count, 3)
    }

    func testMockPlanStepsAreContiguousAndOrdered() throws {
        let plan = try loadMockPlan()
        XCTAssertEqual(plan.orderedPlacements.map(\.step), [1, 2, 3, 4, 5, 6])
        XCTAssertEqual(plan.placement(step: 1)?.itemID, "shoes-pair")
        XCTAssertEqual(plan.placement(step: 6)?.itemID, "laptop-sleeve")
    }

    // MARK: - The two headline invariants

    /// No two placements may share interior volume. Face contact (an item resting
    /// on another) is allowed.
    func testNoTwoPlacementsIntersect() throws {
        let plan = try loadMockPlan()
        let placements = plan.orderedPlacements

        for i in placements.indices {
            for j in placements.index(after: i)..<placements.endIndex {
                let a = placements[i]
                let b = placements[j]
                let overlap = a.box.overlapExtents(with: b.box)

                XCTAssertFalse(
                    a.box.intersects(b.box, tolerance: tolerance),
                    """
                    \(a.itemID) (step \(a.step)) overlaps \(b.itemID) (step \(b.step)).
                      \(a.itemID): \(a.box)
                      \(b.itemID): \(b.box)
                      overlap per axis: \(overlap)
                    """
                )
            }
        }
    }

    /// Every placement must lie entirely within the container interior.
    func testNoPlacementExceedsContainerBounds() throws {
        let plan = try loadMockPlan()
        let interior = plan.container.interior

        for placement in plan.orderedPlacements {
            let box = placement.box

            for axis in Axis.allCases {
                XCTAssertGreaterThanOrEqual(
                    box.minCorner[axis], -tolerance,
                    "\(placement.itemID) starts before the origin on \(axis.label): \(box)"
                )
                XCTAssertLessThanOrEqual(
                    box.maxCorner[axis], interior.maxCorner[axis] + tolerance,
                    """
                    \(placement.itemID) exceeds the interior on \(axis.label): \
                    max \(box.maxCorner[axis]) > \(interior.maxCorner[axis])
                      \(placement.itemID): \(box)
                    """
                )
            }

            XCTAssertTrue(
                box.isContained(in: interior, tolerance: tolerance),
                "\(placement.itemID) is out of bounds by \(box.overshoot(outOf: interior))"
            )
        }
    }

    // MARK: - Negative controls
    //
    // Without these, the two tests above would pass even if `intersects` and
    // `isContained` were broken and always returned false/true.

    func testIntersectionIsDetectedForKnownOverlap() {
        let a = BoundingBox(minCorner: Vector3(0, 0, 0), size: Vector3(0.20, 0.10, 0.20))
        let b = BoundingBox(minCorner: Vector3(0.10, 0.05, 0.10), size: Vector3(0.20, 0.10, 0.20))

        XCTAssertTrue(a.intersects(b, tolerance: tolerance))
        XCTAssertTrue(b.intersects(a, tolerance: tolerance))
        XCTAssertEqual(a.overlapExtents(with: b), Vector3(0.10, 0.05, 0.10))
    }

    func testTouchingFacesDoNotIntersect() {
        // b sits exactly on top of a — the shoes/shirts case in the mock plan.
        let a = BoundingBox(minCorner: Vector3(0, 0, 0), size: Vector3(0.20, 0.10, 0.20))
        let b = BoundingBox(minCorner: Vector3(0, 0.10, 0), size: Vector3(0.20, 0.10, 0.20))

        XCTAssertFalse(a.intersects(b, tolerance: tolerance))
        XCTAssertEqual(a.overlapExtents(with: b)[.y], 0, accuracy: tolerance)
    }

    func testOutOfBoundsIsDetected() {
        let interior = BoundingBox(minCorner: .zero, size: Vector3(0.4064, 0.1524, 0.6096))
        let pokesOut = BoundingBox(minCorner: Vector3(0.35, 0, 0), size: Vector3(0.10, 0.10, 0.10))

        XCTAssertFalse(pokesOut.isContained(in: interior, tolerance: tolerance))
        XCTAssertEqual(pokesOut.overshoot(outOf: interior)[.x], 0.0436, accuracy: 1e-5)
    }

    // MARK: - Validator agrees with the hand-rolled checks

    func testMockPlanHasNoGeometryIssues() throws {
        let plan = try loadMockPlan()
        let issues = plan.geometryIssues(tolerance: tolerance)

        XCTAssertTrue(
            issues.isEmpty,
            "Expected a clean plan, found:\n" + issues.map { "• \($0)" }.joined(separator: "\n")
        )
        XCTAssertNoThrow(try plan.validateGeometry(tolerance: tolerance))
    }

    func testValidatorCatchesAnInjectedOverlap() throws {
        let plan = try loadMockPlan()

        // Drop the laptop sleeve straight through the sweater below it.
        var placements = plan.orderedPlacements
        let laptop = placements[5]
        placements[5] = Placement(
            step: laptop.step,
            itemID: laptop.itemID,
            label: laptop.label,
            zone: laptop.zone,
            position: Vector3(laptop.position.x, 0.05, laptop.position.z),
            size: laptop.size,
            rotation: laptop.rotation,
            note: laptop.note
        )
        let broken = PackingPlan(
            version: plan.version,
            units: plan.units,
            container: plan.container,
            placements: placements
        )

        let issues = broken.geometryIssues(tolerance: tolerance)
        XCTAssertTrue(
            issues.contains { issue in
                if case let .intersection(a, b, _) = issue {
                    return [a, b].contains("laptop-sleeve") && [a, b].contains("sweater-roll")
                }
                return false
            },
            "Expected a laptop/sweater intersection, found: \(issues)"
        )
        XCTAssertThrowsError(try broken.validateGeometry(tolerance: tolerance))
    }

    // MARK: - Min-corner vs center

    /// The offset that is easiest to get wrong, pinned down.
    func testRenderCenterIsMinCornerPlusHalfSize() throws {
        let plan = try loadMockPlan()

        for placement in plan.placements {
            let expected = placement.position + placement.size / 2
            XCTAssertEqual(placement.renderCenter.x, expected.x, accuracy: 1e-6)
            XCTAssertEqual(placement.renderCenter.y, expected.y, accuracy: 1e-6)
            XCTAssertEqual(placement.renderCenter.z, expected.z, accuracy: 1e-6)
            XCTAssertNotEqual(
                placement.renderCenter, placement.position,
                "\(placement.itemID) has a non-zero size, so its center must differ from its min corner"
            )
        }
    }

    func testFirstItemSitsOnTheFloorAtTheOrigin() throws {
        let plan = try loadMockPlan()
        let shoes = try XCTUnwrap(plan.placement(step: 1))

        XCTAssertEqual(shoes.position, .zero)
        // Center is lifted by half the item's height — not sunk into the floor.
        XCTAssertEqual(shoes.renderCenter.y, shoes.size.y / 2, accuracy: 1e-6)
    }

    // MARK: - Zones and rotation

    func testEveryPlacementFitsItsDeclaredZone() throws {
        let plan = try loadMockPlan()

        for placement in plan.orderedPlacements {
            let zone = try XCTUnwrap(
                plan.container.zone(id: placement.zone),
                "\(placement.itemID) names unknown zone '\(placement.zone)'"
            )
            XCTAssertTrue(
                placement.box.isContained(in: zone.box, tolerance: tolerance),
                "\(placement.itemID) leaves zone '\(zone.id)' by \(placement.box.overshoot(outOf: zone.box))"
            )
        }
    }

    func testAxisRotationRoundTripsAndPermutes() throws {
        XCTAssertEqual(AxisRotation.allCases.count, 6)

        for rotation in AxisRotation.allCases {
            let data = try JSONEncoder().encode(rotation)
            XCTAssertEqual(try JSONDecoder().decode(AxisRotation.self, from: data), rotation)

            // A permutation maps each bag axis to a distinct local axis.
            let mapped = Axis.allCases.map { rotation.localAxis(forBagAxis: $0) }
            XCTAssertEqual(Set(mapped).count, 3, "\(rotation.rawValue) is not a permutation")

            // localAxis and bagAxis are inverses.
            for axis in Axis.allCases {
                XCTAssertEqual(rotation.bagAxis(forLocalAxis: rotation.localAxis(forBagAxis: axis)), axis)
            }
        }

        XCTAssertEqual(AxisRotation.xyz.bagExtent(ofLocalSize: Vector3(1, 2, 3)), Vector3(1, 2, 3))
        // bag X takes local Z, bag Y takes local X, bag Z takes local Y.
        XCTAssertEqual(AxisRotation.zxy.bagExtent(ofLocalSize: Vector3(1, 2, 3)), Vector3(3, 1, 2))
        XCTAssertEqual(AxisRotation.zyx.bagExtent(ofLocalSize: Vector3(1, 2, 3)), Vector3(3, 2, 1))
    }

    func testMalformedJSONIsRejected() {
        let missingUnits = Data(#"{"version":1,"container":{},"placements":[]}"#.utf8)
        XCTAssertThrowsError(try PlanLoader.plan(from: missingUnits))

        let badRotation = Data(#"{"units":"meters"}"#.utf8)
        XCTAssertThrowsError(try PlanLoader.plan(from: badRotation))
    }
}
