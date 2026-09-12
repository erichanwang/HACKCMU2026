import XCTest
@testable import PackingPlan

final class PlanProjectionTests: XCTestCase {

    private let size = SIMD2<Float>(800, 1000)

    private func mockProjection() throws -> [ProjectedBox] {
        try PlanLoader.mockPlan().projected(camera: .default, size: size, upTo: nil)
    }

    private func area(_ polygon: [SIMD2<Float>]) -> Float {
        var sum: Float = 0
        for i in polygon.indices {
            let a = polygon[i]
            let b = polygon[(i + 1) % polygon.count]
            sum += a.x * b.y - b.x * a.y
        }
        return abs(sum) / 2
    }

    // MARK: - Camera

    func testCameraClampsPitchAndZoom() {
        XCTAssertEqual(PlanCamera(yaw: 0, pitch: 9, zoom: 1).pitch, 1.5)
        XCTAssertEqual(PlanCamera(yaw: 0, pitch: -9, zoom: 1).pitch, -1.5)
        XCTAssertGreaterThan(PlanCamera(yaw: 0, pitch: 0, zoom: -5).zoom, 0)
    }

    // MARK: - Culling

    /// Three faces, and they tile the silhouette exactly. Equal areas is the whole
    /// test: a hidden face that slipped through the cull would push the sum over
    /// the hull, and a visible face wrongly dropped would leave it short.
    func testEachBoxShowsThreeFacesThatTileItsSilhouette() throws {
        for box in try mockProjection() {
            XCTAssertEqual(box.faces.count, 3, box.id)
            XCTAssertEqual(box.outline.count, 6, "\(box.id) silhouette is a hexagon")
            let faces = box.faces.map { area($0.corners) }.reduce(0, +)
            XCTAssertEqual(faces, area(box.outline), accuracy: area(box.outline) * 1e-3, box.id)
        }
    }

    /// Looking straight down an axis, two of the three faces are edge-on.
    func testFlatOnViewShowsOneFace() throws {
        let boxes = try PlanLoader.mockPlan()
            .projected(camera: PlanCamera(yaw: 0, pitch: 0, zoom: 1), size: size, upTo: nil)
        XCTAssertTrue(boxes.allSatisfy { $0.faces.count == 1 })
    }

    /// The key light is fixed in bag space, so every box is lit identically. If
    /// this drifts, the model ripples as the user drags it.
    func testShadingIsTheSameForEveryBox() throws {
        let boxes = try mockProjection()
        let shades = boxes[0].faces.map(\.shade).sorted()
        XCTAssertEqual(shades[0], 0.5609, accuracy: 1e-4, "+X face")
        XCTAssertEqual(shades[1], 0.6663, accuracy: 1e-4, "+Z face")
        XCTAssertEqual(shades[2], 0.8772, accuracy: 1e-4, "the top face is the brightest")
        for box in boxes {
            XCTAssertEqual(box.faces.map(\.shade).sorted(), shades, box.id)
        }
    }

    // MARK: - Order

    func testContainerIsFirst() throws {
        let boxes = try mockProjection()
        XCTAssertEqual(boxes.count, 7)
        XCTAssertEqual(boxes[0].id, "container")
        XCTAssertEqual(boxes[0].step, 0)
        XCTAssertEqual(boxes[0].label, "Demo carry-on (16 x 6 x 24 in)")
    }

    /// The regression that a centre-depth sort gets wrong.
    ///
    /// `upper` rests on the far half of `lower`, so `lower` reaches further
    /// *toward* the camera and its centre is the nearer of the two — while
    /// `upper` still occludes it. Sorting on `sortDepth` paints `lower` last and
    /// its top face slices a diagonal across `upper`; the returned order must not.
    func testAnItemRestingOnTheFarHalfOfAnotherDrawsAfterIt() {
        let plan = PackingPlan(
            version: 1,
            units: .meters,
            container: Container(id: "c", label: "Stack", dimensions: Vector3(0.4, 0.3, 0.6), zones: []),
            placements: [
                Placement(
                    step: 1, itemID: "lower", label: "Lower", zone: "all",
                    position: Vector3(0, 0, 0.2), size: Vector3(0.3, 0.05, 0.4),
                    rotation: .xyz, note: ""
                ),
                Placement(
                    step: 2, itemID: "upper", label: "Upper", zone: "all",
                    position: Vector3(0, 0.05, 0.2), size: Vector3(0.28, 0.1, 0.2),
                    rotation: .xyz, note: ""
                ),
            ]
        )
        let boxes = plan.projected(camera: .default, size: SIMD2(400, 400), upTo: nil)

        XCTAssertEqual(boxes.map(\.id), ["container", "lower", "upper"])
        // ...and it is later *despite* the depth key, not because of it.
        XCTAssertGreaterThan(boxes[2].sortDepth, boxes[1].sortDepth)
    }

