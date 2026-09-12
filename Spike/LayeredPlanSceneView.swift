import PackingPlan
import PackingPlanUI
import RealityKit
import SwiftUI

/// Thickness of the container's wireframe edges, in metres.
private let wireThickness: Float = 0.004
/// Height of the item labels, in metres. Small enough that stacked items in a
/// shallow bag do not overprint each other.
private let labelSize: CGFloat = 0.014
/// How far the camera starts back, as a multiple of the container's diagonal.
/// Framed against the narrow axis of a portrait viewport, not the wide one.
private let startDistanceFactor: Float = 2.2
/// Keeps the orbit from tipping over the poles.
private let maxPitch: Float = 1.4

@MainActor
final class PlanSceneController: ObservableObject {
    let arView = ARView(frame: .zero, cameraMode: .nonAR, automaticallyConfigureSession: false)

    /// Number of layers the plan groups into — the slider's range.
    let layerCount: Int

    private let camera = PerspectiveCamera()
    private let target: SIMD3<Float>
    /// The shared placement entities.
    private var content: PlanEntityBuilder.Content!
    /// Label pivots, re-oriented to face the camera whenever it moves.
    private var labels: [Entity] = []

    private var yaw: Float = .pi / 5
    private var pitch: Float = .pi / 7
    private var distance: Float

    init(plan: PackingPlan, scans: [String: ScannedItem]) {
        let dimensions = plan.container.dimensions
        target = (dimensions / 2).simd
        distance = simd_length(dimensions.simd) * startDistanceFactor

        let layers = plan.layers()
        layerCount = max(layers.count, 1)

        arView.environment.background = .color(.secondarySystemBackground)

        let root = AnchorEntity(world: .zero)
        root.addChild(PlanEntityBuilder.wireframeBox(size: dimensions))

        // Same boxes the AR overlay draws — one builder, two hosts.
        let built = PlanEntityBuilder(plan: plan, scans: scans).build(includeLabels: true)
        root.addChild(built.root)
        content = built
        labels = built.labels

        let cameraAnchor = AnchorEntity(world: .zero)
        cameraAnchor.addChild(camera)
        arView.scene.addAnchor(cameraAnchor)
        arView.scene.addAnchor(root)

        updateCamera()
    }

    // MARK: - Camera

    func orbit(by delta: CGSize) {
        yaw -= Float(delta.width) * 0.01
        pitch = min(maxPitch, max(-maxPitch, pitch + Float(delta.height) * 0.01))
        updateCamera()
    }

    func zoom(by scale: CGFloat) {
        let span = max(target.x, target.y, target.z) * 2
        distance = min(span * 6, max(span * 0.4, distance / Float(scale)))
        updateCamera()
    }

    private func updateCamera() {
        let offset = SIMD3<Float>(
            distance * cos(pitch) * sin(yaw),
            distance * sin(pitch),
            distance * cos(pitch) * cos(yaw)
        )
        camera.look(at: target, from: target + offset, relativeTo: nil)

        // RealityKit has no billboard component on iOS 17, and the camera only
        // moves on a gesture, so turning the labels here is enough.
        let facing = camera.orientation(relativeTo: nil)
        for label in labels { label.orientation = facing }
    }

    // MARK: - Layers

    /// Peels the suitcase apart, delegating to the shared content.
    func show(upTo topLayer: Int) {
        content?.show(upTo: topLayer)
    }

}



private struct PlanSceneContainer: UIViewRepresentable {
    let controller: PlanSceneController

    func makeUIView(context: Context) -> ARView { controller.arView }
    func updateUIView(_ uiView: ARView, context: Context) {}
}

/// A packing plan as a rotatable 3D scene: container wireframe, one translucent
/// labelled box per placement, and a slider that peels the layers apart.
/// Non-AR — runs in the simulator with no camera.
struct PlanSceneView: View {
    @StateObject private var controller: PlanSceneController
    /// Used when the caller does not supply a binding.
    @State private var ownTopLayer: Int
    /// Set when an owner drives the selection — see `PlanSheet`.
    private let externalTopLayer: Binding<Int>?
    @State private var lastDrag: CGSize = .zero

    private let plan: PackingPlan

    /// The topmost visible layer, from whichever source is in charge, clamped to
    /// what this plan actually has.
    private var topLayer: Int {
        let raw = externalTopLayer?.wrappedValue ?? ownTopLayer
        return min(max(raw, 0), controller.layerCount - 1)
    }

    /// The slider works in `Double`; the shared index is an `Int`.
    private var sliderBinding: Binding<Double> {
        Binding(
            get: { Double(topLayer) },
            set: { newValue in
                let clamped = min(max(Int(newValue), 0), controller.layerCount - 1)
                if let externalTopLayer { externalTopLayer.wrappedValue = clamped }
                else { ownTopLayer = clamped }
            }
        )
    }

