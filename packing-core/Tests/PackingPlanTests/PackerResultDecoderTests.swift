import simd
import XCTest
@testable import PackingPlan

final class PackerAxisRemapTests: XCTestCase {

    /// The remap in isolation. If the team flips the convention, these are the
    /// assertions to rewrite — everything else follows from `packerAxisForOurs`.
    func testEachOurAxisTakesTheRightPackerAxis() {
        // A vector whose components are distinguishable per axis.
        let mapped = PackerAxes.toBagFrame(SIMD3(0.75, 0.50, 0.28))

        XCTAssertEqual(mapped.x, 0.50, accuracy: 1e-6, "our X (width) takes packer Y (width)")
        XCTAssertEqual(mapped.y, 0.28, accuracy: 1e-6, "our Y (up) takes packer Z (up)")
        XCTAssertEqual(mapped.z, 0.75, accuracy: 1e-6, "our Z (depth) takes packer X (length)")
    }

    /// A permutation with determinant +1 maps a right-handed frame to a
    /// right-handed frame. If this ever fails the whole scene is mirrored.
    func testRemapIsAnEvenPermutationSoHandednessSurvives() {
        let x = PackerAxes.toBagFrame(SIMD3(1, 0, 0)).simd
        let y = PackerAxes.toBagFrame(SIMD3(0, 1, 0)).simd
        let z = PackerAxes.toBagFrame(SIMD3(0, 0, 1)).simd

        // Columns are the images of packer's basis vectors.
        let determinant = simd_determinant(simd_float3x3(x, y, z))
        XCTAssertEqual(determinant, 1, accuracy: 1e-6, "remap must not mirror the scene")

        // And it really is a permutation: every axis used exactly once.
        let images = [x, y, z].map { simd_float3($0) }
        XCTAssertEqual(Set(images.map { "\($0)" }).count, 3)
    }

    func testRemapPreservesLengthsAndTheOrigin() {
        XCTAssertEqual(PackerAxes.toBagFrame(SIMD3(0, 0, 0)), .zero)
        let v = PackerAxes.toBagFrame(SIMD3(0.3, 0.4, 0.5))
        XCTAssertEqual(simd_length(v.simd), Float(simd_length(SIMD3<Double>(0.3, 0.4, 0.5))), accuracy: 1e-6)
    }

    func testBoxOrientationIsPermutedOnTheWorldSideOnly() {
        // Identity in packer's frame is not identity in ours: our X takes packer's
        // Y, so the item axis that packer sent down its Y now runs along our X.
        XCTAssertEqual(PackerAxes.toBagRotation("xyz"), .yzx)
        // The camera's real orientation from the fixture.
        XCTAssertEqual(PackerAxes.toBagRotation("yxz"), .xzy)
        XCTAssertEqual(PackerAxes.toBagRotation("xzy"), .zyx)
    }

    func testOrientationRemapAlwaysYieldsAValidPermutation() {
        for orientation in ["xyz", "xzy", "yxz", "yzx", "zxy", "zyx"] {
            let mapped = PackerAxes.toBagRotation(orientation)
            XCTAssertEqual(Set(mapped.rawValue).count, 3, "\(orientation) → \(mapped.rawValue)")
        }
    }

    func testCylinderAndJunkOrientationsFallBackToIdentity() {
        // Cylinders render as their bounding box; `dims` already carries the axis.
        XCTAssertEqual(PackerAxes.toBagRotation("cyl_axis_x"), .xyz)
        XCTAssertEqual(PackerAxes.toBagRotation("cyl_axis_z"), .xyz)
        XCTAssertEqual(PackerAxes.toBagRotation(""), .xyz)
        XCTAssertEqual(PackerAxes.toBagRotation("abc"), .xyz)
    }
}

final class PackerResultDecoderTests: XCTestCase {

    /// The real `--compare` output committed in packer3d/examples.
    private func fixtureData() throws -> Data {
        let url = try XCTUnwrap(
            Bundle.module.url(
                forResource: "suitcase_result",
                withExtension: "json",
                subdirectory: "Fixtures"
            ),
            "suitcase_result.json missing from the test bundle"
        )
        return try Data(contentsOf: url)
    }

    private func decoded() throws -> DecodedPackerResult {
        try PackerResultDecoder.decode(try fixtureData())
    }

    func testDecodesTheOptimizedBranchOfACompareFile() throws {
        let result = try decoded()

        // optimized packs 13 of 18; naive packs 12. Getting 12 here would mean we
        // silently read the wrong branch.
        XCTAssertEqual(result.plan.placements.count, 13)
        XCTAssertEqual(result.unpacked.count, 5)
    }

    func testContainerDimensionsAreRemapped() throws {
        let container = try decoded().plan.container

        XCTAssertEqual(container.id, "suitcase")
        // packer3d dims [0.75, 0.50, 0.28] = length × width × up.
        XCTAssertEqual(container.dimensions.x, 0.50, accuracy: 1e-6)  // width
        XCTAssertEqual(container.dimensions.y, 0.28, accuracy: 1e-6)  // up
        XCTAssertEqual(container.dimensions.z, 0.75, accuracy: 1e-6)  // depth
    }

