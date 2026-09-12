import XCTest
@testable import PackingPlan

final class ScannedContainerTests: XCTestCase {

    /// The document exactly as it came out of Mongo.
    private let document = Data("""
    {
      "id": "5fd83487-65fd-4a88-8d2d-4dbaf6be27ba",
      "name": "Scanned suitcase",
      "dimensions": [0.7718232, 0.16374293, 0.8440979],
      "createdAt": "2026-09-12T06:44:17.634301+00:00"
    }
    """.utf8)

    func testDecodesTheRealDocument() throws {
        let scanned = try ScannedContainerLoader.container(from: document)

        XCTAssertEqual(scanned.id, "5fd83487-65fd-4a88-8d2d-4dbaf6be27ba")
        XCTAssertEqual(scanned.label, "Scanned suitcase")
        XCTAssertEqual(scanned.createdAt, "2026-09-12T06:44:17.634301+00:00")
    }

    /// `[width, height, depth]` maps straight onto x/y/z — the scanner already
    /// uses this app's frame. A remap here would be a bug.
    func testDimensionsMapStraightOntoOurAxes() throws {
        let size = try XCTUnwrap(try ScannedContainerLoader.container(from: document).size)

        XCTAssertEqual(size.x, 0.7718232, accuracy: 1e-6, "width")
        XCTAssertEqual(size.y, 0.16374293, accuracy: 1e-6, "up")
        XCTAssertEqual(size.z, 0.8440979, accuracy: 1e-6, "depth")

        // A suitcase cavity is shallowest; if the height came out as the largest
        // value the field order has been misread.
        XCTAssertLessThan(size.y, size.x)
        XCTAssertLessThan(size.y, size.z)
    }

    func testBundledDocumentMatchesTheOneFromMongo() throws {
        let bundled = try ScannedContainerLoader.bundled()
        let literal = try ScannedContainerLoader.container(from: document)

        XCTAssertEqual(bundled, literal, "the bundled copy has drifted from the real document")
    }

    func testMissingOrNonsenseDimensionsAreRejected() {
        func size(_ dimensions: [Float]) -> Vector3? {
            ScannedContainer(id: "x", name: nil, dimensions: dimensions).size
        }

        XCTAssertNil(size([]))
        XCTAssertNil(size([0.5, 0.2]))
        XCTAssertNil(size([0.5, 0.2, 0.3, 0.4]))
        XCTAssertNil(size([0.5, 0, 0.3]), "a zero axis is not a box")
        XCTAssertNil(size([0.5, -0.2, 0.3]))
        XCTAssertNil(size([0.5, .nan, 0.3]))
        XCTAssertNil(size([0.5, .infinity, 0.3]))
        XCTAssertNotNil(size([0.5, 0.2, 0.3]))
    }

    func testNameFallsBackWhenAbsent() throws {
        let unnamed = try ScannedContainerLoader.container(
            from: Data(#"{"id":"a","dimensions":[0.5,0.2,0.3]}"#.utf8)
        )
        XCTAssertEqual(unnamed.label, "Scanned container")
        XCTAssertNil(unnamed.createdAt)
    }

    func testMalformedJSONIsRejected() {
        XCTAssertThrowsError(try ScannedContainerLoader.container(from: Data("nope".utf8)))
        // `dimensions` is required: a document without it is not a container.
        XCTAssertThrowsError(
            try ScannedContainerLoader.container(from: Data(#"{"id":"a"}"#.utf8))
        )
    }

    // MARK: - Re-homing a plan

    func testMockPlanRehomedIntoTheScannedBag() throws {
        let mock = try PlanLoader.mockPlan()
        let scanned = try ScannedContainerLoader.bundled()
        let rehomed = try XCTUnwrap(mock.replacingContainer(with: scanned))

        XCTAssertEqual(rehomed.container.id, scanned.id)
        XCTAssertEqual(rehomed.container.label, "Scanned suitcase")
        XCTAssertEqual(rehomed.container.dimensions, try XCTUnwrap(scanned.size))

        // Placements, zones, steps and units all survive untouched.
        XCTAssertEqual(rehomed.placements, mock.placements)
        XCTAssertEqual(rehomed.container.zones, mock.container.zones)
        XCTAssertEqual(rehomed.units, .meters)
        XCTAssertEqual(rehomed.version, mock.version)
    }

    /// The scanned bag is larger than the mock on every axis, so the plan stays
    /// valid after the swap.
    func testRehomedPlanIsStillGeometricallyValid() throws {
        let rehomed = try XCTUnwrap(
            try PlanLoader.mockPlan().replacingContainer(with: try ScannedContainerLoader.bundled())
        )
        let issues = rehomed.geometryIssues()

        XCTAssertTrue(
            issues.isEmpty,
            "re-homing should not invalidate the plan:\n"
                + issues.map { "• \($0)" }.joined(separator: "\n")
        )
    }

    /// A bag too small for the plan is reported, never quietly resized.
    func testTooSmallBagIsReportedRatherThanRefitted() throws {
        let mock = try PlanLoader.mockPlan()
        let tiny = ScannedContainer(id: "tiny", name: "Tiny", dimensions: [0.2, 0.1, 0.2])
        let rehomed = try XCTUnwrap(mock.replacingContainer(with: tiny))

        XCTAssertEqual(rehomed.placements, mock.placements, "placements must not be moved")
        XCTAssertTrue(
            rehomed.geometryIssues().contains { issue in
                if case .outOfBounds = issue { return true }
                return false
            },
            "a plan that no longer fits must say so"
        )
    }

    func testUnusableDocumentReturnsNilSoCallersCanFallBack() throws {
        let mock = try PlanLoader.mockPlan()
        let broken = ScannedContainer(id: "broken", name: nil, dimensions: [0.5, 0.2])

        XCTAssertNil(mock.replacingContainer(with: broken))
    }
}
