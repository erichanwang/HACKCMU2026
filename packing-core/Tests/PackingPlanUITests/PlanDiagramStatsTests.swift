import XCTest
@testable import PackingPlanUI
@testable import PackingPlan

/// What the 2D diagram now claims about a plan, checked without SwiftUI: the
/// layer captions index `PlanStats.Layer` by `PlanLayer.index`, and the unstable
/// preview fixture really does trip stability and only stability.
final class PlanDiagramStatsTests: XCTestCase {

    private let tolerance: Float = 1e-6

    // MARK: - The caption's indexing assumption

    /// `PlanDiagramView` draws from `plan.layers()` and captions from
    /// `PlanStats.layers`. Two groupings, same rule — if they ever diverge, the
    /// caption describes a different layer than the diagram.
    func testLayerStatsLineUpWithDiagramLayers() throws {
        for plan in [try PlanLoader.mockPlan(), unstableDemoPlan()] {
            let drawn = plan.layers()
            let described = PlanStats(plan: plan).layers

            XCTAssertEqual(drawn.map(\.index), described.map(\.index))
            XCTAssertEqual(
                drawn.map { $0.placements.map(\.itemID) },
                described.map { $0.placements.map(\.itemID) }
            )
            for (a, b) in zip(drawn, described) {
                XCTAssertEqual(a.floorY, b.floorY, accuracy: 1e-6)
                XCTAssertEqual(a.thickness, b.thickness, accuracy: 1e-6)
            }
        }
    }

    // MARK: - The preview fixture

    /// The fixture exists to make the stability banner appear while the geometry
    /// banner stays away — that separation is the whole point of showing them as
    /// two different things, so it is asserted rather than eyeballed.
    func testUnstableFixtureTripsStabilityOnly() {
        let plan = unstableDemoPlan()

        let geometry = plan.geometryIssues(tolerance: tolerance)
        XCTAssertTrue(geometry.isEmpty, "\(geometry)")

        let stability = plan.stabilityIssues(tolerance: tolerance)
        XCTAssertEqual(stability.count, 2, "\(stability)")

        let floating = stability.compactMap { issue -> String? in
            guard case let .floating(id, gap) = issue else { return nil }
            XCTAssertEqual(gap, 0.05, accuracy: 1e-6, "the whole way down to the bag floor")
            return id
        }
        let overhanging = stability.compactMap { issue -> String? in
            guard case let .unsupportedOverhang(id, _) = issue else { return nil }
            return id
        }
        XCTAssertEqual(floating, ["toiletry-kit"])
        XCTAssertEqual(overhanging, ["laptop-sleeve"])
    }

    /// A structurally valid plan, so the fixture could be written out as JSON and
    /// loaded like any other plan.
    func testUnstableFixtureIsStructurallyValid() throws {
        let plan = unstableDemoPlan()
        let data = try JSONEncoder().encode(plan)

        XCTAssertEqual(try PlanLoader.plan(from: data), plan)
    }
}
