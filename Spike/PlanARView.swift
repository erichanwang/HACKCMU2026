import ARKit
import RealityKit
import SwiftUI

/// Length of each drawn axis, in metres.
private let axisLength: Float = 1.0
/// Thickness of the axis bars, in metres.
private let axisThickness: Float = 0.005
/// Radius of the sphere dropped at each tapped corner, in metres.
private let markerRadius: Float = 0.012
/// Two corners closer than this are treated as the same tap.
private let minCornerSpacing: Float = 0.02
/// sin of the angle between the X and Z legs, below which the taps are too close to a line.
private let minCornerSine: Float = 0.05

/// The three inner corners the user taps, in order.
enum BagCorner: Int, CaseIterable {
    case backLeftFloor
    case backRight
    case frontLeft

    var prompt: String {
        switch self {
        case .backLeftFloor: return "Tap the BACK-LEFT floor corner (origin)"
        case .backRight: return "Tap the BACK-RIGHT floor corner (+X, width)"
        case .frontLeft: return "Tap the FRONT-LEFT floor corner (+Z, depth)"
        }
    }

    var color: UIColor {
        switch self {
        case .backLeftFloor: return .systemYellow
        case .backRight: return .systemRed
        case .frontLeft: return .systemBlue
        }
    }
}

/// Builds a right-handed bag frame from three tapped floor corners.
///
/// - `p0` back-left floor corner → the origin
/// - `p1` back-right → +X (width)
/// - `p2` front-left → +Z (depth)
///
/// Y is the cross product of the two legs, and +Z is then re-derived from X and Y
/// so the basis stays orthonormal even when the third tap is not exactly square
/// to the first two.
func bagFrame(
    origin p0: SIMD3<Float>,
    xPoint p1: SIMD3<Float>,
    zPoint p2: SIMD3<Float>
) -> simd_float4x4? {
    let xLeg = p1 - p0
    let zLeg = p2 - p0
    guard length(xLeg) > minCornerSpacing, length(zLeg) > minCornerSpacing else { return nil }

    let x = normalize(xLeg)
    // Z × X = Y for a right-handed frame, so Y comes out of the floor.
    let yRaw = cross(normalize(zLeg), x)
    guard length(yRaw) > minCornerSine else { return nil }  // taps nearly in a line

    let y = normalize(yRaw)
    let z = cross(x, y)  // unit already: x ⟂ y, both unit

    return simd_float4x4(
        SIMD4(x, 0),
        SIMD4(y, 0),
        SIMD4(z, 0),
        SIMD4(p0, 1)
    )
}

@MainActor
final class PlanFrameController: NSObject, ObservableObject {
    @Published private(set) var status = BagCorner.backLeftFloor.prompt
    @Published private(set) var cornerCount = 0

    let arView = ARView(frame: .zero)

    private var corners: [SIMD3<Float>] = []
    private var markers: [AnchorEntity] = []
    private var frameAnchor: AnchorEntity?
    private var usesSceneMesh = false
    private var started = false

