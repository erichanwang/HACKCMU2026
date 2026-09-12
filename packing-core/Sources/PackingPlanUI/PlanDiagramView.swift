import PackingPlan

#if canImport(SwiftUI)
import SwiftUI

/// Top-down 2D diagram of a packing plan — the fallback path that must work with
/// no AR session, no plane detection, and no camera permission.
///
/// Placements are grouped into horizontal layers by their floor height and shown
/// one layer at a time. Each item is a rectangle positioned and sized to scale
/// within the container footprint, seen from above (bag X across, bag Z down).
public struct PlanDiagramView: View {
    private let plan: PackingPlan
    private let layers: [PlanLayer]
    private let issues: [GeometryIssue]
    private let stability: [GeometryIssue]
    private let stats: PlanStats

    @State private var selection: Int = 0

    /// - Parameter initialLayer: which layer to show first. Useful for previews
    ///   and for returning the user to the layer they were last working on.
    public init(plan: PackingPlan, initialLayer: Int = 0) {
        self.plan = plan
        self.layers = plan.layers()
        self._selection = State(initialValue: initialLayer)
        // Computed once: a plan does not change while it is on screen, and the
        // 2D view is exactly where a bad plan should become visible.
        self.issues = plan.geometryIssues()
        self.stability = plan.stabilityIssues()
        self.stats = PlanStats(plan: plan)
    }

    private var currentLayer: PlanLayer? {
        layers.indices.contains(selection) ? layers[selection] : layers.first
    }

    public var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            header

            if !issues.isEmpty {
                PlanIssueBanner(kind: .geometry, issues: issues)
            }

            if !stability.isEmpty {
                PlanIssueBanner(kind: .stability, issues: stability)
            }

            if layers.count > 1 {
                Picker("Layer", selection: $selection) {
                    ForEach(layers) { layer in
                        Text("Layer \(layer.index + 1)").tag(layer.index)
                    }
                }
                .pickerStyle(.segmented)
            }

            if let layer = currentLayer {
                layerCaption(for: layer)
                diagram(for: layer)
                legend(for: layer)
            } else {
                Text("This plan has no placements.")
                    .foregroundStyle(.secondary)
            }

            details

            Spacer(minLength: 0)
        }
        .padding()
    }

    // MARK: - Header

    private var header: some View {
        VStack(alignment: .leading, spacing: 2) {
            Text(plan.container.label)
                .font(.headline)
            Text(
                "\(centimetres(plan.container.dimensions.x)) × "
                    + "\(centimetres(plan.container.dimensions.y)) × "
                    + "\(centimetres(plan.container.dimensions.z)) interior · "
                    + "\(plan.placements.count) items · "
                    + stats.fullnessText
            )
            .font(.caption)
            .foregroundStyle(.secondary)
        }
    }

    /// `PlanStats.Layer` groups placements by exactly the rule `PlanLayer` uses —
    /// same 5 mm tolerance, same code — so index `n` is the same layer in both.
    /// The guard is for a plan with no placements, not for a mismatch.
    private func layerCaption(for layer: PlanLayer) -> some View {
        Text(
            stats.layers.indices.contains(layer.index)
                ? stats.layers[layer.index].summaryText
                : ""
        )
        .font(.caption)
        .foregroundStyle(.secondary)
    }

    // MARK: - Details

    /// The plan-wide numbers that do not change as you flip layers. Collapsed
    /// because none of them is a *packing instruction*: you read "is there room
    /// for the charger" and "what goes in first" once, at the start or the end,
    /// not while lining up the item in front of you.
    private var details: some View {
        DisclosureGroup("Pack details") {
            VStack(alignment: .leading, spacing: 4) {
                Text(stats.gapText)
                Text(stats.floorCoverageText)
                ForEach(stats.orderText, id: \.self) { line in
                    Text(line)
                }
            }
            .font(.caption)
            .foregroundStyle(.secondary)
            .frame(maxWidth: .infinity, alignment: .leading)
        }
        .font(.caption.weight(.medium))
    }

    // MARK: - Diagram

    private func diagram(for layer: PlanLayer) -> some View {
        GeometryReader { proxy in
            let projection = FootprintProjection(
                footprint: plan.container.dimensions,
                in: proxy.size
            )

            ZStack(alignment: .topLeading) {
                // Container footprint.
                RoundedRectangle(cornerRadius: 6)
                    .fill(Color.secondary.opacity(0.08))
                    .overlay(
                        RoundedRectangle(cornerRadius: 6)
                            .strokeBorder(Color.secondary.opacity(0.45), lineWidth: 1)
                    )
                    .frame(
                        width: projection.footprintRect.width,
                        height: projection.footprintRect.height
                    )
                    .offset(
                        x: projection.footprintRect.minX,
                        y: projection.footprintRect.minY
                    )

                // Items from lower layers still occupying this height.
                ForEach(plan.protrusions(into: layer)) { placement in
                    ProtrusionRectangle(rect: projection.rect(for: placement))
                }

                // This layer's own items.
                ForEach(layer.placements) { placement in
                    PlacementRectangle(
                        placement: placement,
                        rect: projection.rect(for: placement)
                    )
                }
            }
        }
        .aspectRatio(
            CGFloat(plan.container.dimensions.x / plan.container.dimensions.z),
            contentMode: .fit
        )
        .frame(maxWidth: .infinity)
    }

    private func legend(for layer: PlanLayer) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            ForEach(layer.placements) { placement in
                HStack(alignment: .firstTextBaseline, spacing: 8) {
                    Text("\(placement.step)")
                        .font(.caption2.monospacedDigit().weight(.bold))
                        .foregroundStyle(.white)
                        .frame(width: 18, height: 18)
                        .background(placement.diagramColor, in: Circle())
                    VStack(alignment: .leading, spacing: 1) {
                        Text(placement.label).font(.caption.weight(.medium))
                        Text(placement.note)
                            .font(.caption2)
                            .foregroundStyle(.secondary)
                    }
                }
            }

            let protrusions = plan.protrusions(into: layer)
            if !protrusions.isEmpty {
                Text(
                    "Dashed: \(protrusions.map(\.label).joined(separator: ", ")) "
                        + "\(protrusions.count == 1 ? "stands" : "stand") up through this layer."
                )
                .font(.caption2)
                .foregroundStyle(.secondary)
            }

            Text("Seen from above · X across, Z down · origin at top-left")
                .font(.caption2)
                .foregroundStyle(.tertiary)
        }
    }
}

