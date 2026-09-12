#if canImport(CoreGraphics)
import CoreGraphics
#else
import Foundation
#endif
import XCTest
@testable import PackingPlanUI
@testable import PackingPlan

/// The two things the 2D fallback diagram cannot be trusted to get right by
/// inspection, checked without SwiftUI: how tall the diagram asks to be, and which
/// layer picture each half of a nest lands in.
///
/// SwiftUI does not exist on this platform, so the view itself cannot be built —
/// which is exactly why both answers live in plain functions the view calls rather
/// than inside its `body`.
final class PlanDiagramLayoutTests: XCTestCase {

    // MARK: - The diagram's height

    /// The load-bearing property: the diagram's height comes from the width and the
    /// bag, and from nothing else. If it ever starts depending on how much room is
    /// left over, an over-full screen can negotiate it down again — which was the
    /// bug (`aspectRatio(.fit)` in a `VStack` under a caption, a legend, a
    /// disclosure group and up to two banners).
    func testDiagramHeightFollowsWidthAndFootprintOnly() throws {
        let footprint = try PlanLoader.mockPlan().container.dimensions
        XCTAssertEqual(footprint.x, 0.4064, accuracy: 1e-6)
        XCTAssertEqual(footprint.z, 0.6096, accuracy: 1e-6)

        // 390 pt phone, 16 pt of padding a side: 358 pt of content width.
        let content = 390 - 2 * planDiagramPadding
        XCTAssertEqual(content, 358)

        let height = planDiagramHeight(footprint: footprint, width: content)
        XCTAssertEqual(height, 537, accuracy: 0.5, "a portrait bag on a portrait phone")

        // Scale-free in the width, so the footprint exactly fills the box
        // `FootprintProjection` is handed: no letterboxing, nothing cropped.
        for width in [200, 358, 500, 1000] as [CGFloat] {
            let h = planDiagramHeight(footprint: footprint, width: width)
            XCTAssertEqual(h / width, height / content, accuracy: 1e-6)
            let drawn = FootprintProjection(
                footprint: footprint, in: CGSize(width: width, height: h)
            ).footprintRect
            XCTAssertEqual(drawn.width, width, accuracy: 0.01)
            XCTAssertEqual(drawn.height, h, accuracy: 0.01)
        }

        // What a squeeze actually costs, which is the argument for the fixed
        // height: hand the same 358 pt of width a box 197 pt shorter than it asked
        // for — the overflow a 763 pt safe area produces once header, picker,
        // caption, legend and details are counted — and the bag is not merely
        // smaller, it is letterboxed to 63% of the width it could have had.
        let squeezed = FootprintProjection(
            footprint: footprint, in: CGSize(width: content, height: height - 197)
        )
        XCTAssertEqual(squeezed.footprintRect.width, 227, accuracy: 1)
        XCTAssertEqual(squeezed.footprintRect.minX, 65, accuracy: 1, "empty margin either side")

        // Degenerate input gives up rather than dividing by zero or asking for a
        // negative frame: a plan with no interior is a loader problem, not a
        // layout one.
        XCTAssertEqual(planDiagramHeight(footprint: footprint, width: 0), 0)
        XCTAssertEqual(planDiagramHeight(footprint: .zero, width: 358), 0)
    }

    /// A wide, shallow bag asks for less height than it has width — the reason the
    /// ratio is read off the plan rather than hard-coded at the phone's 537.
    func testLandscapeFootprintAsksForLessHeight() {
        let height = planDiagramHeight(footprint: Vector3(0.60, 0.15, 0.30), width: 358)
        XCTAssertEqual(height, 179, accuracy: 0.5)
    }

    // MARK: - Nesting across layers

    /// The split nest: socks in the shoes' cavity, drawn one layer above the shoes.
    /// The host's layer must be able to name its guest and the guest must be able to
    /// name its host, or the pair reads as two unrelated items — or as an overlap.
    func testSplitNestIsReachableFromBothLayers() throws {
        let plan = nestedDemoPlan()
        let layers = plan.layers()
        let nesting = PlanNesting(plan: plan, layers: layers)

        XCTAssertEqual(layers.map { $0.placements.map(\.itemID) }, [
            ["shoes-pair", "sweater-roll"],
            ["socks-pair"],
            ["passport-pouch"],
        ])

        let nest = try XCTUnwrap(nesting.nest(of: "socks-pair"))
        XCTAssertEqual(nest.host.itemID, "shoes-pair")
        XCTAssertEqual(nest.itemLayer, 1)
        XCTAssertEqual(nest.hostLayer, 0)
        XCTAssertTrue(nest.isSplit)

        // The shoes' layer knows the socks go inside them...
        XCTAssertEqual(nesting.guests(hostedIn: layers[0]).map(\.item.itemID), ["socks-pair"])
        // ...and no other layer claims to host them, least of all the socks' own.
        XCTAssertEqual(nesting.guests(hostedIn: layers[1]).map(\.item.itemID), [])
        XCTAssertEqual(nesting.guests(hostedIn: layers[2]).map(\.item.itemID), [])
    }

