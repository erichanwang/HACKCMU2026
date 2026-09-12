import XCTest
@testable import PackingPlanUI
@testable import PackingPlan

final class FootprintProjectionTests: XCTestCase {

    private let interior = Vector3(0.4064, 0.1524, 0.6096)

    /// 2000 points per metre, footprint exactly filling the space.
    private func exactProjection() -> FootprintProjection {
        FootprintProjection(footprint: interior, in: CGSize(width: 812.8, height: 1219.2))
    }

    func testScaleAndFootprintFillAnExactlyMatchingSpace() {
        let projection = exactProjection()

        XCTAssertEqual(projection.scale, 2000, accuracy: 1e-3)
        XCTAssertEqual(projection.footprintRect.minX, 0, accuracy: 1e-3)
        XCTAssertEqual(projection.footprintRect.minY, 0, accuracy: 1e-3)
        XCTAssertEqual(projection.footprintRect.width, 812.8, accuracy: 1e-3)
        XCTAssertEqual(projection.footprintRect.height, 1219.2, accuracy: 1e-3)
    }

    func testFootprintIsCentredAndKeepsAspectRatioInAWiderSpace() {
        let projection = FootprintProjection(
            footprint: interior,
            in: CGSize(width: 1000, height: 1219.2)
        )

        // Depth is the binding axis, so the scale is unchanged...
        XCTAssertEqual(projection.scale, 2000, accuracy: 1e-3)
        // ...and the extra width becomes equal margins.
        XCTAssertEqual(projection.footprintRect.width, 812.8, accuracy: 1e-3)
        XCTAssertEqual(projection.footprintRect.minX, (1000 - 812.8) / 2, accuracy: 1e-3)
        XCTAssertEqual(
            projection.footprintRect.width / projection.footprintRect.height,
            CGFloat(interior.x / interior.z),
            accuracy: 1e-6
        )
    }

    /// The whole point: a projected rect's origin is the placement's **min
    /// corner**, never its centre.
    func testRectOriginIsTheMinCornerNotTheCentre() throws {
        let plan = try PlanLoader.mockPlan()
        let projection = exactProjection()

        for placement in plan.placements {
            let rect = projection.rect(for: placement)

            XCTAssertEqual(
                rect.minX,
                CGFloat(placement.position.x) * 2000,
                accuracy: 1e-3,
                "\(placement.itemID) origin X"
            )
            XCTAssertEqual(
                rect.minY,
                CGFloat(placement.position.z) * 2000,
                accuracy: 1e-3,
                "\(placement.itemID) origin Z"
            )
            XCTAssertEqual(rect.width, CGFloat(placement.size.x) * 2000, accuracy: 1e-3)
            XCTAssertEqual(rect.height, CGFloat(placement.size.z) * 2000, accuracy: 1e-3)

            // A centre-based projection would land half a size further along.
            let centreOrigin = CGFloat(placement.renderCenter.x) * 2000
            XCTAssertNotEqual(rect.minX, centreOrigin, accuracy: 1e-6)
        }
    }

    func testShoesProjectToTheTopLeftCorner() throws {
        let plan = try PlanLoader.mockPlan()
        let shoes = try XCTUnwrap(plan.placement(step: 1))
        let rect = exactProjection().rect(for: shoes)

        XCTAssertEqual(rect.origin.x, 0, accuracy: 1e-3)
        XCTAssertEqual(rect.origin.y, 0, accuracy: 1e-3)
        XCTAssertEqual(rect.width, 600, accuracy: 1e-3)   // 0.30 m
        XCTAssertEqual(rect.height, 300, accuracy: 1e-3)  // 0.15 m
    }

    func testEveryProjectedRectStaysInsideTheFootprint() throws {
        let plan = try PlanLoader.mockPlan()
        let projection = exactProjection()

        for placement in plan.placements {
            let rect = projection.rect(for: placement)
            XCTAssertTrue(
                projection.footprintRect.insetBy(dx: -0.001, dy: -0.001).contains(rect),
                "\(placement.itemID) projects outside the footprint: \(rect)"
            )
        }
    }

    func testDegenerateInputsProduceAnEmptyProjection() {
        let noSpace = FootprintProjection(footprint: interior, in: .zero)
        XCTAssertEqual(noSpace.scale, 0)
        XCTAssertEqual(noSpace.footprintRect, .zero)

        let noBag = FootprintProjection(
            footprint: Vector3(0, 0, 0),
            in: CGSize(width: 100, height: 100)
        )
        XCTAssertEqual(noBag.scale, 0)
        XCTAssertEqual(noBag.footprintRect, .zero)
    }
}