// MARK: - Issue banner

/// The one banner shape this module uses for "something is wrong with this plan",
/// in the two readings the model keeps apart.
///
/// `geometryIssues()` means the solver emitted something impossible — items
/// outside the bag, items sharing volume — and a call site may refuse the plan
/// over it. `stabilityIssues()` means the plan is legal and will still not
/// survive being carried. Folding them together would say "this plan is wrong"
/// about a plan that merely tips, which is the distinction `validateGeometry()`
/// exists to preserve; so: same shape, filled vs hollow mark, different verdict.
///
/// Internal rather than private: `PlanViewer` shows it over the 3D scene, which
/// has no issue reporting of its own.
struct PlanIssueBanner: View {
    enum Kind {
        case geometry
        case stability

        var tint: Color {
            switch self {
            case .geometry: return .orange
            case .stability: return .yellow
            }
        }

        var symbol: String {
            switch self {
            case .geometry: return "exclamationmark.triangle.fill"
            case .stability: return "exclamationmark.triangle"
            }
        }

        func title(count: Int) -> String {
            switch self {
            case .geometry:
                return "\(count) geometry \(count == 1 ? "issue" : "issues") — this plan is wrong"
            case .stability:
                return "\(count) stability \(count == 1 ? "warning" : "warnings") — "
                    + "this plan will not survive being carried"
            }
        }
    }

    let kind: Kind
    let issues: [GeometryIssue]

    var body: some View {
        HStack(alignment: .top, spacing: 8) {
            Image(systemName: kind.symbol)
                .foregroundStyle(kind.tint)
            VStack(alignment: .leading, spacing: 2) {
                Text(kind.title(count: issues.count))
                    .font(.caption.weight(.semibold))
                // The count in the title is the honest total; three lines is as
                // much detail as a banner can carry without becoming the page.
                ForEach(Array(issues.prefix(3).enumerated()), id: \.offset) { _, issue in
                    Text(issue.description)
                        .font(.caption2)
                        .foregroundStyle(.secondary)
                }
            }
        }
        .padding(8)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(kind.tint.opacity(0.12), in: RoundedRectangle(cornerRadius: 8))
    }
}

// MARK: - Item rectangles

private struct PlacementRectangle: View {
    let placement: Placement
    let rect: CGRect

    /// Below this the text is unreadable and the legend carries the label instead.
    private var showsLabel: Bool { rect.width >= 54 && rect.height >= 28 }
    private var showsSize: Bool { rect.width >= 74 && rect.height >= 44 }

