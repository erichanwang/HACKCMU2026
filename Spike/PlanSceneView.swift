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
    /// Placement entities bucketed by layer index, bottom layer first.
    private var entitiesByLayer: [[Entity]] = []
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
        root.addChild(wireframeBox(size: dimensions))

        for layer in layers {
            var entities: [Entity] = []
            for placement in layer.placements {
                let box = itemEntity(placement, scan: scans[placement.itemID])
                root.addChild(box)
                entities.append(box)

                let label = labelPivot(for: placement)
                root.addChild(label)
                labels.append(label)
                entities.append(label)
            }
            entitiesByLayer.append(entities)
        }

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

    /// Shows layers `0...topLayer`, so dragging the slider down peels the suitcase apart.
    func show(upTo topLayer: Int) {
        for (index, entities) in entitiesByLayer.enumerated() {
            let visible = index <= topLayer
            for entity in entities { entity.isEnabled = visible }
        }
    }

    // MARK: - Scene building

    /// The scanned surface when we have one, the bounding box otherwise.
    ///
    /// The two meshes are anchored differently and that is easy to get wrong:
    /// `generateBox` is built around its centre, so it wants `renderCenter`, while
    /// the heightmap is authored from its min corner and wants `position`.
    private func itemEntity(_ placement: Placement, scan: ScannedItem?) -> ModelEntity {
        let material = SimpleMaterial(
            color: color(for: placement).withAlphaComponent(0.45),
            roughness: 0.6,
            isMetallic: false
        )

        if let scan, let mesh = HeightmapMesh.generate(from: scan, fitting: placement) {
            let entity = ModelEntity(mesh: mesh, materials: [material])
            entity.position = placement.position.simd
            return entity
        }

        let entity = ModelEntity(
            mesh: .generateBox(size: placement.size.simd, cornerRadius: 0.002),
            materials: [material]
        )
        // `renderCenter` is the shared min-corner → centre helper; never inline the
        // `size / 2` here.
        entity.position = placement.renderCenter.simd
        return entity
    }

    private func labelPivot(for placement: Placement) -> Entity {
        let mesh = MeshResource.generateText(
            placement.label,
            extrusionDepth: 0.0004,
            font: .systemFont(ofSize: labelSize, weight: .medium),
            alignment: .center
        )
        let text = ModelEntity(mesh: mesh, materials: [UnlitMaterial(color: .label)])
        // generateText anchors at the baseline's left edge; recentre on the pivot.
        text.position = -text.visualBounds(relativeTo: nil).center

        let pivot = Entity()
        pivot.addChild(text)
        var centre = placement.renderCenter
        centre.y = placement.box.maxCorner.y + 0.012
        pivot.position = centre.simd
        return pivot
    }

    /// Wireframe as twelve thin bars — RealityKit has no line primitive.
    private func wireframeBox(size: Vector3) -> Entity {
        let container = Entity()
        let material = SimpleMaterial(color: .systemGray, roughness: 0.5, isMetallic: false)
        let extent = size.simd

        for axis in 0..<3 {
            var barSize = SIMD3<Float>(repeating: wireThickness)
            barSize[axis] = extent[axis]

            // The four edges parallel to `axis` sit at the corners of the other two.
            let otherA = (axis + 1) % 3
            let otherB = (axis + 2) % 3
            for a in [Float(0), 1] {
                for b in [Float(0), 1] {
                    let bar = ModelEntity(mesh: .generateBox(size: barSize), materials: [material])
                    var position = SIMD3<Float>(repeating: 0)
                    position[axis] = extent[axis] / 2
                    position[otherA] = a * extent[otherA]
                    position[otherB] = b * extent[otherB]
                    bar.position = position
                    container.addChild(bar)
                }
            }
        }
        return container
    }

    private func color(for placement: Placement) -> UIColor {
        UIColor(
            hue: (CGFloat(placement.step) * 0.17).truncatingRemainder(dividingBy: 1),
            saturation: 0.65,
            brightness: 0.85,
            alpha: 1
        )
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
    @State private var topLayer: Double
    @State private var lastDrag: CGSize = .zero

    private let plan: PackingPlan

    /// - Parameter scans: LiDAR scans by item id. A placement whose id matches one
    ///   is drawn as the scanned surface; everything else falls back to its box.
    ///   This assumes the solver's `item_id` is the scanner's `id`, which is what
    ///   the server contract in SCAN_OUTPUT.md produces.
    init(plan: PackingPlan, scans: [String: ScannedItem] = [:]) {
        self.plan = plan
        let controller = PlanSceneController(plan: plan, scans: scans)
        _controller = StateObject(wrappedValue: controller)
        _topLayer = State(initialValue: Double(controller.layerCount - 1))
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
        // onChange does not fire for the initial value, so the slider's starting
        // position has to be pushed into the scene explicitly.
        .onAppear { controller.show(upTo: Int(topLayer)) }
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
                    value: $topLayer,
                    in: 0...Double(controller.layerCount - 1),
                    step: 1
                )
                .onChange(of: topLayer) { _, value in
                    controller.show(upTo: Int(value))
                }
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
        let shown = Int(topLayer) + 1
        let items = plan.layers().prefix(shown).reduce(0) { $0 + $1.placements.count }
        return "Layers 1–\(shown) of \(controller.layerCount) · \(items) items"
    }
}

