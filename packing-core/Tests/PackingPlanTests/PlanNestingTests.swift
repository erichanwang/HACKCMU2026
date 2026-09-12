import XCTest
@testable import PackingPlan

/// `nestedIn` — the solver genuinely putting a small item inside a larger one's
/// scanned cavity, and the geometry checks not calling that a collision.
///
/// The line these tests pin down: a nested pair may share volume, but only inside
/// the cavity the producer named. Every test that allows an overlap is paired with
/// one where the same two items overlap somewhere else and it is still reported.
final class PlanNestingTests: XCTestCase {

    private let tolerance: Float = 1e-6

    private func loadNestedPlan() throws -> PackingPlan {
        try PlanLoader.plan(resourceNamed: "nested-plan", in: .module)
    }

    /// Rebuilds the plan with `cup` moved and/or re-nested. Everything else is the
    /// fixture verbatim, so each variant differs from the clean case in one way.
    private func replacingCup(
        in plan: PackingPlan,
        position: Vector3? = nil,
        nestedIn: Nesting?
    ) throws -> PackingPlan {
        let cup = try XCTUnwrap(plan.placements.first { $0.itemID == "cup" })
        let rest = plan.placements.filter { $0.itemID != "cup" }
        return PackingPlan(
            version: plan.version,
            units: plan.units,
            container: plan.container,
            placements: rest + [
                Placement(
                    step: cup.step,
                    itemID: cup.itemID,
                    label: cup.label,
                    zone: cup.zone,
                    position: position ?? cup.position,
                    size: cup.size,
                    rotation: cup.rotation,
                    note: cup.note,
                    nestedIn: nestedIn
                )
            ]
        )
    }

    private func intersections(_ issues: [GeometryIssue]) -> [Set<String>] {
        issues.compactMap { issue in
            if case let .intersection(a, b, _) = issue { return Set([a, b]) }
            return nil
        }
    }

    // MARK: - The field is optional

    /// The common case for a while: the producer has not started emitting the field
    /// and every plan must decode, and validate, exactly as it did before.
    func testPlanWithoutTheFieldDecodesAsBefore() throws {
        let plan = try PlanLoader.mockPlan()

        XCTAssertTrue(plan.placements.allSatisfy { $0.nestedIn == nil })
        XCTAssertTrue(plan.honouredNesting().isEmpty)
        XCTAssertTrue(plan.geometryIssues(tolerance: tolerance).isEmpty)
        XCTAssertTrue(plan.stabilityIssues(tolerance: tolerance).isEmpty)

        // And a plan with no nesting does not grow a `nestedIn` key on the way out.
        let json = try XCTUnwrap(String(data: try JSONEncoder().encode(plan), encoding: .utf8))
        XCTAssertFalse(json.contains("nestedIn"))
    }

    func testExplicitNullIsNotNested() throws {
        let json = """
        {"version":1,"units":"meters",
         "container":{"id":"c","label":"C","dimensions":{"x":1,"y":1,"z":1},
           "zones":[{"id":"interior","label":"I","origin":{"x":0,"y":0,"z":0},"size":{"x":1,"y":1,"z":1}}]},
         "placements":[{"step":1,"itemId":"a","label":"A","zone":"interior",
           "position":{"x":0,"y":0,"z":0},"size":{"x":0.1,"y":0.1,"z":0.1},
           "rotation":"XYZ","note":"","nestedIn":null}]}
        """
        let plan = try PlanLoader.plan(from: Data(json.utf8))
        XCTAssertNil(plan.placements[0].nestedIn)
        XCTAssertTrue(plan.honouredNesting().isEmpty)
    }

    // MARK: - The wire format

