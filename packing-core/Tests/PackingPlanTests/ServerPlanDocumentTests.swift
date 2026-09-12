import XCTest
@testable import PackingPlan

/// `PlanLoader.plan(fromServerDocument:)` against a document produced by the real
/// `server/app_plan.py`, fed real `packer3d` output.
///
/// The fixture keeps the solver's own block alongside the converted plan, so the
/// two conventions can be cross-checked against each other inside one file.
final class ServerPlanDocumentTests: XCTestCase {

    private func documentData() throws -> Data {
        let url = try XCTUnwrap(
            Bundle.module.url(
                forResource: "server_plan_document",
                withExtension: "json",
                subdirectory: "Fixtures"
            ),
            "server_plan_document.json missing from the test bundle"
        )
        return try Data(contentsOf: url)
    }

    private func decoded() throws -> PackingPlan {
        try PlanLoader.plan(fromServerDocument: try documentData())
    }

    /// The solver's own placements, as the server saw them, for cross-checking.
    private func solverPlacements() throws -> [[String: Any]] {
        let root = try JSONSerialization.jsonObject(with: try documentData()) as! [String: Any]
        let solver = root["solver"] as! [String: Any]
        return solver["placements"] as! [[String: Any]]
    }

    // MARK: - The envelope

    func testUnwrapsThePlanFromTheServerEnvelope() throws {
        let plan = try decoded()

        XCTAssertEqual(plan.placements.count, 13)
        XCTAssertEqual(plan.units, .meters)
        XCTAssertEqual(plan.version, 1)
        // From the suitcase document, not from the solver's container block.
        XCTAssertEqual(plan.container.id, "5fd83487-65fd-4a88-8d2d-4dbaf6be27ba")
        XCTAssertEqual(plan.container.label, "Scanned suitcase")
    }

    /// `solver`, `validation`, `chosen` and `alternatives` ride along beside the
    /// plan; none of them may leak into it or trip decoding.
    func testSiblingKeysAreIgnored() throws {
        let root = try JSONSerialization.jsonObject(with: try documentData()) as! [String: Any]
        XCTAssertEqual(Set(root.keys), ["solver", "validation", "plan", "chosen", "alternatives"])
        XCTAssertNoThrow(try decoded())
    }

    func testABarePlanWithoutTheEnvelopeIsRejected() throws {
        // What `plan(from:)` takes is *not* what the endpoint returns; mixing the
        // two up is the easy mistake, so it must fail rather than half-work.
        let root = try JSONSerialization.jsonObject(with: try documentData()) as! [String: Any]
        let barePlan = try JSONSerialization.data(withJSONObject: root["plan"]!)

        XCTAssertThrowsError(try PlanLoader.plan(fromServerDocument: barePlan))
        XCTAssertNoThrow(try PlanLoader.plan(from: barePlan), "the same bytes are a valid bare plan")
    }