    /// - Parameter scans: LiDAR scans by item id. A placement whose id matches one
    ///   is drawn as the scanned surface; everything else falls back to its box.
    ///   This assumes the solver's `item_id` is the scanner's `id`, which is what
    ///   the server contract in SCAN_OUTPUT.md produces.
    ///   - topLayer: a layer index owned by the caller, so it can be shared with
    ///     the other views of the same plan. When omitted the view keeps its own
    ///     and starts with every layer visible.
    init(plan: PackingPlan, scans: [String: ScannedItem] = [:], topLayer: Binding<Int>? = nil) {
        self.plan = plan
        let controller = PlanSceneController(plan: plan, scans: scans)
        _controller = StateObject(wrappedValue: controller)
        externalTopLayer = topLayer
        _ownTopLayer = State(initialValue: controller.layerCount - 1)
    }

    var body: some View {
        ZStack(alignment: .bottom) {
            PlanSceneContainer(controller: controller)
                .ignoresSafeArea()
                .gesture(
                    DragGesture()
                        .onChanged { value in
                            let delta = CGSize(
                                width: value.translation.width - lastDrag.width,
                                height: value.translation.height - lastDrag.height
                            )
                            lastDrag = value.translation
                            controller.orbit(by: delta)
                        }
                        .onEnded { _ in lastDrag = .zero }
                )
                .simultaneousGesture(
                    MagnifyGesture()
                        .onChanged { controller.zoom(by: $0.magnification) }
                )

            controls
        }
        .navigationTitle("Plan scene")
        .navigationBarTitleDisplayMode(.inline)
        // onChange does not fire for the initial value, so the starting position
        // has to be pushed in explicitly — and again whenever the shared index
        // moves, including while this view is off screen.
        .onAppear { controller.show(upTo: topLayer) }
        .onChange(of: topLayer) { _, value in controller.show(upTo: value) }
    }

    private var controls: some View {
        VStack(spacing: 6) {
            Text(containerCaption)
                .font(.caption2)
                .foregroundStyle(.secondary)
            Text(layerCaption)
                .font(.system(.footnote, design: .monospaced))
            if controller.layerCount > 1 {
                Slider(
                    value: sliderBinding,
                    in: 0...Double(controller.layerCount - 1),
                    step: 1
                )
            }
            Text("Drag to orbit · pinch to zoom")
                .font(.caption2)
                .foregroundStyle(.secondary)
        }
        .padding()
        .background(.regularMaterial, in: RoundedRectangle(cornerRadius: 12))
        .padding()
    }

    private var containerCaption: String {
        let size = plan.container.dimensions
        return String(
            format: "%@ · %.0f × %.0f × %.0f cm",
            plan.container.label, size.x * 100, size.y * 100, size.z * 100
        )
    }

    private var layerCaption: String {
        let shown = topLayer + 1
        let items = plan.layers().prefix(shown).reduce(0) { $0 + $1.placements.count }
        return "Layers 1–\(shown) of \(controller.layerCount) · \(items) items"
    }
}

/// One plan, shown flat, in 3D, or anchored to the real bag.
///
/// The plan is handed in rather than fetched: whoever presents this sheet owns
/// the fetch, so a single request feeds every view and switching between them
/// costs nothing.
struct PlanSheet: View {
    let plan: PackingPlan
    /// Set when the plan on screen is not the one the server produced.
    var notice: String?
    var scans: [String: ScannedItem] = [:]
    /// Raised while the AR overlay is up, so whoever presented this sheet can
    /// stand its own camera down. iOS runs one ARSession at a time: a second
    /// `run()` takes the camera from the first, which then never recovers.
    var arActive: Binding<Bool>?

    /// The two ways of drawing the same diagram. AR is deliberately not a third
    /// case here — it is a different mode, not another rendering, and it gets its
    /// own control.
    enum DiagramMode {
        case flat, scene

        var title: String { self == .flat ? "2D" : "3D" }
        var other: DiagramMode { self == .flat ? .scene : .flat }
    }

    @State private var diagram: DiagramMode = .flat
    @State private var showingAR = false
    /// Shared by all three views: the 2D picker shows layer `n`, the 3D slider
    /// and the AR overlay peel down to layer `n`, so switching lands you where
    /// you were rather than resetting.
    @State private var selectedLayer: Int

    init(
        plan: PackingPlan,
        notice: String? = nil,
        scans: [String: ScannedItem] = [:],
        arActive: Binding<Bool>? = nil
    ) {
        self.plan = plan
        self.notice = notice
        self.scans = scans
        self.arActive = arActive
        // Start on the top layer: in 3D and AR that means the whole bag is
        // visible, which is the useful overview. Starting at 0 would open them
        // with everything above the floor layer hidden, which reads as broken.
        _selectedLayer = State(initialValue: max(plan.layers().count - 1, 0))
    }