    func testFirstPlacementIsRemappedCornerAndSize() throws {
        let camera = try XCTUnwrap(try decoded().plan.placement(step: 1))

        XCTAssertEqual(camera.itemID, "camera")
        // packer position [0, 0, 0], dims [0.18, 0.25, 0.14].
        XCTAssertEqual(camera.position, .zero)
        XCTAssertEqual(camera.size.x, 0.25, accuracy: 1e-6)
        XCTAssertEqual(camera.size.y, 0.14, accuracy: 1e-6)
        XCTAssertEqual(camera.size.z, 0.18, accuracy: 1e-6)
        XCTAssertEqual(camera.rotation, .xzy)  // packer "yxz"
    }

    func testAPlacementOffTheFloorKeepsItsHeight() throws {
        // shirts_2 sits at packer z = 0.12, which is our Y.
        let shirts = try XCTUnwrap(
            try decoded().plan.placements.first { $0.itemID == "shirts_2" }
        )
        XCTAssertEqual(shirts.position.y, 0.12, accuracy: 1e-6)
        XCTAssertEqual(shirts.position.z, 0.18, accuracy: 1e-6)  // packer x
        XCTAssertEqual(shirts.position.x, 0.0, accuracy: 1e-6)   // packer y
    }

    func testStepsAreOneThroughNInSolverOrder() throws {
        let plan = try decoded().plan
        XCTAssertEqual(plan.orderedPlacements.map(\.step), Array(1...13))
        XCTAssertEqual(Set(plan.placements.map(\.itemID)).count, 13, "item ids must be unique")
    }

    /// The strongest check that the remap is self-consistent: packer3d guarantees
    /// its own results contain no overlaps and stay inside the container, so if
    /// our translated plan violates either, the remap is wrong.
    func testDecodedPlanHasNoGeometryIssues() throws {
        let plan = try decoded().plan
        let issues = plan.geometryIssues()

        XCTAssertTrue(
            issues.isEmpty,
            "A valid packer3d result must survive translation:\n"
                + issues.map { "• \($0)" }.joined(separator: "\n")
        )
    }

    /// Everything must sit inside the *remapped* box specifically — this fails
    /// loudly if width and depth were swapped.
    func testEveryPlacementFitsTheRemappedContainer() throws {
        let plan = try decoded().plan
        for placement in plan.orderedPlacements {
            XCTAssertTrue(
                placement.box.isContained(in: plan.container.interior),
                "\(placement.itemID) \(placement.box) escapes \(plan.container.interior)"
            )
        }
    }

    func testUnpackedItemsKeepTheirReasons() throws {
        let unpacked = try decoded().unpacked

        XCTAssertEqual(Set(unpacked.map(\.id)).count, unpacked.count)
        XCTAssertTrue(unpacked.contains { $0.id == "wine" }, "ids: \(unpacked.map(\.id))")
        for item in unpacked {
            XCTAssertFalse(item.reason.isEmpty, "\(item.id) lost its reason")
        }
    }

    /// A decoded plan must be indistinguishable from a hand-authored one: round
    /// trip it through our own loader, which re-checks units, unique ids and the
    /// step sequence.
    func testDecodedPlanSurvivesOurOwnLoader() throws {
        let encoded = try JSONEncoder().encode(try decoded().plan)
        let reloaded = try PlanLoader.plan(from: encoded)

        XCTAssertEqual(reloaded.units, .meters)
        XCTAssertEqual(reloaded.placements.count, 13)
        XCTAssertEqual(reloaded.container.dimensions, try decoded().plan.container.dimensions)
    }

    func testEveryPlacementNamesTheContainerZone() throws {
        let plan = try decoded().plan
        for placement in plan.placements {
            XCTAssertEqual(placement.zone, PackerResultDecoder.wholeContainerZoneID)
            XCTAssertNotNil(plan.container.zone(id: placement.zone))
        }
    }

    func testGroupsIntoLayersForTheSceneView() throws {
        // Not a UI test — just proof the decoded plan is usable by the existing
        // layer grouping the 3D view relies on.
        let plan = try decoded().plan
        let floors = Set(plan.placements.map { ($0.position.y * 1000).rounded() })
        XCTAssertGreaterThan(floors.count, 1, "a packed suitcase should stack")
    }

    // MARK: - Shape handling

    func testBareResultWithoutCompareWrapperAlsoDecodes() throws {
        let compare = try JSONSerialization.jsonObject(with: try fixtureData()) as! [String: Any]
        let bare = try JSONSerialization.data(withJSONObject: compare["optimized"]!)

        let result = try PackerResultDecoder.decode(bare)
        XCTAssertEqual(result.plan.placements.count, 13)
    }

    func testNaiveBranchIsUsedOnlyWhenOptimizedIsAbsent() throws {
        let compare = try JSONSerialization.jsonObject(with: try fixtureData()) as! [String: Any]
        let naiveOnly = try JSONSerialization.data(withJSONObject: ["naive": compare["naive"]!])

        XCTAssertEqual(try PackerResultDecoder.decode(naiveOnly).plan.placements.count, 12)
    }

    func testNonPackerJSONIsRejected() {
        XCTAssertThrowsError(try PackerResultDecoder.decode(Data(#"{"hello":"world"}"#.utf8)))
        XCTAssertThrowsError(try PackerResultDecoder.decode(Data("not json".utf8)))
    }

    func testMalformedVectorIsRejected() {
        let short = Data("""
        {"container":{"id":"c","dims":[1,2]},"placements":[]}
        """.utf8)
        XCTAssertThrowsError(try PackerResultDecoder.decode(short)) { error in
            guard case PackerDecodeError.badVector(let field, let count) = error else {
                return XCTFail("wrong error: \(error)")
            }
            XCTAssertEqual(field, "container.dims")
            XCTAssertEqual(count, 2)
        }
    }
}