    func testFixtureDecodesTheAgreedShape() throws {
        let plan = try loadNestedPlan()
        let cup = try XCTUnwrap(plan.placements.first { $0.itemID == "cup" })
        let nesting = try XCTUnwrap(cup.nestedIn)

        XCTAssertEqual(nesting.itemID, "bowl")
        XCTAssertEqual(nesting.cavity.position, Vector3(0.08, 0.03, 0.08))
        XCTAssertEqual(nesting.cavity.size, Vector3(0.14, 0.07, 0.14))
        // Cavity is min corner + full extent, like a placement.
        XCTAssertEqual(nesting.cavity.box.maxCorner, Vector3(0.22, 0.10, 0.22))
        XCTAssertNil(try XCTUnwrap(plan.placements.first { $0.itemID == "bowl" }).nestedIn)
    }

    func testNestingRoundTrips() throws {
        let plan = try loadNestedPlan()
        let data = try JSONEncoder().encode(plan)
        XCTAssertEqual(try PlanLoader.plan(from: data), plan)
    }

    // MARK: - A legitimate nest is clean

    func testCupInTheBowlsCavityIsNotAnOverlap() throws {
        let plan = try loadNestedPlan()
        let bowl = try XCTUnwrap(plan.placements.first { $0.itemID == "bowl" })
        let cup = try XCTUnwrap(plan.placements.first { $0.itemID == "cup" })

        // The pair really does share volume — this is not a fixture that dodges the
        // question by leaving a gap.
        XCTAssertTrue(bowl.box.intersects(cup.box, tolerance: tolerance))

        let issues = plan.geometryIssues(tolerance: tolerance)
        XCTAssertTrue(
            issues.isEmpty,
            "Expected a clean nested plan, found:\n" + issues.map { "• \($0)" }.joined(separator: "\n")
        )
        XCTAssertNoThrow(try plan.validateGeometry(tolerance: tolerance))
        XCTAssertEqual(plan.honouredNesting()["cup"]?.itemID, "bowl")
    }

    func testTheCavityFloorHoldsTheCupUp() throws {
        let plan = try loadNestedPlan()
        let issues = plan.stabilityIssues(tolerance: tolerance)
        XCTAssertTrue(
            issues.isEmpty,
            "Expected a stable nested plan, found:\n" + issues.map { "• \($0)" }.joined(separator: "\n")
        )
    }

    /// Negative control for the test above: the cup's base is above the bowl's own
    /// box floor and the bowl is solid, so without the cavity nothing is under the
    /// cup and it reads as floating. The cavity is what fixes it — not a blanket
    /// exemption for nested items.
    func testTheSameCupFloatsWithoutTheCavity() throws {
        let plan = try replacingCup(in: try loadNestedPlan(), nestedIn: nil)

        XCTAssertTrue(
            plan.stabilityIssues(tolerance: tolerance).contains { issue in
                if case let .floating(itemID, _) = issue { return itemID == "cup" }
                return false
            },
            "Expected the un-nested cup to float, found: \(plan.stabilityIssues(tolerance: tolerance))"
        )
    }

    // MARK: - Overlap outside the cavity is still an overlap

    /// The crux. The cup is shoved left until part of the volume it shares with the
    /// bowl lies outside the stated cavity — through the bowl's wall. Some of the
    /// overlap is still inside the cavity, so an implementation that merely skipped
    /// nested pairs would call this clean.
    func testOverlapReachingOutsideTheCavityIsReported() throws {
        let plan = try loadNestedPlan()
        let cavity = try XCTUnwrap(plan.placements.first { $0.itemID == "cup" }?.nestedIn?.cavity)
        let pushed = try replacingCup(
            in: plan,
            position: Vector3(0.04, 0.03, 0.10),
            nestedIn: Nesting(itemID: "bowl", cavity: cavity)
        )

        // Still partly in the cavity: X 0.08-0.12 of the shared volume is legal, the
        // strip X 0.05-0.08 is inside the bowl's wall.
        XCTAssertEqual(pushed.honouredNesting()["cup"]?.itemID, "bowl")
        XCTAssertEqual(intersections(pushed.geometryIssues(tolerance: tolerance)), [Set(["bowl", "cup"])])
        XCTAssertThrowsError(try pushed.validateGeometry(tolerance: tolerance))
    }

