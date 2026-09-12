import XCTest
@testable import PackingPlan

/// Exercises `PlanLoader.plan(fromServerDocument:)` — the live path, decoding
/// the `{suitcaseId, createdAt, solver, validation, plan, chosen, alternatives}`
/// document `server/main.py`'s `/suitcases/{id}/plan` returns.
///
/// `Self.capturedDocument` is not hand-written to match the decoder: it is the
/// literal output of `server/app_plan.py::to_app_plan` run against
/// `packer3d/examples/suitcase_result.json`'s `"naive"` result (see the packing
/// task notes for the one-off script). It carries the real sibling keys
/// (`solver`, `validation`, `stats`, `metrics`, a `cylinder` shape, an
/// `unpacked` list) that a hand-authored fixture would never think to include.
final class PlanLoaderServerDocumentTests: XCTestCase {

    // MARK: - The real document

    func testServerDocumentDecodesEveryPlacement() throws {
        let plan = try PlanLoader.plan(fromServerDocument: Data(Self.capturedDocument.utf8))

        XCTAssertEqual(plan.units, .meters)
        XCTAssertEqual(plan.container.id, "demo-suitcase")
        XCTAssertEqual(plan.container.dimensions, Vector3(0.75, 0.28, 0.5))
        XCTAssertEqual(plan.container.zones.map(\.id), ["interior"])

        // The solver packed 12 of the 18 items it was given (6 are in its
        // `unpacked` list). All 12 placed items must survive decoding.
        XCTAssertEqual(plan.placements.count, 12)
        XCTAssertEqual(plan.orderedPlacements.map(\.step), Array(1...12))
    }

    /// Spot-check one placement field by field so a silently-dropped or
    /// silently-defaulted field would fail here, not just a count check.
    func testServerDocumentFieldsAreNotDropped() throws {
        let plan = try PlanLoader.plan(fromServerDocument: Data(Self.capturedDocument.utf8))
        let wine = try XCTUnwrap(plan.placements.first { $0.itemID == "wine" })

        XCTAssertEqual(wine.step, 11)
        XCTAssertEqual(wine.label, "wine")
        XCTAssertEqual(wine.zone, "interior")
        XCTAssertEqual(wine.position, Vector3(0.36, 0.14, 0.2))
        XCTAssertEqual(wine.size, Vector3(0.32, 0.08, 0.08))
        // The solver's own orientation string is "cyl_axis_x" (a cylinder axis,
        // not one of the six box permutations); app_plan.py's `_ROTATION.get`
        // falls back to "XYZ" for anything it doesn't recognise, and that
        // fallback is what must show up here — not a decode failure.
        XCTAssertEqual(wine.rotation, .xyz)
        XCTAssertEqual(wine.note, "")
    }

    /// The decoder must not choke on — or accidentally require — the sibling
    /// keys (`solver`, `validation`, `chosen`, `alternatives`) that ride along
    /// with `plan` in the real document but aren't part of `PackingPlan`.
    func testServerDocumentIgnoresSiblingKeysItDoesNotModel() throws {
        XCTAssertNoThrow(try PlanLoader.plan(fromServerDocument: Data(Self.capturedDocument.utf8)))
    }

    /// **Known gap, pinned down rather than fixed** (out of scope: this would
    /// mean adding an `unpacked` field to `PackingPlan`, which touches the model
    /// the UI renders, `app_plan.py`, and the 2D/AR views — a bigger change than
    /// this pass owns). `to_app_plan` in `server/app_plan.py` says outright:
    /// "`result["unpacked"]` is not part of a plan and is dropped here." The
    /// solver's own document (captured above) lists 6 unpacked items
    /// (`books_2`, `jacket`, `tripod`, `hair_dryer`, `gifts`, `umbrella`), but
    /// `PackingPlan` has no field to carry that, so a demo suitcase that didn't
    /// fully fit renders as if every item fit — no banner, no "6 items left
    /// out" — because the information never reaches this module.
    func testDecodedPlanHasNoTraceOfItemsTheSolverCouldNotPlace() throws {
        let plan = try PlanLoader.plan(fromServerDocument: Data(Self.capturedDocument.utf8))
        let decodedIDs = Set(plan.placements.map(\.itemID))

        XCTAssertEqual(decodedIDs.count, 12, "only the packed items decode")
        for droppedID in ["books_2", "jacket", "tripod", "hair_dryer", "gifts", "umbrella"] {
            XCTAssertFalse(
                decodedIDs.contains(droppedID),
                "\(droppedID) was in the solver's `unpacked` list and must not appear as a placement"
            )
        }
    }

    // MARK: - Failure paths

