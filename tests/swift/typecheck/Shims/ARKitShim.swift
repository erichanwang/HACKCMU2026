// Stand-in for the ARKit symbols Spike/ScanView.swift touches. Signatures from Apple's docs:
// https://developer.apple.com/documentation/arkit/arconfiguration
// https://developer.apple.com/documentation/arkit/arworldtrackingconfiguration
// https://developer.apple.com/documentation/arkit/arsession
// https://developer.apple.com/documentation/arkit/arframe
// https://developer.apple.com/documentation/arkit/aranchor
// https://developer.apple.com/documentation/arkit/arplaneanchor
// https://developer.apple.com/documentation/arkit/armeshanchor
// https://developer.apple.com/documentation/arkit/argeometrysource
// https://developer.apple.com/documentation/arkit/argeometryelement
// https://developer.apple.com/documentation/arkit/arraycastquery
// https://developer.apple.com/documentation/arkit/arraycastresult
// https://developer.apple.com/documentation/arkit/arsessiondelegate
//
// UNVERIFIED actor isolation: unlike UIKit/SwiftUI, Apple's public docs for these ARKit types do
// not (as far as this pass found) call out `@MainActor`. ARSession historically delivers frames
// via a delegate callback that is explicitly NOT guaranteed to be the main thread, which is why
// these are left nonisolated below. This is a real gap in this harness's fidelity — see the
// "unverified signatures" list in the report — not a confirmed fact about the SDK.
//
// `@_exported`: on the real SDK, `import ARKit` alone gives you NSObject (ARAnchor is one),
// simd types (ARAnchor.transform is simd_float4x4, with its operators), and MTLBuffer — this
// mirrors that so Spike/ScanView.swift (which imports only ARKit/PackingPlan/RealityKit/SwiftUI,
// no Foundation/simd/Metal) resolves the same way here as on a Mac.
@_exported import Foundation
@_exported import simd
@_exported import Metal

public class ARConfiguration {
    public struct SceneReconstruction: OptionSet {
        public let rawValue: Int
        public init(rawValue: Int) { self.rawValue = rawValue }
        public static let mesh = SceneReconstruction(rawValue: 1)
    }
}

public final class ARWorldTrackingConfiguration: ARConfiguration {
    public struct PlaneDetection: OptionSet {
        public let rawValue: Int
        public init(rawValue: Int) { self.rawValue = rawValue }
        public static let horizontal = PlaneDetection(rawValue: 1)
    }
    public override init() { super.init() }
    public var planeDetection: PlaneDetection = []
    public var sceneReconstruction: ARConfiguration.SceneReconstruction = []
}

public class ARSession {
    public func run(_ configuration: ARConfiguration) {}
    public var currentFrame: ARFrame? { fatalError() }
    public weak var delegate: ARSessionDelegate?
}

/// Real ARSessionDelegate (https://developer.apple.com/documentation/arkit/arsessiondelegate) is
/// `@objc public protocol ARSessionDelegate: NSObjectProtocol` with every method `optional` —
/// neither `@objc` protocols nor `optional` requirements can exist without Objective-C interop
/// (unavailable on Linux; see run.sh). This is a plain Swift approximation: only the one method
/// Spike/ScanView.swift implements is declared, given a no-op default so conformance doesn't
/// require it (the closest approximation of "optional" available here). NOT `@MainActor` — the
/// real protocol carries no such annotation; ARKit is documented to call it "on the main queue"
/// but as a convention, not a compiler-enforced isolation. This is UNVERIFIED (see report).
public protocol ARSessionDelegate: AnyObject {
    func session(_ session: ARSession, didUpdate frame: ARFrame)
}
extension ARSessionDelegate {
    public func session(_ session: ARSession, didUpdate frame: ARFrame) {}
}

public class ARFrame {
    public var anchors: [ARAnchor] { fatalError() }
}

public class ARAnchor {
    public var transform: simd_float4x4 { fatalError() }
}

public final class ARPlaneAnchor: ARAnchor {
    public enum Alignment {
        case horizontal, vertical
    }
    public var alignment: Alignment { fatalError() }
}

public final class ARMeshAnchor: ARAnchor {
    public var geometry: ARMeshGeometry { fatalError() }
}

public class ARMeshGeometry {
    public var vertices: ARGeometrySource { fatalError() }
    public var faces: ARGeometryElement { fatalError() }
}

public class ARGeometrySource {
    public var buffer: MTLBuffer { fatalError() }
    public var offset: Int { fatalError() }
    public var stride: Int { fatalError() }
}

public class ARGeometryElement {
    public var buffer: MTLBuffer { fatalError() }
    /// Number of primitives (e.g. triangles), NOT the number of indices.
    public var count: Int { fatalError() }
    public var indexCountPerPrimitive: Int { fatalError() }
    public var bytesPerIndex: Int { fatalError() }
}

public final class ARRaycastQuery {
    public struct Target {
        public static let estimatedPlane = Target()
    }
    public struct TargetAlignment {
        public static let any = TargetAlignment()
    }
}

public final class ARRaycastResult {
    public var worldTransform: simd_float4x4 { fatalError() }
}