    /// Nesting licenses the pair in the cavity and nowhere else: a third item that
    /// truly interpenetrates the host is untouched by it.
    func testNestingDoesNotExcuseAThirdItemsOverlap() throws {
        let plan = try loadNestedPlan()
        let spoon = Placement(
            step: 3,
            itemID: "spoon",
            label: "Spoon",
            zone: "interior",
            position: Vector3(0.00, 0.02, 0.10),
            size: Vector3(0.10, 0.02, 0.02),
            rotation: .xyz,
            note: ""
        )
        let withSpoon = PackingPlan(
            version: plan.version,
            units: plan.units,
            container: plan.container,
            placements: plan.placements + [spoon]
        )

        XCTAssertEqual(intersections(withSpoon.geometryIssues(tolerance: tolerance)), [Set(["bowl", "spoon"])])
    }

    // MARK: - The guest must rest on the floor it declares

    /// The cavity used to only have to *exist*: a guest hanging in mid-air inside a
    /// correctly-shaped cavity passed every check, because the plinth that
    /// suppresses `.floating` was built from the same declaration nobody was
    /// testing. Lift the cup 8 mm and leave its cavity where it is — the shared
    /// volume is still entirely inside the cavity, so nothing else in the file has
    /// anything to say about it.
    func testGuestLiftedOffItsDeclaredCavityFloorIsReported() throws {
        let plan = try loadNestedPlan()
        let cavity = try XCTUnwrap(plan.placements.first { $0.itemID == "cup" }?.nestedIn?.cavity)
        let lifted = try replacingCup(
            in: plan,
            position: Vector3(0.10, 0.038, 0.10),
            nestedIn: Nesting(itemID: "bowl", cavity: cavity)
        )
        let issues = lifted.geometryIssues(tolerance: tolerance)

        // The nest is still honoured and the overlap is still excused: this issue is
        // the only thing between the plan and a silent pass.
        XCTAssertEqual(lifted.honouredNesting()["cup"]?.itemID, "bowl")
        XCTAssertEqual(intersections(issues), [])
        let offFloor = issues.compactMap { issue -> Float? in
            if case let .nestedOffCavityFloor(itemID, hostItemID, gap) = issue,
               itemID == "cup", hostItemID == "bowl" { return gap }
            return nil
        }
        XCTAssertEqual(offFloor.count, 1, "expected one off-floor issue, found: \(issues)")
        XCTAssertEqual(try XCTUnwrap(offFloor.first), 0.008, accuracy: 1e-6)
        XCTAssertThrowsError(try lifted.validateGeometry(tolerance: tolerance))
        XCTAssertTrue(
            issues.first?.description.contains("0.008 m above the floor of the cavity") == true,
            "expected the gap and its direction in the message, got: \(issues)"
        )
        // And the stability pass agrees rather than contradicting it.
        XCTAssertTrue(lifted.stabilityIssues(tolerance: tolerance).contains { issue in
            if case let .floating(itemID, _) = issue { return itemID == "cup" }
            return false
        })
    }

    /// The other direction: sunk into the floor it claims. Says "below", not a
    /// negative "above".
    func testGuestSunkIntoItsDeclaredCavityFloorIsReported() throws {
        let plan = try loadNestedPlan()
        let cavity = try XCTUnwrap(plan.placements.first { $0.itemID == "cup" }?.nestedIn?.cavity)
        let sunk = try replacingCup(
            in: plan,
            position: Vector3(0.10, 0.024, 0.10),
            nestedIn: Nesting(itemID: "bowl", cavity: cavity)
        )
        let issues = sunk.geometryIssues(tolerance: tolerance)

        XCTAssertTrue(
            issues.contains { issue in
                if case let .nestedOffCavityFloor(itemID, _, gap) = issue {
                    return itemID == "cup" && abs(gap + 0.006) < 1e-6
                }
                return false
            },
            "expected the cup to be reported 6 mm below its declared floor, found: \(issues)"
        )
        XCTAssertTrue(
            issues.contains { $0.description.contains("0.006 m below the floor of the cavity") },
            "expected the direction in the message, got: \(issues)"
        )
    }

