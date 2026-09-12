// Stand-in for the RealityKit symbols Spike/ScanView.swift touches. Signatures from Apple's docs:
// https://developer.apple.com/documentation/realitykit/arview
// https://developer.apple.com/documentation/realitykit/entity
// https://developer.apple.com/documentation/realitykit/anchorentity
// https://developer.apple.com/documentation/realitykit/modelentity
// https://developer.apple.com/documentation/realitykit/meshresource
// https://developer.apple.com/documentation/realitykit/simplematerial
// https://developer.apple.com/documentation/realitykit/scene
// https://developer.apple.com/documentation/realitykit/hasanchoring
// https://developer.apple.com/documentation/realitykit/entity/removefromparent()
//
// Actor isolation: ARView is a UIView subclass and the real SDK documents RealityKit's entity/
// scene APIs as main-thread-only ("Interact with RealityKit content only from the main thread" —
// https://developer.apple.com/documentation/realitykit/entity), so Entity/Scene/AnchorEntity/
// ModelEntity are marked @MainActor here to match. This is the *other* half of the concurrency
// bug this harness is a deliberate test for (see run.sh): Spike/ScanView.swift's `Coordinator` is
// a plain, non-@MainActor NSObject whose `tap()` touches `ARView`/`Entity` members directly.
// `@_exported`: on the real SDK, `import RealityKit` alone gives you UIKit (ARView: UIView,
// Material.Color = UIColor) and ARKit (ARView.raycast/session return ARKit types) — matching
// that here so ScanView.swift resolves the same way it would on a Mac.
@_exported import UIKit
@_exported import ARKit
import simd

@MainActor
open class ARView: UIView {
    public override init(frame: CGRect) { super.init(frame: frame) }
    public var session: ARSession { fatalError() }
    public struct DebugOptions: OptionSet {
        public let rawValue: Int
        public init(rawValue: Int) { self.rawValue = rawValue }
        public static let showSceneUnderstanding = DebugOptions(rawValue: 1)
    }
    public var debugOptions: DebugOptions = []
    public var scene: RealityKit_Scene { fatalError() }
    public func raycast(from point: CGPoint, allowing target: ARRaycastQuery.Target, alignment: ARRaycastQuery.TargetAlignment) -> [ARRaycastResult] { fatalError() }
    public func project(_ point: SIMD3<Float>) -> CGPoint? { fatalError() }
    public func snapshot(saveToHDR: Bool, completion: @escaping (UIImage?) -> Void) {}
}

/// Named to avoid colliding with SwiftUI's own `Scene` protocol, which the app also uses
/// (`SpikeApp: App { var body: some Scene ... }`) — the real SDK disambiguates by module
/// (`RealityKit.Scene` vs `SwiftUI.Scene`); this shim disambiguates by name instead since it has
/// no module system of its own.
@MainActor
public class RealityKit_Scene {
    public func addAnchor(_ anchor: HasAnchoring) {}
}

@MainActor
public protocol HasAnchoring: AnyObject {}

@MainActor
open class Entity {
    public init() {}
    public var position: SIMD3<Float> = .zero
    public var orientation: simd_quatf = simd_quatf()
    public func addChild(_ child: Entity) {}
    public func removeFromParent() {}
}

@MainActor
public final class AnchorEntity: Entity, HasAnchoring {
    public init(world position: SIMD3<Float>) { super.init() }
    public init(anchor: ARAnchor) { super.init() }
}

public protocol Material {}

@MainActor
public final class ModelEntity: Entity {
    public init(mesh: MeshResource, materials: [Material]) { super.init() }
}

public struct MeshResource {
    public static func generateBox(width: Float, height: Float, depth: Float) -> MeshResource { fatalError() }
}

public struct SimpleMaterial: Material {
    public init(color: UIColor, isMetallic: Bool) {}
}
