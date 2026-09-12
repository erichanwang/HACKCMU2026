#if canImport(SwiftUI)
import PackingPlan
import SwiftUI

/// Interactive 3D view of the packed bag — the same plan `PlanDiagramView` draws
/// flat, seen instead as a box you can orbit around and step through in packing
/// order.
///
/// Every matrix lives behind `PackingPlan.projected(camera:size:upTo:)`. This
/// view owns three things and nothing else: the camera, the current step, and
/// how the projected polygons are painted. Faces are filled in the order the
/// projection returns them — back to front, container first — so there is no
/// depth sorting here either.
///
/// The packing *sequence* is the product, so stepping is the primary control:
/// step 0 is the empty bag, the item at the current step is drawn bright with
/// its label, earlier items are dimmed, and nothing past the current step is
/// drawn at all. Tapping an item selects it out of that sequence, and focusing
/// a selection puts the stepper on it so the two controls never disagree.
public struct PlanSceneView: View {
    /// Radians of orbit per point of drag. 100 points ≈ one radian: far enough
    /// round the bag in one comfortable swipe, short of feeling twitchy.
    private static let radiansPerPoint: Float = 0.01

    /// 1 is "fit the container"; below 0.6 the bag is a speck, above 4 the
    /// current item fills the canvas and there is nothing more to see.
    private static let zoomRange: ClosedRange<Float> = 0.6...4

    private let plan: PackingPlan
    private let placements: [Placement]

    @State private var camera: PlanCamera = .default
    @State private var step: Int

    /// `Placement.itemID` of the tapped item, or `nil` for nothing selected.
    /// The container is never selectable — tapping it is how you deselect.
    @State private var selectedID: String?

    /// "Show me just this": everything but the selection drops to a whisper.
    /// Only ever true with a selection, and only while the stepper agrees with
    /// it — stepping away turns it off rather than leaving a dark canvas.
    @State private var focused = false

    /// Gesture-local, so an interrupted drag or pinch snaps back on its own and
    /// only a completed one is committed to `camera`.
    @GestureState private var orbit: CGSize = .zero
    @GestureState private var pinch: CGFloat = 1

    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    /// - Parameter initialStep: which step to open on — 0 for the empty bag,
    ///   `plan.placements.count` for the fully packed one. Clamped to the plan.
    public init(plan: PackingPlan, initialStep: Int = 0) {
        self.plan = plan
        self.placements = plan.orderedPlacements
        self._step = State(initialValue: min(max(initialStep, 0), plan.placements.count))
    }

    private var current: Placement? {
        placements.first { $0.step == step }
    }

    private var selected: Placement? {
        selectedID.flatMap { id in placements.first { $0.itemID == id } }
    }

    /// The camera as it stands right now: the committed one plus whatever the
    /// in-flight gesture is adding.
    private var liveCamera: PlanCamera {
        adjusted(orbitedBy: orbit, zoomedBy: pinch)
    }