    /// The tolerance is 1 mm (`restingContactTolerance`), not the 1e-6 float slack:
    /// "is this resting on that" across two independently authored faces, the same
    /// question `tools/pipeline_check/check_seams.py` asks with the same number.
    func testRestingContactIsJudgedAtOneMillimetre() throws {
        let plan = try loadNestedPlan()
        let cavity = try XCTUnwrap(plan.placements.first { $0.itemID == "cup" }?.nestedIn?.cavity)
        func offFloorIssues(liftedBy lift: Float) throws -> [GeometryIssue] {
            try replacingCup(
                in: plan,
                position: Vector3(0.10, 0.03 + lift, 0.10),
                nestedIn: Nesting(itemID: "bowl", cavity: cavity)
            )
            .geometryIssues(tolerance: tolerance)
            .filter { if case .nestedOffCavityFloor = $0 { return true } else { return false } }
        }

        XCTAssertEqual(try offFloorIssues(liftedBy: 0.0005), [])
        XCTAssertEqual(try offFloorIssues(liftedBy: 0.0015).count, 1)
    }

    /// **The boundary, stated as a test so nobody mistakes the check above for more
    /// than it is.** Lift the guest *and* its declared floor together, exactly as
    /// `check_nested_support`'s negative control does: the declaration stays
    /// perfectly self-consistent — the cup rests on the floor it claims, the cavity
    /// is still inside the bowl, the shared volume is still inside the cavity — and
    /// only the host's real solid decomposition can say the cup is in the air. The
    /// plan JSON does not carry that decomposition, so this plan is clean here, and
    /// catching it is `tools/pipeline_check/check_seams.py`'s job, not this file's.
    func testGuestAndItsDeclaredFloorLiftedTogetherIsBeyondWhatAPlanCanShow() throws {
        let plan = try loadNestedPlan()
        let raised = try replacingCup(
            in: plan,
            position: Vector3(0.10, 0.038, 0.10),
            nestedIn: Nesting(
                itemID: "bowl",
                cavity: Nesting.Cavity(
                    position: Vector3(0.08, 0.038, 0.08),
                    size: Vector3(0.14, 0.062, 0.14)
                )
            )
        )
        let issues = raised.geometryIssues(tolerance: tolerance)
        XCTAssertTrue(
            issues.isEmpty,
            "the plan alone cannot disprove a self-consistent declaration; found:\n"
                + issues.map { "• \($0)" }.joined(separator: "\n")
        )
    }

    // MARK: - The cavity must be inside the host

    /// A cavity is a void in the host, so one poking out through the bowl's wall
    /// describes nothing — and it would still license the pair's overlap and hold
    /// the cup up. The cup itself is inside both the cavity and the bowl, so this is
    /// the only issue in the plan.
    func testCavityReachingOutsideTheHostIsReported() throws {
        let plan = try loadNestedPlan()
        let pushed = try replacingCup(
            in: plan,
            position: Vector3(0.16, 0.03, 0.10),
            nestedIn: Nesting(
                itemID: "bowl",
                cavity: Nesting.Cavity(
                    position: Vector3(0.15, 0.03, 0.08),
                    size: Vector3(0.14, 0.07, 0.14)
                )
            )
        )
        let issues = pushed.geometryIssues(tolerance: tolerance)

        XCTAssertEqual(intersections(issues), [])
        XCTAssertEqual(issues.count, 1, "expected only the cavity issue, found: \(issues)")
        guard case let .cavityOutsideHost(itemID, hostItemID, overshoot) = try XCTUnwrap(issues.first) else {
            return XCTFail("expected .cavityOutsideHost, got \(issues)")
        }
        XCTAssertEqual(itemID, "cup")
        XCTAssertEqual(hostItemID, "bowl")
        XCTAssertEqual(overshoot.x, 0.04, accuracy: 1e-6)
        XCTAssertEqual(overshoot.y, 0, accuracy: 1e-6)
    }

