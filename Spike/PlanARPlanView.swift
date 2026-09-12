import ARKit
import PackingPlan
import RealityKit
import SwiftUI

/// How far around the tap, in screen points, to look for the real floor.
private let neighbourhoodRadius: CGFloat = 24
/// Anything this far below the highest nearby surface is something else — the
/// table the case stands on, or the room floor seen through the opening.
private let maxDropBelowHighest: Float = 0.35
/// A detected plane wider than this multiple of the container has merged the
/// case with its surroundings, so its centre is not the bag's centre.
private let maxPlaneOversize: Float = 2.5

/// What the last tap actually found. Shown on screen and printed, because this
/// is the kind of thing that can only be diagnosed with the phone in hand.
struct AnchorDiagnostics {
    var cameraY: Float = 0
    /// The first hit straight under the tap, before any neighbourhood search.
    var firstHitY: Float?
    var planeY: Float?
    var planeExtent: SIMD2<Float>?
    var planeIsOversized = false
    var candidateCount = 0
    var lowestY: Float?
    var highestY: Float?
    var chosenY: Float?
    var chosenSource = "none"
    var originSource = "none"

    var consoleLine: String {
        func f(_ v: Float?) -> String { v.map { String(format: "%.3f", $0) } ?? "—" }
        var line = "[anchor] camera.y=\(f(cameraY)) firstHit.y=\(f(firstHitY)) plane.y=\(f(planeY))"
        if let e = planeExtent {
            line += String(format: " plane=%.2f×%.2fm%@", e.x, e.y, planeIsOversized ? " OVERSIZED" : "")
        }
        line += " candidates=\(candidateCount) span=[\(f(lowestY)), \(f(highestY))]"
        line += " chosen=\(f(chosenY)) via \(chosenSource), origin via \(originSource)"
        return line
    }

    /// Compact enough for the on-screen overlay.
    var screenLines: [String] {
        func cm(_ v: Float?) -> String { v.map { String(format: "%.1f", $0 * 100) } ?? "—" }
        var lines = [
            "cam \(cm(cameraY))  hit \(cm(firstHitY))  plane \(cm(planeY))",
            "low \(cm(lowestY))  high \(cm(highestY))  chose \(cm(chosenY)) cm",
            "\(candidateCount) samples · \(chosenSource) · origin \(originSource)",
        ]
        if planeIsOversized, let e = planeExtent {
            lines.append(String(format: "plane %.2f×%.2f m spans past the bag", e.x, e.y))
        }
        return lines
    }
}

/// Anchors a plan to the floor inside an open suitcase.
///
/// The previous version took the first raycast hit and the plane anchor's centre.
/// Both mislead on an open case: the rim is what a finger can reach, and ARKit
/// readily merges the rim with the surrounding table into one large plane whose
/// centre is nowhere near the bag. So the tap now samples a neighbourhood against
/// both the reconstruction mesh and plane geometry and takes the *lowest*
/// plausible surface, which is the interior floor rather than the lip above it.
@MainActor
final class BagPlaneController: NSObject, ObservableObject {
    @Published private(set) var status = "Tap the floor inside the open suitcase"
    @Published private(set) var isAnchored = false
    @Published private(set) var arEnabled = true
    @Published private(set) var diagnostics: AnchorDiagnostics?

    let arView = ARView(frame: .zero)

    private struct Pose {
        var origin: SIMD3<Float>
        var baseYaw: Float
        var yawOffset: Float = 0
        var nudge: SIMD3<Float> = .zero
        /// Hand correction for the height, when detection is still off.
        var heightOffset: Float = 0
    }

    enum Source: String {
        case mesh = "mesh"
        case plane = "plane"
        case estimated = "estimated"
    }

    struct Candidate {
        var world: SIMD3<Float>
        var source: Source
        var plane: ARPlaneAnchor?
    }

    private var pose: Pose?
    private var anchor: AnchorEntity?
    private let bagRoot = Entity()
    /// Sits exactly where the first raycast hit landed, so the tap can be
    /// compared against the wireframe on screen.
    private var hitMarker: AnchorEntity?
    /// Sits at the surface actually chosen, when it differs from the first hit.
    private var chosenMarker: AnchorEntity?