    /// A nest whose halves share a layer draws small-box-inside-big-box, which still
    /// needs the "in <host>" marking — but not the dotted stand-in, which would put
    /// a second outline on top of the item itself.
    func testSameLayerNestIsMarkedButNotDrawnTwice() {
        let plan = nestedDemoPlan()
        // Drop the socks to the bag floor, the same floor as the shoes, so both
        // halves of the nest land in layer 0.
        let flattened = PackingPlan(
            version: plan.version,
            units: plan.units,
            container: plan.container,
            placements: plan.placements.map { placement in
                guard placement.itemID == "socks-pair" else { return placement }
                return Placement(
                    step: placement.step,
                    itemID: placement.itemID,
                    label: placement.label,
                    zone: placement.zone,
                    position: Vector3(placement.position.x, 0, placement.position.z),
                    size: placement.size,
                    rotation: placement.rotation,
                    note: placement.note,
                    nestedIn: Nesting(
                        itemID: "shoes-pair",
                        cavity: Nesting.Cavity(
                            position: Vector3(0.02, 0, 0.02),
                            size: Vector3(0.10, 0.09, 0.10)
                        )
                    )
                )
            }
        )
        let layers = flattened.layers()
        let nesting = PlanNesting(plan: flattened, layers: layers)

        XCTAssertEqual(nesting.nest(of: "socks-pair")?.isSplit, false)
        XCTAssertEqual(nesting.guests(hostedIn: layers[0]).map(\.item.itemID), [])
    }

    /// A `nestedIn` naming an item the plan does not contain gets **no** affordance:
    /// `honouredNesting()` refuses it, so nothing in the view can turn a producer
    /// bug into a feature. It stays a geometry issue, which is the banner's job.
    func testDanglingHostGetsNoAffordance() {
        let plan = nestedDemoPlan()
        let layers = plan.layers()
        let nesting = PlanNesting(plan: plan, layers: layers)

        XCTAssertNotNil(plan.placements.first { $0.itemID == "passport-pouch" }?.nestedIn)
        XCTAssertNil(nesting.nest(of: "passport-pouch"))
        for layer in layers {
            XCTAssertFalse(nesting.guests(hostedIn: layer).contains { $0.item.itemID == "passport-pouch" })
        }

        let dangling = plan.geometryIssues().filter {
            if case .unknownNestingHost = $0 { return true } else { return false }
        }
        XCTAssertEqual(dangling.count, 1, "\(plan.geometryIssues())")
    }

    /// The nesting fixture's invariants, so the previews built on it keep showing
    /// what their comments claim: the honoured nest is not reported as an overlap,
    /// the dangling one is the only geometry issue, and nothing tips over — a
    /// stability banner here would be noise over the nesting story.
    func testNestedFixtureShowsNestingAndNothingElse() throws {
        let plan = nestedDemoPlan()

        XCTAssertEqual(plan.geometryIssues().count, 1, "\(plan.geometryIssues())")
        XCTAssertTrue(plan.stabilityIssues().isEmpty, "\(plan.stabilityIssues())")
        XCTAssertEqual(plan.honouredNesting().keys.sorted(), ["socks-pair"])
        // Structurally valid, so it could be written out and loaded like any plan.
        XCTAssertEqual(try PlanLoader.plan(from: JSONEncoder().encode(plan)), plan)
    }

    /// The bundled mock plan is the demo's own plan and carries no nesting at all.
    /// Nothing added here may put a banner or a nest marker on it.
    func testBundledMockPlanStaysClean() throws {
        let plan = try PlanLoader.mockPlan()
        let layers = plan.layers()
        let nesting = PlanNesting(plan: plan, layers: layers)

        XCTAssertTrue(plan.geometryIssues().isEmpty, "\(plan.geometryIssues())")
        XCTAssertTrue(plan.stabilityIssues().isEmpty, "\(plan.stabilityIssues())")
        XCTAssertTrue(plan.honouredNesting().isEmpty)
        for layer in layers {
            XCTAssertEqual(nesting.guests(hostedIn: layer).map(\.item.itemID), [])
            for placement in layer.placements {
                XCTAssertNil(nesting.nest(of: placement.itemID))
            }
        }
    }
}