    func start() {
        guard !started else { return }
        started = true

        let config = ARWorldTrackingConfiguration()
        config.planeDetection = [.horizontal, .vertical]
        if ARWorldTrackingConfiguration.supportsSceneReconstruction(.mesh) {
            config.sceneReconstruction = .mesh
            usesSceneMesh = true
        }
        arView.session.run(config)
        arView.addGestureRecognizer(
            UITapGestureRecognizer(target: self, action: #selector(tap))
        )
    }

    func pause() {
        arView.session.pause()
    }

    func reset() {
        for marker in markers { arView.scene.removeAnchor(marker) }
        markers.removeAll()
        if let frameAnchor { arView.scene.removeAnchor(frameAnchor) }
        frameAnchor = nil
        corners.removeAll()
        cornerCount = 0
        status = BagCorner.backLeftFloor.prompt
    }

    @objc private func tap(_ gesture: UITapGestureRecognizer) {
        guard corners.count < BagCorner.allCases.count,
              let corner = BagCorner(rawValue: corners.count) else { return }

        guard let point = worldPoint(at: gesture.location(in: arView)) else {
            status = "No surface under that tap — move closer and try again"
            return
        }

        addMarker(at: point, color: corner.color)
        corners.append(point)
        cornerCount = corners.count

        if let next = BagCorner(rawValue: corners.count) {
            status = next.prompt
        } else {
            buildFrame()
        }
    }

    /// Where the tap lands in world space.
    ///
    /// With scene reconstruction running, `.estimatedPlane` raycasts resolve
    /// against the reconstructed mesh, which is what we want on a suitcase rim.
    /// Without LiDAR there is no mesh, so detected plane geometry is tried first
    /// and a plane estimate is the last resort.
    private func worldPoint(at location: CGPoint) -> SIMD3<Float>? {
        let targets: [ARRaycastQuery.Target] = usesSceneMesh
            ? [.estimatedPlane, .existingPlaneGeometry]
            : [.existingPlaneGeometry, .estimatedPlane]

        for target in targets {
            guard let hit = arView.raycast(from: location, allowing: target, alignment: .any).first
            else { continue }
            let column = hit.worldTransform.columns.3
            return SIMD3(column.x, column.y, column.z)
        }
        return nil
    }

    private func addMarker(at point: SIMD3<Float>, color: UIColor) {
        let sphere = ModelEntity(
            mesh: .generateSphere(radius: markerRadius),
            materials: [SimpleMaterial(color: color, isMetallic: false)]
        )
        let anchor = AnchorEntity(world: point)
        anchor.addChild(sphere)
        arView.scene.addAnchor(anchor)
        markers.append(anchor)
    }

    private func buildFrame() {
        guard let transform = bagFrame(origin: corners[0], xPoint: corners[1], zPoint: corners[2]) else {
            status = "Those taps are too close together or in a line — reset and retry"
            return
        }

        let anchor = AnchorEntity(world: transform)
        addAxes(to: anchor)
        arView.scene.addAnchor(anchor)
        frameAnchor = anchor

        // The measured legs are the quickest check that the frame landed on the bag.
        let width = simd_distance(corners[0], corners[1]) * 100
        let depth = simd_distance(corners[0], corners[2]) * 100
        status = String(
            format: "Frame set — %.0f cm wide × %.0f cm deep. Red +X, green +Y, blue +Z.",
            width, depth
        )
    }

    /// One-metre bars along each axis of the anchor's own frame.
    private func addAxes(to anchor: AnchorEntity) {
        let axes: [(direction: SIMD3<Float>, color: UIColor)] = [
            (SIMD3(1, 0, 0), .systemRed),
            (SIMD3(0, 1, 0), .systemGreen),
            (SIMD3(0, 0, 1), .systemBlue),
        ]

        for axis in axes {
            let size = SIMD3<Float>(
                axis.direction.x != 0 ? axisLength : axisThickness,
                axis.direction.y != 0 ? axisLength : axisThickness,
                axis.direction.z != 0 ? axisLength : axisThickness
            )
            let bar = ModelEntity(
                mesh: .generateBox(size: size),
                materials: [SimpleMaterial(color: axis.color, isMetallic: false)]
            )
            // generateBox is centred, so push it out to start at the origin.
            bar.position = axis.direction * (axisLength / 2)
            anchor.addChild(bar)
        }
    }
}

private struct PlanARContainer: UIViewRepresentable {
    let controller: PlanFrameController

    func makeUIView(context: Context) -> ARView {
        controller.start()
        return controller.arView
    }

    func updateUIView(_ uiView: ARView, context: Context) {}
}

/// Establishes the bag's coordinate frame by tapping three inner corners, then
/// draws the resulting axes. Does not render a packing plan yet.
struct PlanARView: View {
    @StateObject private var controller = PlanFrameController()

    var body: some View {
        ZStack(alignment: .bottom) {
            PlanARContainer(controller: controller).ignoresSafeArea()

            VStack(spacing: 12) {
                Text(controller.status)
                    .font(.system(.headline, design: .monospaced))
                    .multilineTextAlignment(.center)
                Text("Corner \(min(controller.cornerCount + 1, 3)) of 3")
                    .font(.footnote)
                Button("Reset", role: .destructive) { controller.reset() }
                    .buttonStyle(.borderedProminent)
            }
            .padding()
            .background(.black.opacity(0.6))
            .foregroundStyle(.white)
            .clipShape(RoundedRectangle(cornerRadius: 12))
            .padding(.bottom, 40)
        }
        .navigationTitle("Plan AR")
        .navigationBarTitleDisplayMode(.inline)
        .onDisappear { controller.pause() }
    }
}
