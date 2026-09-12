import PackingPlan
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

    /// Used when the caller does not supply a binding.
    @State private var ownSelection: Int

    /// Set when an owner drives the selection — the plan sheet shares one layer
    /// index between the 2D and 3D views so toggling between them does not reset
    /// what you were looking at.
    private let externalSelection: Binding<Int>?

    /// Whichever of the two is in charge.
    private var selection: Binding<Int> { externalSelection ?? $ownSelection }

    /// - Parameter initialLayer: which layer to show first. Useful for previews
    ///   and for returning the user to the layer they were last working on.
    public init(plan: PackingPlan, initialLayer: Int = 0) {
        self.plan = plan
        self.layers = plan.layers()
        self._selection = State(initialValue: initialLayer)
        // Computed once: a plan does not change while it is on screen, and the
        // 2D view is exactly where a bad plan should become visible.
        self.issues = plan.geometryIssues()
    }

    private var currentLayer: PlanLayer? {
        layers.indices.contains(selection) ? layers[selection] : layers.first
    }

    public var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            header

            if !issues.isEmpty {
                issueBanner
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
                    + "\(plan.placements.count) items"
            )
            .font(.caption)
            .foregroundStyle(.secondary)
        }
    }

    private var issueBanner: some View {
        HStack(alignment: .top, spacing: 8) {
            Image(systemName: "exclamationmark.triangle.fill")
                .foregroundStyle(.orange)
            VStack(alignment: .leading, spacing: 2) {
                Text("\(issues.count) geometry \(issues.count == 1 ? "issue" : "issues")")
                    .font(.caption.weight(.semibold))
                ForEach(Array(issues.prefix(3).enumerated()), id: \.offset) { _, issue in
                    Text(issue.description)
                        .font(.caption2)
                        .foregroundStyle(.secondary)
                }
            }
        }
        .padding(8)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(.orange.opacity(0.12), in: RoundedRectangle(cornerRadius: 8))
    }

    private func layerCaption(for layer: PlanLayer) -> some View {
        Text(
            "Floor at \(centimetres(layer.floorY)) · "
                + "\(centimetres(layer.thickness)) thick · "
                + "\(layer.placements.count) \(layer.placements.count == 1 ? "item" : "items")"
        )
        .font(.caption)
        .foregroundStyle(.secondary)
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

// MARK: - Presentation helpers

extension Placement {
    /// Stable per-item colour. Driven by `step` so a plan always draws the same
    /// way, and spaced around the wheel so neighbours stay distinguishable.
    var diagramColor: Color {
        Color(hue: (Double(step) * 0.17).truncatingRemainder(dividingBy: 1.0),
              saturation: 0.62,
              brightness: 0.78)
    }
}

/// Metres are the only unit in the model layer; centimetres exist solely here,
/// at the moment a string is built for a person to read.
func centimetres(_ metres: Float, decimals: Int = 1) -> String {
    String(format: "%.\(decimals)f cm", metres * 100)
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