/// Entry point from the app's root: the live plan for one suitcase.
///
/// The server runs the solver and returns the real plan. The bundled mock is a
/// fallback for when that fetch fails — and when it does, the failure is shown
/// rather than swallowed, because a mock that silently stands in for a live plan
/// is indistinguishable from a working one.
struct PlanSceneScreen: View {
    let suitcaseID: String?

    @State private var state: LoadState = .loading

    enum LoadState {
        case loading
        /// The real plan, straight from the server.
        case live(PackingPlan)
        /// The server failed; this is the mock, and why.
        case fallback(PackingPlan, reason: String)
        /// Neither the server nor the mock produced anything.
        case unavailable(String)
    }

    var body: some View {
        Group {
            switch state {
            case .loading:
                ProgressView("Planning…")
                    .frame(maxWidth: .infinity, maxHeight: .infinity)

            case let .live(plan):
                PlanSceneView(plan: plan)
                    .id(identity(of: plan))

            case let .fallback(plan, reason):
                PlanSceneView(plan: plan)
                    .id(identity(of: plan))
                    .overlay(alignment: .top) { banner(reason) }

            case let .unavailable(reason):
                ContentUnavailableView(
                    "No plan",
                    systemImage: "shippingbox",
                    description: Text(reason)
                )
            }
        }
        .task { await load() }
    }

    /// The scene is built once per plan, so a plan arriving after the view is on
    /// screen rebuilds it instead of leaving the first one rendered.
    private func identity(of plan: PackingPlan) -> String {
        "\(plan.container.id)-\(plan.placements.count)"
    }

    private func banner(_ reason: String) -> some View {
        HStack(alignment: .top, spacing: 8) {
            Image(systemName: "exclamationmark.triangle.fill")
                .foregroundStyle(.orange)
            VStack(alignment: .leading, spacing: 2) {
                Text("Showing the bundled mock plan")
                    .font(.caption.weight(.semibold))
                Text(reason)
                    .font(.caption2)
                    .foregroundStyle(.secondary)
                    .textSelection(.enabled)
            }
        }
        .padding(10)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(.regularMaterial, in: RoundedRectangle(cornerRadius: 12))
        .padding()
    }

    private func load() async {
        if let suitcaseID {
            do {
                state = .live(try await API.plan(suitcaseId: suitcaseID))
                return
            } catch {
                state = fallbackState(reason: Self.message(for: error))
                return
            }
        }
        state = fallbackState(reason: "No suitcase selected.")
    }

    private func fallbackState(reason: String) -> LoadState {
        guard let mock = Self.mockPlan() else {
            return .unavailable("\(reason)\n\nThe bundled mock plan could not be loaded either.")
        }
        return .fallback(mock, reason: reason)
    }

    /// The mock, in the scanned bag when one is bundled.
    private static func mockPlan() -> PackingPlan? {
        guard let mock = try? PlanLoader.mockPlan() else { return nil }
        guard let scanned = try? ScannedContainerLoader.bundled(),
              let rehomed = mock.replacingContainer(with: scanned)
        else { return mock }
        return rehomed
    }

    /// `API` puts the server's own `{"detail": …}` message in the error, which is
    /// far more useful than "operation could not be completed".
    private static func message(for error: Error) -> String {
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