    func testMissingPlanKeyFailsLoudly() {
        var document = Self.capturedJSONObject()
        document.removeValue(forKey: "plan")

        XCTAssertThrowsError(try PlanLoader.plan(fromServerDocument: Self.data(from: document))) { error in
            guard case PlanError.malformedJSON = error else {
                return XCTFail("expected .malformedJSON, got \(error)")
            }
        }
    }

    /// The empty-plan case the demo will hit if a suitcase is too small for
    /// anything: the solver packs nothing, `placements` decodes to `[]`, and the
    /// loader must refuse it rather than hand the UI a plan with zero items to
    /// silently render as "done."
    func testEmptyPlanFailsLoudlyRatherThanRenderingNothing() {
        var document = Self.capturedJSONObject()
        var plan = document["plan"] as! [String: Any]
        plan["placements"] = []
        document["plan"] = plan

        XCTAssertThrowsError(try PlanLoader.plan(fromServerDocument: Self.data(from: document))) { error in
            XCTAssertEqual(error as? PlanError, .noPlacements)
        }
    }

    /// A document missing a field `Placement` requires (here, `rotation`) must
    /// fail decoding outright — never fall back to a default that would hide a
    /// server-side contract break.
    func testPlacementMissingARequiredFieldFailsLoudly() {
        var document = Self.capturedJSONObject()
        var plan = document["plan"] as! [String: Any]
        var placements = plan["placements"] as! [[String: Any]]
        placements[0].removeValue(forKey: "rotation")
        plan["placements"] = placements
        document["plan"] = plan

        XCTAssertThrowsError(try PlanLoader.plan(fromServerDocument: Self.data(from: document))) { error in
            guard case PlanError.malformedJSON = error else {
                return XCTFail("expected .malformedJSON, got \(error)")
            }
        }
    }

    /// An extra field on a placement (something a future solver revision might
    /// add) must not break decoding — `Placement`'s `CodingKeys` simply ignores
    /// what it doesn't declare.
    func testPlacementWithAnUnknownExtraFieldStillDecodes() throws {
        var document = Self.capturedJSONObject()
        var plan = document["plan"] as! [String: Any]
        var placements = plan["placements"] as! [[String: Any]]
        placements[0]["fragile"] = false
        plan["placements"] = placements
        document["plan"] = plan

        let decoded = try PlanLoader.plan(fromServerDocument: Self.data(from: document))
        XCTAssertEqual(decoded.placements.count, 12)
    }

    // MARK: - Scale

    func testSingleItemPlanDecodes() throws {
        var document = Self.capturedJSONObject()
        var plan = document["plan"] as! [String: Any]
        var placements = plan["placements"] as! [[String: Any]]
        placements = [placements[0]]
        placements[0]["step"] = 1
        plan["placements"] = placements
        document["plan"] = plan

        let decoded = try PlanLoader.plan(fromServerDocument: Self.data(from: document))
        XCTAssertEqual(decoded.placements.count, 1)
        XCTAssertEqual(decoded.placements[0].itemID, "shoes_1")
    }

    func testThirtyItemPlanDecodes() throws {
        var document = Self.capturedJSONObject()
        var plan = document["plan"] as! [String: Any]
        let template = (plan["placements"] as! [[String: Any]])[0]

        var placements: [[String: Any]] = []
        for i in 1...30 {
            var item = template
            item["step"] = i
            item["itemId"] = "item-\(i)"
            item["label"] = "Item \(i)"
            // Positions don't need to be geometrically valid for a decode test —
            // structural validation, not `geometryIssues()`, is what's under test.
            item["position"] = ["x": 0.0, "y": 0.0, "z": Double(i) * 0.01]
            placements.append(item)
        }
        plan["placements"] = placements
        document["plan"] = plan

        let decoded = try PlanLoader.plan(fromServerDocument: Self.data(from: document))
        XCTAssertEqual(decoded.placements.count, 30)
        XCTAssertEqual(decoded.orderedPlacements.map(\.step), Array(1...30))
    }

    // MARK: - Fixture plumbing

    private static func capturedJSONObject() -> [String: Any] {
        let data = Data(capturedDocument.utf8)
        return try! JSONSerialization.jsonObject(with: data) as! [String: Any]
    }

    private static func data(from object: [String: Any]) -> Data {
        try! JSONSerialization.data(withJSONObject: object)
    }