    public var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            header
            scene
            HStack(spacing: 12) {
                zoomSlider
                resetButton
            }
            stepControl
            caption
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
                    + "\(placements.count) items"
            )
            .font(.caption)
            .foregroundStyle(.secondary)
        }
    }

    // MARK: - Scene

    private var scene: some View {
        // The reader exists for the tap: hit-testing has to re-project into the
        // same rect the canvas drew into, and only layout knows how big that is.
        GeometryReader { proxy in
            Canvas { context, size in
                // Projected once per draw — never per box, never per face.
                let boxes = plan.projected(
                    camera: liveCamera,
                    size: SIMD2(Float(size.width), Float(size.height)),
                    upTo: step
                )
                for box in boxes {
                    draw(box, in: &context)
                }
                // After every fill, so a nearer already-packed item cannot paint
                // over the labels the view is meant to be about.
                context.opacity = 1
                for box in boxes where box.step != 0 && (box.step == step || box.id == selectedID) {
                    context.draw(
                        Text("\(box.step). \(box.label)").font(.caption2.weight(.semibold)),
                        at: CGPoint(x: CGFloat(box.center.x), y: CGFloat(box.center.y))
                    )
                }
            }
            .contentShape(Rectangle())
            .onTapGesture(count: 1, coordinateSpace: .local) { location in
                select(at: location, in: proxy.size)
            }
        }
        .aspectRatio(1, contentMode: .fit)
        .frame(maxWidth: .infinity)
        .background(
            Color.secondary.opacity(0.06),
            in: RoundedRectangle(cornerRadius: 6)
        )
        .contentShape(Rectangle())
        .gesture(orbitGesture.simultaneously(with: zoomGesture))
        .accessibilityElement(children: .ignore)
        .accessibilityLabel(sceneDescription)
    }

    private func draw(_ box: ProjectedBox, in context: inout GraphicsContext) {
        let isContainer = box.step == 0
        let isSelected = box.id == selectedID
        let isCurrent = !isContainer && box.step == step
        let tint = isContainer
            ? Color.secondary
            : (placements.first { $0.step == box.step }?.diagramColor ?? .accentColor)

        // The container reads as glass; already-packed items sit back so the
        // current one is unmistakable. Focus is the same idea turned up: the
        // rest of the bag stays as context and nothing else competes.
        if focused && !isSelected {
            context.opacity = isContainer ? 0.15 : 0.07
        } else {
            context.opacity = isContainer ? 0.3 : (isSelected || isCurrent ? 1 : 0.45)
        }

        for face in box.faces {
            guard let path = polygon(face.corners) else { continue }
            context.fill(path, with: .color(tint))
            // `shade` is a brightness and a `Color` cannot be dimmed in place,
            // so the shading is a second pass of black rather than a new colour.
            context.fill(path, with: .color(Color.black.opacity(Double(1 - face.shade) * 0.5)))
        }

        if let outline = polygon(box.outline) {
            context.stroke(
                outline,
                with: .color(isContainer ? .secondary : tint),
                lineWidth: isSelected ? 3 : (isCurrent ? 2 : 1)
            )
        }
    }

    /// Closed path through projected points. `nil` for anything that cannot be a
    /// polygon, which is how a degenerate face declines to be drawn.
    private func polygon(_ points: [SIMD2<Float>]) -> Path? {
        guard points.count >= 3 else { return nil }
        var path = Path()
        path.addLines(points.map { CGPoint(x: CGFloat($0.x), y: CGFloat($0.y)) })
        path.closeSubpath()
        return path
    }

    // MARK: - Selection

    /// Select the front-most item under `point`, or deselect if that is the bag
    /// or empty space.
    ///
    /// The test is point-in-polygon over the visible faces, not the silhouette's
    /// bounding rect: boxes in a packed bag overlap heavily, and a rect test
    /// picks whichever one happens to be *near* the tap rather than under it.
    private func select(at point: CGPoint, in size: CGSize) {
        let boxes = plan.projected(
            camera: liveCamera,
            size: SIMD2(Float(size.width), Float(size.height)),
            upTo: step
        )
        let target = SIMD2(Float(point.x), Float(point.y))
        // Back to front is the draw order, so the front-most hit is the last one
        // — walk it in reverse and stop at the first.
        selectedID = boxes.reversed().first { box in
            box.step != 0 && box.faces.contains { contains($0.corners, target) }
        }?.id
        // A new selection is not a focused one: the button says what it does.
        focused = false
    }

    /// Ray-crossing test in screen space, y down. The parity of the crossings to
    /// one side of the point decides it, so it is right for any simple polygon
    /// and does not care which way the ring winds.
    private func contains(_ corners: [SIMD2<Float>], _ point: SIMD2<Float>) -> Bool {
        guard corners.count >= 3 else { return false }
        var inside = false
        var j = corners.count - 1
        for i in corners.indices {
            let a = corners[i]
            let b = corners[j]
            // The straddle test guarantees `b.y != a.y`, so the divide is safe.
            if (a.y > point.y) != (b.y > point.y),
               point.x < (b.x - a.x) * (point.y - a.y) / (b.y - a.y) + a.x {
                inside.toggle()
            }
            j = i
        }
        return inside
    }

    /// Focus, or drop focus. Focusing moves the stepper onto the item: the two
    /// controls describe one thing, so they had better not disagree about it.
    private func focus(on placement: Placement) {
        guard !focused else {
            focused = false
            return
        }
        step = placement.step
        focused = true
    }

    // MARK: - Camera

    private func adjusted(orbitedBy translation: CGSize, zoomedBy magnification: CGFloat) -> PlanCamera {
        // `PlanCamera.init` already clamps pitch away from straight-down, so
        // the only limit worth repeating here is the zoom range the slider uses.
        PlanCamera(
            yaw: camera.yaw + Float(translation.width) * Self.radiansPerPoint,
            pitch: camera.pitch - Float(translation.height) * Self.radiansPerPoint,
            zoom: min(max(camera.zoom * Float(magnification), Self.zoomRange.lowerBound),
                      Self.zoomRange.upperBound)
        )
    }

    private var orbitGesture: some Gesture {
        DragGesture(minimumDistance: 1)
            .updating($orbit) { value, state, _ in state = value.translation }
            .onEnded { value in
                camera = adjusted(orbitedBy: value.translation, zoomedBy: 1)
            }
    }

    private var zoomGesture: some Gesture {
        MagnifyGesture()
            .updating($pinch) { value, state, _ in state = value.magnification }
            .onEnded { value in
                camera = adjusted(orbitedBy: .zero, zoomedBy: value.magnification)
            }
    }

    /// Pinching is the natural gesture; the slider is the one that works with a
    /// keyboard, with VoiceOver, and in a preview canvas.
    private var zoomSlider: some View {
        Slider(
            value: $camera.zoom,
            in: Self.zoomRange
        ) {
            Text("Zoom")
        } minimumValueLabel: {
            Image(systemName: "minus.magnifyingglass")
        } maximumValueLabel: {
            Image(systemName: "plus.magnifyingglass")
        }
        .font(.caption)
        .foregroundStyle(.secondary)
    }

    /// Free orbit means getting lost, and there is no gesture for "back to where
    /// I started".
    private var resetButton: some View {
        Button {
            camera = .default
        } label: {
            Label("Reset view", systemImage: "arrow.counterclockwise")
                .labelStyle(.iconOnly)
        }
        .buttonStyle(.bordered)
        .controlSize(.small)
        .accessibilityLabel("Reset view")
    }

    // MARK: - Stepping

    private var stepControl: some View {
        // Stepping is a disagreement with focus, so it ends it. The selection
        // survives — it is still what the caption is talking about — unless the
        // step it belongs to has been stepped back past, in which case the
        // canvas is no longer drawing it and the caption must not either.
        let binding = Binding(get: { step }) { newStep in
            step = newStep
            focused = false
            if let placement = selected, placement.step > newStep {
                selectedID = nil
            }
        }
        return Stepper(value: binding, in: 0...placements.count) {
            Text(step == 0 ? "Empty bag" : "Step \(step) of \(placements.count)")
                .font(.subheadline.weight(.medium))
                .monospacedDigit()
        }
    }

    private var caption: some View {
        Group {
            if let placement = selected {
                selectionCard(placement)
            } else if let placement = current {
                HStack(alignment: .firstTextBaseline, spacing: 8) {
                    stepBadge(placement)
                    VStack(alignment: .leading, spacing: 1) {
                        Text(placement.label).font(.caption.weight(.medium))
                        Text(placement.note)
                            .font(.caption2)
                            .foregroundStyle(.secondary)
                    }
                }
            } else {
                Text("Empty bag · step forward to place the first item.")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }

            Text("Tap an item to select · drag to orbit · pinch or the slider to zoom")
                .font(.caption2)
                .foregroundStyle(.tertiary)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        // The projection is recomputed rather than interpolated, so this
        // crossfade is the only motion in the view — and it is the only thing
        // Reduce Motion has to turn off.
        .animation(reduceMotion ? nil : .easeInOut(duration: 0.2), value: step)
    }

    /// The tapped item, with the two things the stepper never showed: how big it
    /// is, and a way to see it on its own.
    private func selectionCard(_ placement: Placement) -> some View {
        HStack(alignment: .firstTextBaseline, spacing: 8) {
            stepBadge(placement)
            VStack(alignment: .leading, spacing: 1) {
                Text(placement.label).font(.caption.weight(.medium))
                Text(
                    "\(centimetres(placement.size.x, decimals: 0)) × "
                        + "\(centimetres(placement.size.y, decimals: 0)) × "
                        + "\(centimetres(placement.size.z, decimals: 0))"
                )
                .font(.caption2.monospacedDigit())
                .foregroundStyle(.secondary)
                Text(placement.note)
                    .font(.caption2)
                    .foregroundStyle(.secondary)
            }
            Spacer(minLength: 8)
            VStack(alignment: .trailing, spacing: 4) {
                Button(focused ? "Show all" : "Show only this") { focus(on: placement) }
                Button("Deselect") {
                    selectedID = nil
                    focused = false
                }
            }
            .font(.caption2)
            .buttonStyle(.bordered)
            .controlSize(.small)
        }
    }

    private func stepBadge(_ placement: Placement) -> some View {
        Text("\(placement.step)")
            .font(.caption2.monospacedDigit().weight(.bold))
            .foregroundStyle(.white)
            .frame(width: 18, height: 18)
            .background(placement.diagramColor, in: Circle())
    }

    /// The canvas is opaque to VoiceOver, so it carries the step in words.
    private var sceneDescription: String {
        if let placement = selected {
            return "Selected: \(placement.label), step \(placement.step) of \(placements.count), "
                + "\(centimetres(placement.size.x, decimals: 0)) by "
                + "\(centimetres(placement.size.y, decimals: 0)) by "
                + "\(centimetres(placement.size.z, decimals: 0)). \(placement.note)"
                + (focused ? " Shown on its own." : "")
        }
        guard let placement = current else {
            return "Empty \(plan.container.label), seen in 3D."
        }
        return "Step \(placement.step) of \(placements.count): \(placement.label), "
            + "\(centimetres(placement.size.x, decimals: 0)) by "
            + "\(centimetres(placement.size.y, decimals: 0)) by "
            + "\(centimetres(placement.size.z, decimals: 0)). \(placement.note)"
    }
}

// MARK: - Preview

#Preview("Demo carry-on — empty bag") {
    if let plan = try? PlanLoader.mockPlan() {
        PlanSceneView(plan: plan)
    } else {
        Text("Could not load the bundled mock plan.")
    }
}

/// The end of the sequence: every item in, the last one still highlighted — and
/// the one preview where there is something to tap.
#Preview("Demo carry-on — fully packed") {
    if let plan = try? PlanLoader.mockPlan() {
        PlanSceneView(plan: plan, initialStep: plan.placements.count)
    } else {
        Text("Could not load the bundled mock plan.")
    }
}
#endif  // canImport(SwiftUI) -- SwiftUI is Apple-only; the view vanishes on Linux so `swift test` can run
