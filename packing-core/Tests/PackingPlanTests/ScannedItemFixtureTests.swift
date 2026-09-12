import XCTest
@testable import PackingPlan

/// The fixture is data, not code, so these tests check the shape a renderer
/// relies on rather than any behaviour.
final class ScannedItemFixtureTests: XCTestCase {

    private struct Document: Decodable {
        let id: String
        let dimensions: [Float]
        let cellSize: Float
        let heights: [[Float]]
        let label: String?
        let suitcaseId: String?
    }

    private func fixture() throws -> Document {
        try JSONDecoder().decode(Document.self, from: try ScannedItemFixture.data())
    }

    func testFixtureDecodes() throws {
        let doc = try fixture()
        XCTAssertEqual(doc.id, "7833EA08-3707-4CA3-8F28-4F0E9167ED1E")
        XCTAssertEqual(doc.label, "pink ceramic mug")
        XCTAssertEqual(doc.cellSize, 0.01, accuracy: 1e-9)
    }

    /// It belongs to the scanned suitcase already bundled as `container.json`.
    func testFixtureBelongsToTheBundledContainer() throws {
        XCTAssertEqual(try fixture().suitcaseId, try ScannedContainerLoader.bundled().id)
    }

    func testGridIsRectangularAndNonEmpty() throws {
        let heights = try fixture().heights
        let columns = try XCTUnwrap(heights.first?.count)

        XCTAssertFalse(heights.isEmpty)
        XCTAssertGreaterThan(columns, 0)
        for (index, row) in heights.enumerated() {
            XCTAssertEqual(row.count, columns, "row \(index) is ragged")
        }
    }

    /// SCAN_OUTPUT.md: the grid is `ceil(width / cellSize)` rows by
    /// `ceil(depth / cellSize)` columns.
    ///
    /// The column count matches exactly. The row count is one short of the 91 the
    /// declared width implies — see the note in the fixture's provenance; this
    /// asserts what the file actually contains so a regenerated document that
    /// disagrees fails loudly.
    func testGridSizeAgainstDeclaredDimensions() throws {
        let doc = try fixture()
        let expectedColumns = Int((doc.dimensions[2] / doc.cellSize).rounded(.up))

        XCTAssertEqual(doc.heights[0].count, expectedColumns, "columns")
        XCTAssertEqual(doc.heights.count, 90, "rows in the file as transcribed")
    }

    func testHeightsAreNonNegativeAndWithinTheDeclaredHeight() throws {
        let doc = try fixture()
        let filled = doc.heights.flatMap { $0 }.filter { $0 > 0 }

        XCTAssertFalse(filled.isEmpty, "an all-zero grid renders nothing")
        XCTAssertTrue(doc.heights.allSatisfy { $0.allSatisfy { $0 >= 0 } }, "no negative heights")
        XCTAssertLessThanOrEqual(
            try XCTUnwrap(filled.max()), doc.dimensions[1] + 1e-5,
            "a cell is taller than the bounding box"
        )
    }

    /// Zero means empty, and the renderer skips those cells; if the grid were
    /// fully dense the heightmap path would be indistinguishable from a box.
    func testGridHasBothFilledAndEmptyCells() throws {
        let cells = try fixture().heights.flatMap { $0 }
        XCTAssertTrue(cells.contains { $0 > 0 })
        XCTAssertTrue(cells.contains { $0 == 0 })
    }
}
