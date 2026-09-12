// Linux has no CoreGraphics, but its Foundation provides CGFloat with the same
// API — the same fallback FootprintProjection uses, and for the same reason:
// `planDiagramHeight` at the bottom of this file is deliberately outside the
// SwiftUI guard so the layout claim it encodes can be asserted on Linux.
#if canImport(CoreGraphics)
import CoreGraphics
#else
import Foundation
#endif
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
    private let nesting: PlanNesting

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
        self.init(plan: plan, initialLayer: initialLayer, externalSelection: nil)
    }

    /// - Parameter selectedLayer: a layer index owned by the caller, so it can be
    ///   shared with another view of the same plan.
    public init(plan: PackingPlan, selectedLayer: Binding<Int>) {
        self.init(plan: plan, initialLayer: selectedLayer.wrappedValue, externalSelection: selectedLayer)
    }

    private init(plan: PackingPlan, initialLayer: Int, externalSelection: Binding<Int>?) {
        self.plan = plan
        self.layers = plan.layers()
        self._ownSelection = State(initialValue: initialLayer)
        self.externalSelection = externalSelection
        // Computed once: a plan does not change while it is on screen, and the
        // 2D view is exactly where a bad plan should become visible.
        self.issues = plan.geometryIssues()
        self.stability = plan.stabilityIssues()
        self.stats = PlanStats(plan: plan)
        self.nesting = PlanNesting(plan: plan, layers: self.layers)
    }

    private var currentLayer: PlanLayer? {
        let index = selection.wrappedValue
        return layers.indices.contains(index) ? layers[index] : layers.first
    }

    /// Header, layer picker and layer caption stay put; everything below them
    /// scrolls.
    ///
    /// The footprint is drawn to scale, so the diagram's height follows from the
    /// screen's width: 537 pt for the demo carry-on's portrait footprint at the
    /// 358 pt of content width a 390 pt phone leaves. Add the header, the picker,
    /// the caption, a legend line per item and a banner and the content is taller
    /// than the safe area — which a plain `VStack` resolves by compressing
    /// whatever gives most, i.e. the diagram, the one thing a person holding the
    /// phone over an open bag is actually reading. So the diagram is given a
    /// height that cannot see how much room is left (`planDiagramHeight`, a
    /// function of width alone) and the overflow goes into a `ScrollView`.
    ///
    /// The picker sits *above* that scroll view rather than inside it: choosing a
    /// layer is the other thing you do while packing, and it must not be possible
    /// to scroll it out of reach. The banners, the legend and the details
    /// disclosure are reference material, and are exactly what should scroll.
    public var body: some View {
        GeometryReader { screen in
            VStack(alignment: .leading, spacing: 14) {
                header

                if layers.count > 1 {
                    // `selection` rather than `$selection`: it is the shared layer
                    // index, so the picker, the 3D slider and the AR overlay all
                    // drive the same value.
                    Picker("Layer", selection: selection) {
                        ForEach(layers) { layer in
                            Text("Layer \(layer.index + 1)").tag(layer.index)
                        }
                    }
                    .pickerStyle(.segmented)
                }

                if let layer = currentLayer {
                    layerCaption(for: layer)
                }

                ScrollView {
                    VStack(alignment: .leading, spacing: 14) {
                        if !issues.isEmpty {
                            PlanIssueBanner(kind: .geometry, issues: issues)
                        }

                        if !stability.isEmpty {
                            PlanIssueBanner(kind: .stability, issues: stability)
                        }

                        if let layer = currentLayer {
                            diagram(
                                for: layer,
                                height: planDiagramHeight(
                                    footprint: plan.container.dimensions,
                                    width: screen.size.width - 2 * planDiagramPadding
                                )
                            )
                            legend(for: layer)
                        } else {
                            Text("This plan has no placements.")
                                .foregroundStyle(.secondary)
                        }

                        details
                    }
                    .frame(maxWidth: .infinity, alignment: .leading)
                }
            }
            .padding(planDiagramPadding)
        }
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

    private func diagram(for layer: PlanLayer, height: CGFloat) -> some View {
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
                        rect: projection.rect(for: placement),
                        hostLabel: nesting.nest(of: placement.itemID)?.host.label
                    )
                }

                // Nests hosted in this layer whose guest is drawn in another one.
                // Last, so the outline lands on top of the host it sits inside.
                ForEach(nesting.guests(hostedIn: layer)) { nest in
                    NestedGuestOutline(
                        placement: nest.item,
                        rect: projection.rect(for: nest.item)
                    )
                }
            }
        }
        .frame(maxWidth: .infinity)
        // Not `.aspectRatio(.fit)`: that is flexible, and a flexible height is
        // exactly what an over-full VStack — or a ScrollView proposing no height
        // at all — takes back out of the diagram.
        .frame(height: height)
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
                        if let nest = nesting.nest(of: placement.itemID) {
                            Text(
                                "Nested inside \(nest.host.label)"
                                    + (nest.isSplit ? " · host drawn on layer \(nest.hostLayer + 1)" : "")
                            )
                            .font(.caption2.italic())
                            .foregroundStyle(.secondary)
                        }
                    }
                }
            }

            // The other half of a split nest. Without this the host's layer gives
            // no sign that something goes inside the item you are looking at.
            ForEach(nesting.guests(hostedIn: layer)) { nest in
                Text(
                    "Dotted: \(nest.item.label) goes inside \(nest.host.label) here, "
                        + "and is drawn on layer \(nest.itemLayer + 1)."
                )
                .font(.caption2)
                .foregroundStyle(.secondary)
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

            // Traveller-facing, not frame-facing: "X across, Z down" is true and useless to
            // someone lining a real bag up with the picture. The long side running down the
            // screen follows from Z being depth, which is the bag's longer axis.
            Text("Seen from above · lay the bag with its long side running down the screen")
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

        /// The leading word carries the verdict on its own: orange and yellow are close
        /// together on a phone in daylight, and the glyph only reinforces what the word says.
        func title(count: Int) -> String {
            switch self {
            case .geometry:
                return "Wrong: \(count) geometry \(count == 1 ? "issue" : "issues") — "
                    + "this plan cannot be packed as drawn"
            case .stability:
                return "Unstable: \(count) stability \(count == 1 ? "warning" : "warnings") — "
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

    /// Label of the item whose scanned cavity holds this one, when the plan says
    /// so and `honouredNesting()` believed it. A small rectangle drawn inside a
    /// bigger one is otherwise indistinguishable from two items sharing volume —
    /// that is, from a bug — so a nested item names its host.
    let hostLabel: String?

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
            .overlay {
                // Inner dotted border: this box is in someone's cavity, not on top
                // of them. It reads at any size, including the sizes too small for
                // the "in <host>" line below.
                if hostLabel != nil {
                    RoundedRectangle(cornerRadius: 2)
                        .strokeBorder(
                            placement.diagramColor,
                            style: StrokeStyle(lineWidth: 1, dash: [2, 2])
                        )
                        .padding(3)
                }
            }
            .overlay(alignment: .topLeading) {
                if showsLabel {
                    VStack(alignment: .leading, spacing: 1) {
                        Text("\(placement.step). \(placement.label)")
                            .font(.caption2.weight(.semibold))
                            .lineLimit(2)
                            .minimumScaleFactor(0.75)
                        if let hostLabel {
                            Text("in \(hostLabel)")
                                .font(.system(size: 9).italic())
                                .lineLimit(1)
                                .minimumScaleFactor(0.7)
                                .foregroundStyle(.secondary)
                        }
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
                    + (hostLabel.map { ", nested inside \($0)" } ?? "")
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

/// A nested item whose host is in this layer but whose own floor is a layer up:
/// its footprint, dotted, inside the host it goes into.
///
/// Layers group by floor height, so the socks stuffed into a shoe sit 3.5 cm up
/// and land in the layer above the shoe. Without this the two halves of a nest
/// appear in two separate pictures with nothing tying them together: the host's
/// layer shows no sign anything goes inside it, and the guest's layer shows an
/// item apparently alone in mid-air.
///
/// Dotted and in the item's own colour, against the protrusion outline's grey
/// dash — one means "comes up through this floor from below", the other "drops
/// into the item under it". The legend sentence says which either way.
private struct NestedGuestOutline: View {
    let placement: Placement
    let rect: CGRect

    var body: some View {
        RoundedRectangle(cornerRadius: 3)
            .strokeBorder(
                placement.diagramColor,
                style: StrokeStyle(lineWidth: 1.5, dash: [2, 3])
            )
            .frame(width: rect.width, height: rect.height)
            .offset(x: rect.minX, y: rect.minY)
            // Hidden for the same reason the protrusion outline is: the legend
            // sentence under the diagram is the accessible version of it.
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

/// **The nesting affordance, host side.** The socks are drawn a layer up, and this
/// layer shows their footprint dotted inside the shoes with a legend line naming
/// where they went. The passport pouch names a host the plan does not contain, so
/// it gets the geometry banner and no affordance at all — deliberately.
#Preview("Nested plan — host layer") {
    PlanDiagramView(plan: nestedDemoPlan())
}

/// **The nesting affordance, guest side.** The socks' own layer: solid rectangle
/// with an inner dotted border and "in Running shoes", rather than a small box
/// alone in the corner of an otherwise empty layer.
#Preview("Nested plan — guest layer") {
    PlanDiagramView(plan: nestedDemoPlan(), initialLayer: 1)
}
#endif

// MARK: - Layout

// Outside the SwiftUI guard: on Linux the view cannot be built at all, and these
// two numbers are the whole of the claim that the diagram survives a phone
// screen. Argued layout is layout nobody can check.

/// Side and top padding around the whole diagram screen. Explicit rather than
/// `.padding()`'s platform default because `planDiagramHeight` is fed
/// `width - 2 * this`, and a guessed inset would be a guessed diagram.
let planDiagramPadding: CGFloat = 16

/// Height the top-down diagram needs at a given content width, in points.
///
/// The footprint is drawn to scale, so its height is its width times the bag's
/// depth-over-width ratio: the demo carry-on (0.4064 × 0.6096 m) wants 537 pt at
/// the 358 pt a 390 pt phone leaves after padding, which is most of a safe area
/// on its own.
///
/// **A function of width alone, on purpose.** The bug this fixes was SwiftUI
/// resolving an over-full `VStack` by squeezing the most compressible child, and
/// the diagram was it. A height that cannot see how much room is left cannot be
/// negotiated down.
///
/// The cost of that is a wide screen: at 900 pt of width this asks for 1350 pt of
/// height and the picture has to be scrolled to be seen whole. Worth it on the
/// phone the demo runs on, where the alternative was a squashed bag — if an iPad
/// layout ever matters, cap the width fed in here rather than the height out of it,
/// so the footprint stays to scale.
func planDiagramHeight(footprint: Vector3, width: CGFloat) -> CGFloat {
    guard footprint.x > 0, footprint.z > 0, width > 0 else { return 0 }
    return width * CGFloat(footprint.z / footprint.x)
}

// MARK: - Nesting

/// Where the two halves of a nest are drawn, for the 2D layer diagram.
///
/// `PackingPlan.honouredNesting()` is the authority on *whether* a `nestedIn`
/// claim counts — it drops dangling hosts, self-references and cycles, and none
/// of that is re-derived here. This only answers *where*: which layer picture
/// holds the nested item and which holds its host, because layers group by floor
/// height and a nested item's floor is usually not its host's.
///
/// Outside the SwiftUI guard so `swift test` can check it on Linux.
struct PlanNesting {
    /// One honoured nest, with the layer each half is drawn in.
    struct Nest: Identifiable {
        /// The nested item — the one inside the cavity.
        let item: Placement

        /// The item whose cavity holds it.
        let host: Placement

        /// Layer `item` is drawn in.
        let itemLayer: Int

        /// Layer `host` is drawn in.
        let hostLayer: Int

        var id: String { item.itemID }

        /// True when the pair lands in two different layer pictures — the case
        /// that needs the two halves cross-referenced.
        var isSplit: Bool { itemLayer != hostLayer }
    }

    /// Keyed by the nested item's ID, the same key `honouredNesting()` uses.
    private let nests: [String: Nest]

    init(plan: PackingPlan, layers: [PlanLayer]) {
        let byID = Dictionary(plan.placements.map { ($0.itemID, $0) }, uniquingKeysWith: { a, _ in a })
        let layerOf = Dictionary(
            layers.flatMap { layer in layer.placements.map { ($0.itemID, layer.index) } },
            uniquingKeysWith: { a, _ in a }
        )

        nests = plan.honouredNesting().reduce(into: [:]) { out, entry in
            guard let item = byID[entry.key],
                  let host = byID[entry.value.itemID],
                  let itemLayer = layerOf[entry.key],
                  let hostLayer = layerOf[entry.value.itemID]
            else { return }
            out[entry.key] = Nest(item: item, host: host, itemLayer: itemLayer, hostLayer: hostLayer)
        }
    }

    /// The nest this item is the guest of, or `nil` if it is not nested.
    func nest(of itemID: String) -> Nest? { nests[itemID] }

    /// Nests whose **host** is in this layer but whose guest is drawn elsewhere —
    /// the halves this layer's picture would otherwise say nothing about.
    func guests(hostedIn layer: PlanLayer) -> [Nest] {
        nests.values
            .filter { $0.hostLayer == layer.index && $0.isSplit }
            .sorted { $0.item.step < $1.item.step }
    }
}

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

/// A plan with a **split nest**: the socks sit in the shoes' cavity 3 cm off the
/// bag floor, so `layers()` draws them one layer above the shoes they are inside.
/// That is the case the 2D diagram used to lose — two pictures, no connection —
/// and the case the dotted outline and its legend line exist for.
///
/// It also carries a dangling nest: the passport pouch claims a host the plan does
/// not contain. `honouredNesting()` drops it, so the view must give it no nesting
/// affordance at all; `geometryIssues()` still reports it, so the preview shows the
/// geometry banner. Both halves of that are the point — a bad claim must look like
/// a bug, not like a feature.
///
/// Outside the SwiftUI guard, like `unstableDemoPlan()` above, so `swift test` can
/// assert what it contains.
func nestedDemoPlan() -> PackingPlan {
    let interior = Vector3(0.4064, 0.1524, 0.6096)
    func item(
        _ step: Int, _ id: String, _ label: String,
        at position: Vector3, size: Vector3, note: String, nestedIn: Nesting? = nil
    ) -> Placement {
        Placement(
            step: step,
            itemID: id,
            label: label,
            zone: "interior",
            position: position,
            size: size,
            rotation: .xyz,
            note: note,
            nestedIn: nestedIn
        )
    }
    return PackingPlan(
        version: 1,
        units: .meters,
        container: Container(
            id: "nested-carry-on",
            label: "Nested carry-on (16 x 6 x 24 in)",
            dimensions: interior,
            zones: [Zone(id: "interior", label: "Interior", origin: .zero, size: interior)]
        ),
        placements: [
            item(1, "shoes-pair", "Running shoes",
                 at: Vector3(0, 0, 0), size: Vector3(0.30, 0.115, 0.15),
                 note: "Soles down in the end well. Stuff the socks inside."),
            item(2, "sweater-roll", "Rolled sweater",
                 at: Vector3(0.02, 0, 0.20), size: Vector3(0.36, 0.07, 0.115),
                 note: "Rolled along the width, flat on the bag floor."),
            // Floor at 3 cm — the cavity's own floor, so it is supported rather
            // than floating, and a layer above the shoes it is inside.
            item(3, "socks-pair", "Socks",
                 at: Vector3(0.03, 0.03, 0.03), size: Vector3(0.08, 0.05, 0.08),
                 note: "Balled up and pushed into the left shoe.",
                 nestedIn: Nesting(
                     itemID: "shoes-pair",
                     cavity: Nesting.Cavity(
                         position: Vector3(0.02, 0.03, 0.02),
                         size: Vector3(0.10, 0.06, 0.10)
                     )
                 )),
            item(4, "passport-pouch", "Passport pouch",
                 at: Vector3(0.05, 0.07, 0.21), size: Vector3(0.12, 0.02, 0.08),
                 note: "Flat on the sweater; its nestedIn names an item that is not here.",
                 nestedIn: Nesting(
                     itemID: "ghost-liner",
                     cavity: Nesting.Cavity(
                         position: Vector3(0.05, 0.07, 0.21),
                         size: Vector3(0.12, 0.02, 0.08)
                     )
                 )),
        ]
    )
}