    var body: some View {
        VStack(spacing: 0) {
            controls

            if let notice {
                noticeBanner(notice)
            }

            switch diagram {
            case .flat:
                PlanDiagramView(plan: plan, selectedLayer: $selectedLayer)
            case .scene:
                PlanSceneView(plan: plan, scans: scans, topLayer: $selectedLayer)
            }
        }
        .fullScreenCover(isPresented: $showingAR) {
            PlanARPlanView(plan: plan, scans: scans, topLayer: $selectedLayer)
        }
        .onChange(of: showingAR) { _, active in arActive?.wrappedValue = active }
        .onDisappear { arActive?.wrappedValue = false }
    }

    private var controls: some View {
        HStack {
            // One control: tapping it flips between the two diagrams.
            Button {
                diagram = diagram.other
            } label: {
                Label(diagram.title, systemImage: "arrow.triangle.2.circlepath")
                    .font(.body.monospacedDigit())
            }
            .buttonStyle(.bordered)
            .accessibilityHint("Switches to \(diagram.other.title)")

            Spacer()

            Button {
                showingAR = true
            } label: {
                Label("AR", systemImage: "arkit")
            }
            .buttonStyle(.borderedProminent)
            .disabled(!PlanARPlanView.isSupported)
        }
        .padding(.horizontal)
        .padding(.top)
    }

    private func noticeBanner(_ text: String) -> some View {
        HStack(alignment: .top, spacing: 8) {
            Image(systemName: "exclamationmark.triangle.fill")
                .foregroundStyle(.orange)
            VStack(alignment: .leading, spacing: 2) {
                Text("Showing the bundled mock plan")
                    .font(.caption.weight(.semibold))
                Text(text)
                    .font(.caption2)
                    .foregroundStyle(.secondary)
                    .textSelection(.enabled)
                Text(API.base.absoluteString)
                    .font(.caption2.monospaced())
                    .foregroundStyle(.tertiary)
                    .textSelection(.enabled)
            }
        }
        .padding(10)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(.orange.opacity(0.12), in: RoundedRectangle(cornerRadius: 12))
        .padding([.horizontal, .top])
    }
}

/// The bundled mock, for looking at the views without a server or a scan.
struct MockPlanScreen: View {
    var body: some View {
        if let plan = PlanFallback.mockPlan() {
            PlanSheet(plan: plan)
        } else {
            ContentUnavailableView(
                "No plan",
                systemImage: "shippingbox",
                description: Text("The bundled mock plan could not be loaded.")
            )
        }
    }
}

/// The stand-in used when the server cannot produce a plan.
enum PlanFallback {
    /// The hand-authored mock, in the scanned bag when one is bundled.
    static func mockPlan() -> PackingPlan? {
        guard let mock = try? PlanLoader.mockPlan() else { return nil }
        guard let scanned = try? ScannedContainerLoader.bundled(),
              let rehomed = mock.replacingContainer(with: scanned)
        else { return mock }
        return rehomed
    }

    /// `API` puts the server's own `{"detail": …}` message in the error, which is
    /// far more useful than "operation could not be completed".
    static func message(for error: Error) -> String {
        let described = (error as NSError).localizedDescription
        return described.isEmpty ? String(describing: error) : described
    }
}

#if DEBUG
extension ScannedItem {
    /// A stand-in scan for previews: the bundled mock plan has no LiDAR data, so
    /// without this the heightmap path is invisible. Shoe-shaped on purpose —
    /// a heel-to-toe ramp, a collar dip, and a missing corner — so a wrong
    /// winding, a dropped hole or a missing skirt shows up at a glance.
    static func previewShoe(rows: Int = 30, cols: Int = 15, cell: Float = 0.01) -> ScannedItem {
        var heights = Array(repeating: Array(repeating: Float(0), count: cols), count: rows)
        for i in 0..<rows {
            for j in 0..<cols {
                heights[i][j] = 0.03 + 0.07 * Float(i) / Float(rows - 1)
            }
        }
        for i in (rows * 3 / 5)..<(rows - 2) {
            for j in (cols / 4)..<(cols * 3 / 4) { heights[i][j] = 0.02 }
        }
        for i in 0..<3 {
            for j in 0..<3 { heights[i][j] = 0 }
        }
        // ScannedItem only builds from a BoxFit, the way a real scan does.
        let box = BoxFit(
            width: Float(rows) * cell,
            depth: Float(cols) * cell,
            height: 0.10,
            center: .zero,
            axis: SIMD3(1, 0, 0)
        )
        return ScannedItem(box, heights: heights, cell: cell)
    }
}

#Preview("Mock plan") {
    NavigationStack {
        if let plan = try? PlanLoader.mockPlan() {
            PlanSceneView(plan: plan)
        } else {
            Text("Could not load the bundled mock plan.")
        }
    }
}

#Preview("Mock plan with a scanned item") {
    NavigationStack {
        if let plan = try? PlanLoader.mockPlan() {
            PlanSceneView(plan: plan, scans: ["shoes-pair": .previewShoe()])
        } else {
            Text("Could not load the bundled mock plan.")
        }
    }
}
#endif