    var body: some View {
        RoundedRectangle(cornerRadius: 4)
            .fill(placement.diagramColor.opacity(0.25))
            .overlay(
                RoundedRectangle(cornerRadius: 4)
                    .strokeBorder(placement.diagramColor, lineWidth: 1.5)
            )
            .overlay(alignment: .topLeading) {
                if showsLabel {
                    VStack(alignment: .leading, spacing: 1) {
                        Text("\(placement.step). \(placement.label)")
                            .font(.caption2.weight(.semibold))
                            .lineLimit(2)
                            .minimumScaleFactor(0.75)
                        if showsSize {
                            Text(
                                "\(centimetres(placement.size.x, decimals: 0)) × "
                                    + "\(centimetres(placement.size.z, decimals: 0))"
                            )
                            .font(.system(size: 9).monospacedDigit())
                            .foregroundStyle(.secondary)
                        }
                    }
                    .padding(4)
                } else {
                    Text("\(placement.step)")
                        .font(.system(size: 9).weight(.bold))
                        .padding(2)
                }
            }
            .frame(width: rect.width, height: rect.height)
            .offset(x: rect.minX, y: rect.minY)
            .accessibilityElement(children: .ignore)
            .accessibilityLabel(
                "Step \(placement.step), \(placement.label), "
                    + "\(centimetres(placement.size.x, decimals: 0)) by "
                    + "\(centimetres(placement.size.z, decimals: 0))"
            )
    }
}

/// An item from a lower layer that still occupies this height — outline only.
private struct ProtrusionRectangle: View {
    let rect: CGRect

    var body: some View {
        RoundedRectangle(cornerRadius: 4)
            .strokeBorder(
                Color.secondary.opacity(0.55),
                style: StrokeStyle(lineWidth: 1, dash: [4, 3])
            )
            .frame(width: rect.width, height: rect.height)
            .offset(x: rect.minX, y: rect.minY)
            .accessibilityHidden(true)
    }
}

// MARK: - Preview

#Preview("Demo carry-on — base layer") {
    if let plan = try? PlanLoader.mockPlan() {
        PlanDiagramView(plan: plan)
    } else {
        Text("Could not load the bundled mock plan.")
    }
}

/// The upper layer, where the shoes and dopp kit show through as dashed outlines.
#Preview("Demo carry-on — upper layer") {
    if let plan = try? PlanLoader.mockPlan() {
        PlanDiagramView(plan: plan, initialLayer: 1)
    } else {
        Text("Could not load the bundled mock plan.")
    }
}

/// **The stability banner.** The mock plan above is clean and stable by
/// assertion, so this is the only preview in which the banner appears — and the
/// geometry banner stays absent, which is the point.
#Preview("Unstable plan — stability banner") {
    PlanDiagramView(plan: unstableDemoPlan())
}
#endif

// MARK: - Preview fixture

/// A plan that is geometrically legal and still falls over: the laptop sleeve
/// hangs off the end of the jeans with its centre of mass past the part it rests
/// on, and the toiletry kit floats 5 cm above nothing at all. No box shares
/// volume with another and everything is inside the interior, so
/// `geometryIssues()` is empty — that is the point of the fixture.
///
/// The bundled mock plan is asserted to be clean *and* stable, so it can never
/// show the stability banner — the previews below need a plan that can. Lives
/// outside the SwiftUI guard so `swift test` on Linux can assert the fixture
/// really does trip stability and only stability.
func unstableDemoPlan() -> PackingPlan {
    let interior = Vector3(0.4064, 0.1524, 0.6096)
    func item(_ step: Int, _ id: String, _ label: String, at position: Vector3, size: Vector3, note: String) -> Placement {
        Placement(
            step: step,
            itemID: id,
            label: label,
            zone: "interior",
            position: position,
            size: size,
            rotation: .xyz,
            note: note
        )
    }
    return PackingPlan(
        version: 1,
        units: .meters,
        container: Container(
            id: "unstable-carry-on",
            label: "Unstable carry-on (16 x 6 x 24 in)",
            dimensions: interior,
            zones: [Zone(id: "interior", label: "Interior", origin: .zero, size: interior)]
        ),
        placements: [
            item(1, "jeans-folded", "Folded jeans",
                 at: Vector3(0, 0, 0), size: Vector3(0.20, 0.035, 0.30),
                 note: "Flat on the floor of the bag."),
            item(2, "laptop-sleeve", "Laptop sleeve",
                 at: Vector3(0.15, 0.035, 0.05), size: Vector3(0.20, 0.01, 0.10),
                 note: "Overhanging the jeans by three quarters of its width."),
            item(3, "toiletry-kit", "Toiletry kit",
                 at: Vector3(0.25, 0.05, 0.20), size: Vector3(0.10, 0.06, 0.15),
                 note: "Nothing under it: 5 cm of air down to the bag floor."),
        ]
    )
}