    /// The same rule on the axis that matters most: a declared floor *below* the
    /// host's own base. The bowl stands on the bag floor, so a cavity floor at
    /// y = -0.01 is under the bag as well as under the bowl — and the cup, still on
    /// the bowl's real inner floor, is now 4 cm off the floor it declares.
    func testCavityFloorBelowTheHostsBaseIsReported() throws {
        let plan = try loadNestedPlan()
        let sunkCavity = try replacingCup(
            in: plan,
            nestedIn: Nesting(
                itemID: "bowl",
                cavity: Nesting.Cavity(
                    position: Vector3(0.08, -0.01, 0.08),
                    size: Vector3(0.14, 0.11, 0.14)
                )
            )
        )
        let issues = sunkCavity.geometryIssues(tolerance: tolerance)

        XCTAssertTrue(
            issues.contains { issue in
                if case let .cavityOutsideHost(itemID, _, overshoot) = issue {
                    return itemID == "cup" && abs(overshoot.y - 0.01) < 1e-6
                }
                return false
            },
            "expected the cavity to be reported 1 cm below the bowl, found: \(issues)"
        )
        XCTAssertTrue(
            issues.contains { issue in
                if case let .nestedOffCavityFloor(itemID, _, gap) = issue {
                    return itemID == "cup" && abs(gap - 0.04) < 1e-6
                }
                return false
            },
            "expected the cup to be reported 4 cm above that floor, found: \(issues)"
        )
    }

    // MARK: - A dubious assertion is not nesting

    func testDanglingHostIsReportedAndIgnored() throws {
        let plan = try loadNestedPlan()
        let cavity = try XCTUnwrap(plan.placements.first { $0.itemID == "cup" }?.nestedIn?.cavity)
        let dangling = try replacingCup(
            in: plan,
            nestedIn: Nesting(itemID: "casserole", cavity: cavity)
        )
        let issues = dangling.geometryIssues(tolerance: tolerance)

        XCTAssertTrue(dangling.honouredNesting().isEmpty)
        XCTAssertTrue(
            issues.contains(.unknownNestingHost(itemID: "cup", hostItemID: "casserole")),
            "Expected the dangling host to be reported, found: \(issues)"
        )
        // And with the assertion ignored, the cup/bowl overlap comes back.
        XCTAssertEqual(intersections(issues), [Set(["bowl", "cup"])])
    }

    func testSelfReferenceIsNotNesting() throws {
        let plan = try loadNestedPlan()
        let cavity = try XCTUnwrap(plan.placements.first { $0.itemID == "cup" }?.nestedIn?.cavity)
        let itself = try replacingCup(in: plan, nestedIn: Nesting(itemID: "cup", cavity: cavity))
        let issues = itself.geometryIssues(tolerance: tolerance)

        XCTAssertTrue(itself.honouredNesting().isEmpty)
        // A self-reference names an item that *is* in the plan, so it is not a
        // dangling host — just not nesting.
        XCTAssertFalse(issues.contains { issue in
            if case .unknownNestingHost = issue { return true }
            return false
        })
        XCTAssertEqual(intersections(issues), [Set(["bowl", "cup"])])
    }

    func testACycleIsNotNesting() throws {
        let plan = try loadNestedPlan()
        let cup = try XCTUnwrap(plan.placements.first { $0.itemID == "cup" })
        let bowl = try XCTUnwrap(plan.placements.first { $0.itemID == "bowl" })
        let cavity = try XCTUnwrap(cup.nestedIn?.cavity)
        // bowl claims to nest in the cup that claims to nest in the bowl.
        let cyclic = PackingPlan(
            version: plan.version,
            units: plan.units,
            container: plan.container,
            placements: [
                Placement(
                    step: bowl.step,
                    itemID: bowl.itemID,
                    label: bowl.label,
                    zone: bowl.zone,
                    position: bowl.position,
                    size: bowl.size,
                    rotation: bowl.rotation,
                    note: bowl.note,
                    nestedIn: Nesting(itemID: "cup", cavity: cavity)
                ),
                cup,
            ]
        )

        XCTAssertTrue(cyclic.honouredNesting().isEmpty)
        XCTAssertEqual(intersections(cyclic.geometryIssues(tolerance: tolerance)), [Set(["bowl", "cup"])])
    }
}