    private let container: Vector3
    private let populate: (Entity) -> Void
    private var started = false
    private var usesSceneMesh = false

    init(container: Vector3, populate: @escaping (Entity) -> Void) {
        self.container = container
        self.populate = populate
        super.init()
    }

    // MARK: - Session

    private func configuration() -> ARWorldTrackingConfiguration {
        let config = ARWorldTrackingConfiguration()
        config.planeDetection = [.horizontal]
        if ARWorldTrackingConfiguration.supportsSceneReconstruction(.mesh) {
            config.sceneReconstruction = .mesh
            usesSceneMesh = true
        }
        return config
    }

    func start() {
        guard !started else { return }
        started = true
        arView.session.run(configuration())
        if usesSceneMesh {
            // `.collision` is what lets the mesh be hit-tested directly, rather
            // than only through ARKit's plane estimates.
            arView.environment.sceneUnderstanding.options.insert(.occlusion)
            arView.environment.sceneUnderstanding.options.insert(.collision)
        }
        installGestures()
    }

    func pause() { arView.session.pause() }

    func setAR(_ on: Bool) {
        guard on != arEnabled else { return }
        arEnabled = on
        if on {
            arView.session.run(configuration())
            if pose != nil { applyPose() }
        } else {
            arView.session.pause()
        }
    }

    func reset() {
        bagRoot.isEnabled = false
        for marker in [hitMarker, chosenMarker].compacted() {
            arView.scene.removeAnchor(marker)
        }
        hitMarker = nil
        chosenMarker = nil
        pose = nil
        diagnostics = nil
        isAnchored = false
        status = "Tap the floor inside the open suitcase"
    }

    // MARK: - Gestures
    //
    // All four live in UIKit so they arbitrate against each other properly; the
    // two-finger pan and the twist are allowed to run together.