    func testUpToKeepsTheStepsPackedSoFar() throws {
        let plan = try PlanLoader.mockPlan()
        let boxes = plan.projected(camera: .default, size: size, upTo: 2)
        XCTAssertEqual(boxes.count, 3)
        XCTAssertTrue(boxes.dropFirst().allSatisfy { $0.step <= 2 })
        XCTAssertEqual(plan.projected(camera: .default, size: size, upTo: 0).count, 1)
    }

    func testProjectionIsDeterministic() throws {
        let plan = try PlanLoader.mockPlan()
        XCTAssertEqual(
            plan.projected(camera: .default, size: size, upTo: nil),
            plan.projected(camera: .default, size: size, upTo: nil)
        )
    }

    // MARK: - Fit

    func testContainerIsCentredAndLeavesAMargin() throws {
        let outline = try mockProjection()[0].outline
        let (minX, maxX) = (outline.map(\.x).min()!, outline.map(\.x).max()!)
        let (minY, maxY) = (outline.map(\.y).min()!, outline.map(\.y).max()!)

        // Width is the binding axis here: 92% of 800.
        XCTAssertEqual(minX, 32, accuracy: 1e-2)
        XCTAssertEqual(maxX, 768, accuracy: 1e-2)
        XCTAssertEqual(minY, 237.4007, accuracy: 1e-2)
        XCTAssertEqual(maxY, 762.5993, accuracy: 1e-2)
        XCTAssertEqual((minX + maxX) / 2, 400, accuracy: 1e-2)
        XCTAssertEqual((minY + maxY) / 2, 500, accuracy: 1e-2)
    }

    func testZoomScalesAboutTheCentre() throws {
        let plan = try PlanLoader.mockPlan()
        let one = plan.projected(camera: .default, size: size, upTo: nil)[0]
        let two = plan.projected(
            camera: PlanCamera(yaw: 0.6, pitch: 0.5, zoom: 2), size: size, upTo: nil
        )[0]

        XCTAssertEqual(two.center, SIMD2(400, 500))
        XCTAssertEqual((area(two.outline) / area(one.outline)).squareRoot(), 2, accuracy: 1e-3)
    }

    // MARK: - Degenerate input

    func testDegenerateInputStaysFinite() throws {
        let plan = try PlanLoader.mockPlan()
        let zeroContainer = PackingPlan(
            version: plan.version,
            units: plan.units,
            container: Container(id: "z", label: "Zero", dimensions: .zero, zones: []),
            placements: plan.placements
        )

        func assertFinite(_ boxes: [ProjectedBox], _ what: String) {
            XCTAssertFalse(boxes.isEmpty, what)
            for box in boxes {
                XCTAssertTrue(box.sortDepth.isFinite, what)
                XCTAssertTrue(box.center.x.isFinite && box.center.y.isFinite, what)
                XCTAssertTrue(box.outline.allSatisfy { $0.x.isFinite && $0.y.isFinite }, what)
                for face in box.faces {
                    XCTAssertTrue(face.shade.isFinite, what)
                    XCTAssertTrue(face.corners.allSatisfy { $0.x.isFinite && $0.y.isFinite }, what)
                }
            }
        }

        assertFinite(zeroContainer.projected(camera: .default, size: size, upTo: nil), "zero container")
        assertFinite(plan.projected(camera: .default, size: .zero, upTo: nil), "zero rect")
        assertFinite(
            plan.projected(camera: PlanCamera(yaw: 0, pitch: 0, zoom: 0), size: size, upTo: nil),
            "zoom clamp"
        )
        assertFinite(
            plan.projected(camera: PlanCamera(yaw: 0.3, pitch: 1.5, zoom: 1e6), size: size, upTo: nil),
            "extreme pitch and zoom"
        )
    }
}
