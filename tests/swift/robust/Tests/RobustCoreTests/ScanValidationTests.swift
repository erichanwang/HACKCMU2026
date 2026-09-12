import XCTest
@testable import RobustCore

final class ScanValidationTests: XCTestCase {
    // MARK: pickTablePlane

    func testPicksNearestPlaneBelowSeed() {
        // floor at 0, table at 0.75, seed (tap on top of the item) at 0.9 — must pick the
        // table, not the floor, even though the floor is also "below the seed".
        XCTAssertEqual(pickTablePlane(planeYs: [0.0, 0.75], seedY: 0.9, maxDrop: 1.0), 0.75)
    }

    func testIgnoresPlanesAboveSeed() {
        XCTAssertEqual(pickTablePlane(planeYs: [0.75, 1.2], seedY: 0.9, maxDrop: 1.0), 0.75)
    }

    func testRejectsPlaneBeyondMaxDrop() {
        // only the floor is visible, 2m below the seed -- no usable table nearby.
        XCTAssertNil(pickTablePlane(planeYs: [-1.1], seedY: 0.9, maxDrop: 1.0))
    }

    func testNoPlaneBelowSeed() {
        XCTAssertNil(pickTablePlane(planeYs: [1.5], seedY: 0.9, maxDrop: 1.0))
    }

    func testNoPlanesAtAll() {
        XCTAssertNil(pickTablePlane(planeYs: [], seedY: 0.9, maxDrop: 1.0))
    }

    // MARK: isUsableBox

    func testUsableBox() {
        XCTAssertTrue(isUsableBox(width: 0.2, height: 0.1, depth: 0.3, maxDimension: 1.2))
    }

    func testRejectsZeroDimension() {
        XCTAssertFalse(isUsableBox(width: 0, height: 0.1, depth: 0.3, maxDimension: 1.2))
    }

    func testRejectsNegativeDimension() {
        XCTAssertFalse(isUsableBox(width: -0.1, height: 0.1, depth: 0.3, maxDimension: 1.2))
    }

    func testRejectsNaNDimension() {
        XCTAssertFalse(isUsableBox(width: .nan, height: 0.1, depth: 0.3, maxDimension: 1.2))
    }

    func testRejectsInfiniteDimension() {
        XCTAssertFalse(isUsableBox(width: .infinity, height: 0.1, depth: 0.3, maxDimension: 1.2))
    }

    func testRejectsOversizedCluster() {
        // a cluster that swallowed a wall or the floor.
        XCTAssertFalse(isUsableBox(width: 2.5, height: 0.1, depth: 0.3, maxDimension: 1.2))
    }

    // MARK: isUsableHeightMap

    func testUsableHeightMap() {
        XCTAssertTrue(isUsableHeightMap([[0.1, 0.2], [0.0, 0.3]]))
    }

    func testRejectsEmptyHeightMap() {
        XCTAssertFalse(isUsableHeightMap([]))
        XCTAssertFalse(isUsableHeightMap([[]]))
    }

    func testRejectsRaggedHeightMap() {
        XCTAssertFalse(isUsableHeightMap([[0.1, 0.2], [0.3]]))
    }

    func testRejectsNaNInHeightMap() {
        XCTAssertFalse(isUsableHeightMap([[0.1, Float.nan]]))
    }

    /// Every labelStatus the server can write must get its own actionable line. The bug this
    /// pins: "failed" and "unidentified" are both "not pending", so before the branch existed
    /// they fell through to "labelled unknown" — the app presenting a non-answer as an answer.
    func testEveryLabelStatusGetsADistinctMessage() {
        let done = labelStatusMessage(labelStatus: "done", label: "hiking boot", identifyHint: nil)
        let pending = labelStatusMessage(labelStatus: "pending", label: nil, identifyHint: nil)
        let failed = labelStatusMessage(labelStatus: "failed", label: "unknown", identifyHint: nil)
        let unident = labelStatusMessage(labelStatus: "unidentified", label: "unknown",
                                         identifyHint: "labelling is off — type it in")
        XCTAssertEqual(done, "labelled hiking boot")
        XCTAssertEqual(pending, "labelling…")
        XCTAssertEqual(unident, "labelling is off — type it in")
        XCTAssertTrue(failed.contains("type it in"))
        // Distinct, and none of the terminal ones may read as a successful label.
        XCTAssertEqual(Set([done, pending, failed, unident]).count, 4)
        for m in [failed, unident] { XCTAssertFalse(m.hasPrefix("labelled "), m) }
    }

    /// The server's hint is authoritative — it knows whether a model ran at all and whether the
    /// scan geometry was degenerate. Ours is only the fallback for a malformed response.
    func testUnidentifiedPrefersTheServerHint() {
        XCTAssertEqual(labelStatusMessage(labelStatus: "unidentified", label: "unknown",
                                          identifyHint: "the scan looks too flat or small to show the object clearly — try rescanning it"),
                       "the scan looks too flat or small to show the object clearly — try rescanning it")
        XCTAssertEqual(labelStatusMessage(labelStatus: "unidentified", label: "unknown", identifyHint: nil),
                       "couldn't identify it — type it in")
    }
}