    private func installGestures() {
        let tap = UITapGestureRecognizer(target: self, action: #selector(handleTap))
        let slide = UIPanGestureRecognizer(target: self, action: #selector(handleSlide))
        slide.minimumNumberOfTouches = 1
        slide.maximumNumberOfTouches = 1
        let lift = UIPanGestureRecognizer(target: self, action: #selector(handleLift))
        lift.minimumNumberOfTouches = 2
        lift.maximumNumberOfTouches = 2
        let twist = UIRotationGestureRecognizer(target: self, action: #selector(handleTwist))

        for recogniser in [tap, slide, lift, twist] as [UIGestureRecognizer] {
            recogniser.delegate = self
            arView.addGestureRecognizer(recogniser)
        }
    }

    @objc private func handleTap(_ gesture: UITapGestureRecognizer) {
        place(at: gesture.location(in: arView))
    }

    @objc private func handleSlide(_ gesture: UIPanGestureRecognizer) {
        let delta = gesture.translation(in: arView)
        gesture.setTranslation(.zero, in: arView)
        nudge(by: CGSize(width: delta.x, height: delta.y))
    }

    /// Two fingers dragged up and down move the bag vertically.
    @objc private func handleLift(_ gesture: UIPanGestureRecognizer) {
        let delta = gesture.translation(in: arView)
        gesture.setTranslation(.zero, in: arView)
        guard pose != nil else { return }
        // Screen down is +y, and dragging down should lower the bag.
        pose?.heightOffset -= Float(delta.y) * 0.0015
        applyPose()
        if let height = pose?.heightOffset {
            status = String(format: "Height %+.1f cm by hand", height * 100)
        }
    }

    @objc private func handleTwist(_ gesture: UIRotationGestureRecognizer) {
        let delta = gesture.rotation
        gesture.rotation = 0
        guard pose != nil else { return }
        pose?.yawOffset += Float(-delta)
        applyPose()
    }

    // MARK: - Placing

    /// One tap: search a neighbourhood for the lowest plausible surface.
    func place(at point: CGPoint) {
        guard arEnabled else { return }

        var diag = AnchorDiagnostics()
        diag.cameraY = arView.cameraTransform.translation.y

        let found = candidates(around: point)
        diag.candidateCount = found.count
        diag.firstHitY = firstHit(at: point)?.world.y

        guard !found.isEmpty else {
            status = "No surface under that tap — aim inside the open case"
            diagnostics = diag
            print(diag.consoleLine)
            return
        }

        let heights = found.map(\.world.y)
        diag.lowestY = heights.min()
        diag.highestY = heights.max()

        let chosen = Self.lowestPlausible(of: found)
        diag.chosenY = chosen.world.y
        diag.chosenSource = chosen.source.rawValue

        // XZ: the plane's centre when the plane actually looks like this bag,
        // otherwise the tap itself. A merged plane's centre can be metres away.
        var originXZ = chosen.world
        var yaw: Float = 0
        diag.originSource = "tap"

        if let plane = found.compactMap(\.plane).first {
            let centre = plane.transform * SIMD4<Float>(plane.center, 1)
            let extent = SIMD2<Float>(plane.planeExtent.width, plane.planeExtent.height)
            diag.planeY = centre.y
            diag.planeExtent = extent
            diag.planeIsOversized =
                extent.x > container.x * maxPlaneOversize || extent.y > container.z * maxPlaneOversize
            yaw = Self.yaw(of: plane.transform)
            if !diag.planeIsOversized {
                originXZ = SIMD3(centre.x, chosen.world.y, centre.z)
                diag.originSource = "plane centre"
            }
        }

        pose = Pose(
            origin: SIMD3(originXZ.x, chosen.world.y, originXZ.z),
            baseYaw: yaw
        )
        diagnostics = diag
        print(diag.consoleLine)

        showMarker(&hitMarker, at: firstHit(at: point)?.world ?? chosen.world, color: .systemRed)
        showMarker(&chosenMarker, at: chosen.world, color: .systemGreen)

        applyPose()
        isAnchored = true
        status = "Red = tap · green = floor used · twist, drag, two-finger up/down"
    }

    /// The lowest surface in the sample that is not implausibly far down.
    ///
    /// The rim of an open case is the surface a finger can reach, and it sits a
    /// shell's depth above the interior floor, so the first hit is the wrong one
    /// and the *lowest* one is right. The cut-off stops a sample that slipped
    /// past the case — the table it stands on, or the room floor seen through the
    /// opening — from dragging the bag down with it.
    static func lowestPlausible(of found: [Candidate]) -> Candidate {
        let highest = found.map(\.world.y).max() ?? 0
        let cut = highest - maxDropBelowHighest
        let ordered = found.sorted { $0.world.y < $1.world.y }
        return ordered.first { $0.world.y >= cut } ?? ordered[0]
    }

    /// Everything found within `neighbourhoodRadius` of the tap, from the mesh
    /// and from plane geometry alike.
    private func candidates(around point: CGPoint) -> [Candidate] {
        var out: [Candidate] = []
        let steps: [CGFloat] = [-neighbourhoodRadius, -neighbourhoodRadius / 2, 0,
                                neighbourhoodRadius / 2, neighbourhoodRadius]

        for dx in steps {
            for dy in steps {
                let sample = CGPoint(x: point.x + dx, y: point.y + dy)

                if usesSceneMesh,
                   let hit = arView.hitTest(sample, query: .nearest, mask: .sceneUnderstanding).first {
                    out.append(Candidate(world: hit.position, source: .mesh, plane: nil))
                }
                if let hit = arView.raycast(from: sample, allowing: .existingPlaneGeometry, alignment: .horizontal).first {
                    let t = hit.worldTransform.columns.3
                    out.append(Candidate(world: SIMD3(t.x, t.y, t.z), source: .plane,
                                         plane: hit.anchor as? ARPlaneAnchor))
                }
            }
        }

        if out.isEmpty,
           let hit = arView.raycast(from: point, allowing: .estimatedPlane, alignment: .horizontal).first {
            let t = hit.worldTransform.columns.3
            out.append(Candidate(world: SIMD3(t.x, t.y, t.z), source: .estimated, plane: nil))
        }
        return out
    }

    /// The single hit straight under the tap — what the old code would have used.
    private func firstHit(at point: CGPoint) -> Candidate? {
        if usesSceneMesh, let hit = arView.hitTest(point, query: .nearest, mask: .sceneUnderstanding).first {
            return Candidate(world: hit.position, source: .mesh, plane: nil)
        }
        if let hit = arView.raycast(from: point, allowing: .existingPlaneGeometry, alignment: .horizontal).first {
            let t = hit.worldTransform.columns.3
            return Candidate(world: SIMD3(t.x, t.y, t.z), source: .plane, plane: hit.anchor as? ARPlaneAnchor)
        }
        return nil
    }

    func nudge(by translation: CGSize) {
        guard pose != nil else { return }
        let metresPerPoint: Float = 0.0015
        let camera = arView.cameraTransform.matrix
        let right = Self.flattened(SIMD3(camera.columns.0.x, camera.columns.0.y, camera.columns.0.z))
        let forward = Self.flattened(-SIMD3(camera.columns.2.x, camera.columns.2.y, camera.columns.2.z))
        pose?.nudge += right * Float(translation.width) * metresPerPoint
            + forward * Float(-translation.height) * metresPerPoint
        applyPose()
    }

    // MARK: - Scene

    private func showMarker(_ slot: inout AnchorEntity?, at point: SIMD3<Float>, color: UIColor) {
        if let existing = slot { arView.scene.removeAnchor(existing) }
        let sphere = ModelEntity(
            mesh: .generateSphere(radius: 0.008),
            materials: [UnlitMaterial(color: color)]
        )
        let anchor = AnchorEntity(world: point)
        anchor.addChild(sphere)
        arView.scene.addAnchor(anchor)
        slot = anchor
    }

    private func applyPose() {
        guard let pose else { return }
        if anchor == nil {
            let world = AnchorEntity(world: .zero)
            populate(bagRoot)
            world.addChild(bagRoot)
            arView.scene.addAnchor(world)
            anchor = world
        }
        bagRoot.isEnabled = true
        bagRoot.transform = Transform(matrix: transform(for: pose))
    }

    /// Y up, yaw from the plane plus the twist, and the origin placed so the
    /// container's footprint is centred on `pose.origin` with its floor on it.
    private func transform(for pose: Pose) -> simd_float4x4 {
        let yaw = pose.baseYaw + pose.yawOffset
        let x = SIMD3<Float>(cos(yaw), 0, -sin(yaw))
        let y = SIMD3<Float>(0, 1, 0)
        let z = simd_cross(x, y)

        let origin = pose.origin + pose.nudge + SIMD3(0, pose.heightOffset, 0)
            - x * (container.x / 2)
            - z * (container.z / 2)

        return simd_float4x4(SIMD4(x, 0), SIMD4(y, 0), SIMD4(z, 0), SIMD4(origin, 1))
    }

    private static func yaw(of transform: simd_float4x4) -> Float {
        let axis = flattened(SIMD3(transform.columns.0.x, transform.columns.0.y, transform.columns.0.z))
        return atan2(-axis.z, axis.x)
    }

    private static func flattened(_ v: SIMD3<Float>) -> SIMD3<Float> {
        let flat = SIMD3<Float>(v.x, 0, v.z)
        return simd_length(flat) > 1e-5 ? simd_normalize(flat) : SIMD3(1, 0, 0)
    }
}

extension BagPlaneController: UIGestureRecognizerDelegate {
    nonisolated func gestureRecognizer(
        _ gestureRecognizer: UIGestureRecognizer,
        shouldRecognizeSimultaneouslyWith other: UIGestureRecognizer
    ) -> Bool {
        // Twisting and lifting are both two-fingered and often wanted together.
        gestureRecognizer is UIRotationGestureRecognizer || other is UIRotationGestureRecognizer
    }
}

private extension Array where Element == AnchorEntity? {
    func compacted() -> [AnchorEntity] { compactMap { $0 } }
}

/// The packing plan in the real suitcase, with AR switchable off in place.
///
/// Switching AR off swaps the camera feed for the plain 3D scene over the same
/// plan — one screen with a switch, not two screens. Switching back on restores
/// the last placement rather than asking for another tap.
struct PlanARPlanView: View {
    @Environment(\.dismiss) private var dismiss

    @StateObject private var controller: BagPlaneController
    @Binding private var topLayer: Int
    @State private var showingDiagnostics = true

    private let content: PlanEntityBuilder.Content
    private let plan: PackingPlan
    private let scans: [String: ScannedItem]

    static var isSupported: Bool { ARWorldTrackingConfiguration.isSupported }

    init(plan: PackingPlan, scans: [String: ScannedItem] = [:], topLayer: Binding<Int>) {
        self.plan = plan
        self.scans = scans
        self._topLayer = topLayer

        let built = PlanEntityBuilder(plan: plan, scans: scans)
            .build(includeLabels: false, includeWireframe: true)
        self.content = built
        _controller = StateObject(
            wrappedValue: BagPlaneController(
                container: plan.container.dimensions,
                populate: { $0.addChild(built.root) }
            )
        )
    }

    var body: some View {
        ZStack(alignment: .bottom) {
            // Always present, so the prompt sits over the camera feed rather than
            // a blank view, and so the session keeps its world origin.
            BagPlaneContainer(controller: controller).ignoresSafeArea()

            if !controller.arEnabled {
                LayeredPlanSceneView(plan: plan, scans: scans, topLayer: $topLayer)
                    .ignoresSafeArea()
            }

            overlay
        }
        .onAppear { content.show(upTo: clamped(topLayer)) }
        .onChange(of: topLayer) { _, value in content.show(upTo: clamped(value)) }
        .onDisappear { controller.pause() }
    }

    private var overlay: some View {
        VStack(spacing: 10) {
            if controller.arEnabled {
                Text(controller.status)
                    .font(.system(.footnote, design: .monospaced))
                    .multilineTextAlignment(.center)

                if showingDiagnostics, let diagnostics = controller.diagnostics {
                    VStack(alignment: .leading, spacing: 1) {
                        ForEach(diagnostics.screenLines, id: \.self) { line in
                            Text(line)
                        }
                    }
                    .font(.system(size: 10, design: .monospaced))
                    .foregroundStyle(.yellow)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .textSelection(.enabled)
                }

                if controller.isAnchored, layerCount > 1 {
                    Text(layerCaption).font(.caption.monospacedDigit())
                    Slider(value: sliderBinding, in: 0...Double(layerCount - 1), step: 1)
                }
            }

            HStack {
                Toggle(isOn: arBinding) { Label("AR", systemImage: "arkit") }
                    .toggleStyle(.button)
                    .buttonStyle(.bordered)

                Button("Reset", role: .destructive) { controller.reset() }
                    .buttonStyle(.bordered)
                    .disabled(!controller.arEnabled || !controller.isAnchored)

                Toggle(isOn: $showingDiagnostics) { Image(systemName: "ladybug") }
                    .toggleStyle(.button)
                    .buttonStyle(.bordered)
                    .disabled(!controller.arEnabled)

                Spacer()

                Button("Done") { dismiss() }
                    .buttonStyle(.borderedProminent)
            }
        }
        .padding()
        .background(.black.opacity(0.65))
        .foregroundStyle(.white)
        .clipShape(RoundedRectangle(cornerRadius: 12))
        .padding(.bottom, 40)
        .padding(.horizontal)
    }

    private var arBinding: Binding<Bool> {
        Binding(get: { controller.arEnabled }, set: { controller.setAR($0) })
    }

    private var layerCount: Int { max(content.entitiesByLayer.count, 1) }

    private func clamped(_ index: Int) -> Int { min(max(index, 0), layerCount - 1) }

    private var sliderBinding: Binding<Double> {
        Binding(get: { Double(clamped(topLayer)) }, set: { topLayer = clamped(Int($0)) })
    }

    private var layerCaption: String {
        let shown = clamped(topLayer) + 1
        let items = plan.layers().prefix(shown).reduce(0) { $0 + $1.placements.count }
        return "Layers 1–\(shown) of \(layerCount) · \(items) items"
    }
}

private struct BagPlaneContainer: UIViewRepresentable {
    let controller: BagPlaneController

    func makeUIView(context: Context) -> ARView {
        controller.start()
        return controller.arView
    }

    func updateUIView(_ uiView: ARView, context: Context) {}
}