    /// Captured verbatim: `to_app_plan(json.load(open("packer3d/examples/suitcase_result.json"))["naive"], suitcase, {})`
    /// wrapped in the `{suitcaseId, createdAt, solver, validation, plan}` shape
    /// `server/planner.py::plan` / `server/main.py` actually return, with
    /// `suitcase = {"_id": "demo-suitcase", "name": "Demo Suitcase", "dimensions": [0.75, 0.28, 0.5]}`.
    private static let capturedDocument = #"""
    {
      "suitcaseId": "demo-suitcase",
      "createdAt": "2026-09-12T00:00:00Z",
      "solver": {
        "strategy": "naive",
        "container": {
          "id": "suitcase", "shape": "box", "dims": [0.75, 0.5, 0.28], "gravity": true,
          "max_mass": 23.0, "min_support": 0.6, "obstacles": [],
          "com_target": [0.375, 0.25, 0.0], "com_axis_weights": [1.0, 1.0, 0.5]
        },
        "placements": [
          {"item_id": "shoes_1", "shape": "box", "position": [0.0, 0.0, 0.0], "dims": [0.32, 0.2, 0.12], "center": [0.16, 0.1, 0.06], "orientation": "xyz", "mass": 1.2, "fragile": false},
          {"item_id": "shoes_2", "shape": "box", "position": [0.32, 0.0, 0.0], "dims": [0.32, 0.2, 0.12], "center": [0.48, 0.1, 0.06], "orientation": "xyz", "mass": 1.2, "fragile": false},
          {"item_id": "jeans_1", "shape": "box", "position": [0.0, 0.2, 0.0], "dims": [0.35, 0.28, 0.06], "center": [0.175, 0.34, 0.03], "orientation": "xyz", "mass": 0.7, "fragile": false},
          {"item_id": "jeans_2", "shape": "box", "position": [0.35, 0.2, 0.0], "dims": [0.35, 0.28, 0.06], "center": [0.525, 0.34, 0.03], "orientation": "xyz", "mass": 0.7, "fragile": false},
          {"item_id": "shirts_1", "shape": "box", "position": [0.0, 0.2, 0.06], "dims": [0.35, 0.25, 0.08], "center": [0.175, 0.325, 0.1], "orientation": "xyz", "mass": 0.9, "fragile": false},
          {"item_id": "shirts_2", "shape": "box", "position": [0.35, 0.2, 0.06], "dims": [0.35, 0.25, 0.08], "center": [0.525, 0.325, 0.1], "orientation": "xyz", "mass": 0.9, "fragile": false},
          {"item_id": "laptop", "shape": "box", "position": [0.0, 0.2, 0.14], "dims": [0.36, 0.25, 0.03], "center": [0.18, 0.325, 0.155], "orientation": "xyz", "mass": 1.8, "fragile": true},
          {"item_id": "camera", "shape": "box", "position": [0.0, 0.0, 0.12], "dims": [0.25, 0.18, 0.14], "center": [0.125, 0.09, 0.19], "orientation": "xyz", "mass": 2.2, "fragile": true},
          {"item_id": "toiletries", "shape": "box", "position": [0.25, 0.0, 0.12], "dims": [0.28, 0.15, 0.12], "center": [0.39, 0.075, 0.18], "orientation": "xyz", "mass": 1.5, "fragile": false},
          {"item_id": "water_bottle", "shape": "cylinder", "position": [0.64, 0.0, 0.0], "dims": [0.08, 0.08, 0.26], "center": [0.68, 0.04, 0.13], "orientation": "cyl_axis_z", "mass": 0.9, "fragile": false, "axis": "z", "radius": 0.04, "height": 0.26},
          {"item_id": "wine", "shape": "cylinder", "position": [0.36, 0.2, 0.14], "dims": [0.32, 0.08, 0.08], "center": [0.52, 0.24, 0.18], "orientation": "cyl_axis_x", "mass": 1.4, "fragile": true, "axis": "x", "radius": 0.04, "height": 0.32},
          {"item_id": "books_1", "shape": "box", "position": [0.36, 0.28, 0.14], "dims": [0.24, 0.17, 0.1], "center": [0.48, 0.365, 0.19], "orientation": "xyz", "mass": 3.0, "fragile": false}
        ],
        "unpacked": [
          {"id": "books_2", "reason": "no feasible position (space, support or fragile constraints)"},
          {"id": "jacket", "reason": "no feasible position (space, support or fragile constraints)"},
          {"id": "tripod", "reason": "no feasible position (space, support or fragile constraints)"},
          {"id": "hair_dryer", "reason": "no feasible position (space, support or fragile constraints)"},
          {"id": "gifts", "reason": "no feasible position (space, support or fragile constraints)"},
          {"id": "umbrella", "reason": "no feasible position (space, support or fragile constraints)"}
        ],
        "metrics": {
          "items_packed": 12, "items_unpacked": 6, "volume_utilization": 0.5919561712622031,
          "bbox_extent_utilization": 0.8557714285714285, "packed_volume": 0.06215539798253133,
          "usable_volume": 0.10500000000000001, "total_mass": 16.4, "max_mass": 23.0,
          "com": [0.35682926829268297, 0.22338414634146345, 0.1385365853658537], "com_basis": "mass",
          "com_target": [0.375, 0.25, 0.0], "com_lateral_offset": 0.03222699422459269,
          "com_offset_3d": 0.1422355955504191, "com_deviation": 0.3547125751182388,
          "max_height": 0.26, "max_height_fraction": 0.9285714285714285,
          "unpacked_priority_volume_fraction": 0.3160995830126281, "objective": 6.6093284236928564
        },
        "stats": {"strategy": "first-fit bottom-back-left"}
      },
      "validation": {"valid": true, "violations": []},
      "plan": {
        "version": 1,
        "units": "meters",
        "container": {
          "id": "demo-suitcase",
          "label": "Demo Suitcase",
          "dimensions": {"x": 0.75, "y": 0.28, "z": 0.5},
          "zones": [
            {"id": "interior", "label": "Interior", "origin": {"x": 0.0, "y": 0.0, "z": 0.0}, "size": {"x": 0.75, "y": 0.28, "z": 0.5}}
          ]
        },
        "placements": [
          {"step": 1, "itemId": "shoes_1", "label": "shoes_1", "zone": "interior", "position": {"x": 0.0, "y": 0.0, "z": 0.0}, "size": {"x": 0.32, "y": 0.12, "z": 0.2}, "rotation": "XYZ", "note": ""},
          {"step": 2, "itemId": "shoes_2", "label": "shoes_2", "zone": "interior", "position": {"x": 0.32, "y": 0.0, "z": 0.0}, "size": {"x": 0.32, "y": 0.12, "z": 0.2}, "rotation": "XYZ", "note": ""},
          {"step": 3, "itemId": "jeans_1", "label": "jeans_1", "zone": "interior", "position": {"x": 0.0, "y": 0.0, "z": 0.2}, "size": {"x": 0.35, "y": 0.06, "z": 0.28}, "rotation": "XYZ", "note": ""},
          {"step": 4, "itemId": "jeans_2", "label": "jeans_2", "zone": "interior", "position": {"x": 0.35, "y": 0.0, "z": 0.2}, "size": {"x": 0.35, "y": 0.06, "z": 0.28}, "rotation": "XYZ", "note": ""},
          {"step": 5, "itemId": "shirts_1", "label": "shirts_1", "zone": "interior", "position": {"x": 0.0, "y": 0.06, "z": 0.2}, "size": {"x": 0.35, "y": 0.08, "z": 0.25}, "rotation": "XYZ", "note": ""},
          {"step": 6, "itemId": "shirts_2", "label": "shirts_2", "zone": "interior", "position": {"x": 0.35, "y": 0.06, "z": 0.2}, "size": {"x": 0.35, "y": 0.08, "z": 0.25}, "rotation": "XYZ", "note": ""},
          {"step": 7, "itemId": "laptop", "label": "laptop", "zone": "interior", "position": {"x": 0.0, "y": 0.14, "z": 0.2}, "size": {"x": 0.36, "y": 0.03, "z": 0.25}, "rotation": "XYZ", "note": ""},
          {"step": 8, "itemId": "camera", "label": "camera", "zone": "interior", "position": {"x": 0.0, "y": 0.12, "z": 0.0}, "size": {"x": 0.25, "y": 0.14, "z": 0.18}, "rotation": "XYZ", "note": ""},
          {"step": 9, "itemId": "toiletries", "label": "toiletries", "zone": "interior", "position": {"x": 0.25, "y": 0.12, "z": 0.0}, "size": {"x": 0.28, "y": 0.12, "z": 0.15}, "rotation": "XYZ", "note": ""},
          {"step": 10, "itemId": "water_bottle", "label": "water_bottle", "zone": "interior", "position": {"x": 0.64, "y": 0.0, "z": 0.0}, "size": {"x": 0.08, "y": 0.26, "z": 0.08}, "rotation": "XYZ", "note": ""},
          {"step": 11, "itemId": "wine", "label": "wine", "zone": "interior", "position": {"x": 0.36, "y": 0.14, "z": 0.2}, "size": {"x": 0.32, "y": 0.08, "z": 0.08}, "rotation": "XYZ", "note": ""},
          {"step": 12, "itemId": "books_1", "label": "books_1", "zone": "interior", "position": {"x": 0.36, "y": 0.14, "z": 0.28}, "size": {"x": 0.24, "y": 0.1, "z": 0.17}, "rotation": "XYZ", "note": ""}
        ]
      }
    }
    """#
}