    func testMalformedDocumentsAreRejected() {
        XCTAssertThrowsError(try PlanLoader.plan(fromServerDocument: Data("not json".utf8)))
        XCTAssertThrowsError(try PlanLoader.plan(fromServerDocument: Data(#"{"detail":"boom"}"#.utf8)))
        XCTAssertThrowsError(try PlanLoader.plan(fromServerDocument: Data(#"{"plan":{}}"#.utf8)))
    }

    // MARK: - Min corner, not centre

    /// packer3d's `position` is already the MIN corner of the oriented bbox, and
    /// so is ours, so `app_plan.py` only swaps y/z — it never converts a centre.
    ///
    /// This pins that down: every decoded position must equal the solver's
    /// position with y and z swapped, and must *differ* from the solver's own
    /// `center` swapped the same way. If anyone ever adds a `- size / 2` to this
    /// path, the second half fails.
    func testPositionsAreTheSolversMinCornerNotItsCentre() throws {
        let plan = try decoded()
        let solver = try solverPlacements()
        XCTAssertEqual(plan.placements.count, solver.count)

        for (placement, raw) in zip(plan.orderedPlacements, solver) {
            let position = raw["position"] as! [Double]
            let centre = raw["center"] as! [Double]
            let itemID = raw["item_id"] as! String
            XCTAssertEqual(placement.itemID, itemID)

            // app(x, y, z) = (packer_x, packer_z, packer_y)
            XCTAssertEqual(placement.position.x, Float(position[0]), accuracy: 1e-6, "\(itemID) x")
            XCTAssertEqual(placement.position.y, Float(position[2]), accuracy: 1e-6, "\(itemID) y")
            XCTAssertEqual(placement.position.z, Float(position[1]), accuracy: 1e-6, "\(itemID) z")

            let swappedCentre = Vector3(Float(centre[0]), Float(centre[2]), Float(centre[1]))
            XCTAssertNotEqual(
                placement.position, swappedCentre,
                "\(itemID): a centre-to-min-corner conversion has been introduced somewhere"
            )
            // And our own helper still recovers the centre from the min corner.
            XCTAssertEqual(placement.renderCenter.x, swappedCentre.x, accuracy: 1e-6)
            XCTAssertEqual(placement.renderCenter.y, swappedCentre.y, accuracy: 1e-6)
            XCTAssertEqual(placement.renderCenter.z, swappedCentre.z, accuracy: 1e-6)
        }
    }

    func testSizesAreTheSolversOrientedBoxWithYAndZSwapped() throws {
        for (placement, raw) in zip(try decoded().orderedPlacements, try solverPlacements()) {
            let dims = raw["dims"] as! [Double]
            XCTAssertEqual(placement.size.x, Float(dims[0]), accuracy: 1e-6)
            XCTAssertEqual(placement.size.y, Float(dims[2]), accuracy: 1e-6)
            XCTAssertEqual(placement.size.z, Float(dims[1]), accuracy: 1e-6)
        }
    }

    // MARK: - The container span stays non-negative

    /// The physics frame (`physics/packer3d_adapter.py`) puts the interior at
    /// Z in [-W, 0]; ours puts it at [0, depth]. Only the app frame may reach
    /// here, so a negative coordinate means a physics-frame document has been
    /// wired to the app by mistake.
    func testEveryCoordinateIsNonNegative() throws {
        let plan = try decoded()

        for axis in Axis.allCases {
            XCTAssertGreaterThan(plan.container.dimensions[axis], 0, "container \(axis.label)")
        }
        for placement in plan.orderedPlacements {
            for axis in Axis.allCases {
                XCTAssertGreaterThanOrEqual(
                    placement.position[axis], 0,
                    "\(placement.itemID) has a negative \(axis.label) — physics frame leaked in"
                )
                XCTAssertGreaterThan(placement.size[axis], 0, "\(placement.itemID) \(axis.label) size")
            }
        }
    }

    func testInteriorSpansZeroToDimensionsAndHoldsEveryPlacement() throws {
        let plan = try decoded()
        let interior = plan.container.interior

        XCTAssertEqual(interior.minCorner, .zero)
        XCTAssertEqual(interior.maxCorner, plan.container.dimensions)

        for placement in plan.orderedPlacements {
            XCTAssertTrue(
                placement.box.isContained(in: interior),
                "\(placement.itemID) \(placement.box) escapes \(interior)"
            )
        }
    }

    func testDecodedPlanHasNoGeometryIssues() throws {
        let issues = try decoded().geometryIssues()
        XCTAssertTrue(
            issues.isEmpty,
            "the server's own plan must survive decoding:\n"
                + issues.map { "• \($0)" }.joined(separator: "\n")
        )
    }

    // MARK: - Rotation, zones, structure

    /// `app_plan.py`'s `_ROTATION` table, which re-reads packer3d's string in our
    /// frame: both the world axes and the item's own axes swap y/z.
    func testRotationsAreRemappedFromTheSolversStrings() throws {
        let expected = ["xyz": AxisRotation.xyz, "xzy": .xzy, "yxz": .zyx,
                        "yzx": .zxy, "zxy": .yzx, "zyx": .yxz]

        for (placement, raw) in zip(try decoded().orderedPlacements, try solverPlacements()) {
            let orientation = raw["orientation"] as! String
            // Cylinders carry "cyl_axis_*", which the table has no entry for and
            // the server falls back to XYZ on.
            let want = expected[orientation] ?? .xyz
            XCTAssertEqual(placement.rotation, want, "\(placement.itemID) from '\(orientation)'")
        }
    }

    func testEveryPlacementSitsInTheSingleInteriorZone() throws {
        let plan = try decoded()
        let zone = try XCTUnwrap(plan.container.zone(id: "interior"))

        XCTAssertEqual(plan.container.zones.count, 1, "the solver has no notion of zones")
        XCTAssertEqual(zone.origin, .zero)
        XCTAssertEqual(zone.size, plan.container.dimensions)
        for placement in plan.placements {
            XCTAssertEqual(placement.zone, "interior")
        }
    }

    func testStructuralValidationRunsOnTheServerPath() throws {
        XCTAssertEqual(try decoded().orderedPlacements.map(\.step), Array(1...13))

        func envelope(mutatingPlacements change: ([[String: Any]]) -> [[String: Any]]) throws -> Data {
            var root = try JSONSerialization.jsonObject(with: try documentData()) as! [String: Any]
            var plan = root["plan"] as! [String: Any]
            plan["placements"] = change(plan["placements"] as! [[String: Any]])
            root["plan"] = plan
            return try JSONSerialization.data(withJSONObject: root)
        }

        let duplicated = try envelope { placements in
            var copy = placements
            copy[1]["itemId"] = copy[0]["itemId"]
            return copy
        }
        XCTAssertThrowsError(try PlanLoader.plan(fromServerDocument: duplicated))

        let gap = try envelope { placements in
            var copy = placements
            copy[3]["step"] = 99
            return copy
        }
        XCTAssertThrowsError(try PlanLoader.plan(fromServerDocument: gap))

        let empty = try envelope { _ in [] }
        XCTAssertThrowsError(try PlanLoader.plan(fromServerDocument: empty))
    }

    func testLabelsAndNotesComeFromTheItemDocuments() throws {
        let plan = try decoded()
        XCTAssertTrue(plan.placements.allSatisfy { !$0.label.isEmpty })
        XCTAssertTrue(plan.placements.contains { !$0.note.isEmpty }, "notes carry item descriptions")
    }
}
