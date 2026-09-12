// Scene schema shared by every PackPhysics module. Mirrors physics/schema.py 1:1,
// including its JSON keys, so scenes round-trip between the iOS app, the Python
// prototype and the renderer unchanged.
//
// Units: meters everywhere (mass in kg).
// Coordinates: X = right, Y = up, Z = forward (right-handed) — ARKit's world frame.
// Rotation: quaternion (x, y, z, w), unit length; world orientation of the local axes.
// Dimensions: full extents along LOCAL x, y, z before rotation (so index 1 is the
// vertical extent of an unrotated object). Soft/semi items keep their LOOSE size.
// IDs: stable strings, unique within a Scene, shared across scan → solver →
// physics → PAN → renderer → AR.

import Foundation

public typealias Vec3 = SIMD3<Double>

/// Quaternion (x, y, z, w). Encodes as a 4-element JSON array like the Python layer.
public struct Quat: Equatable, Hashable, Sendable {
    public var x: Double
    public var y: Double
    public var z: Double
    public var w: Double

    public init(x: Double, y: Double, z: Double, w: Double) {
        self.x = x; self.y = y; self.z = z; self.w = w
    }

    public static let identity = Quat(x: 0, y: 0, z: 0, w: 1)

    /// Rotation of `radians` about the world Y (up) axis, right-hand rule.
    public static func yaw(radians: Double) -> Quat {
        Quat(x: 0, y: sin(radians / 2), z: 0, w: cos(radians / 2))
    }
}

extension Quat: Codable {
    public init(from decoder: Decoder) throws {
        var c = try decoder.unkeyedContainer()
        x = try c.decode(Double.self)
        y = try c.decode(Double.self)
        z = try c.decode(Double.self)
        w = try c.decode(Double.self)
    }

    public func encode(to encoder: Encoder) throws {
        var c = encoder.unkeyedContainer()
        try c.encode(x); try c.encode(y); try c.encode(z); try c.encode(w)
    }
}

public struct Constraints: Codable, Equatable, Hashable, Sendable {
    public var fragile = false
    public var keepUpright = false
    public var cannotSupportWeight = false
    public var heavy = false
    /// nil | "this_side_up" | "flat_only" | "horizontal"
    public var orientationLock: String? = nil

    public init(fragile: Bool = false, keepUpright: Bool = false, cannotSupportWeight: Bool = false,
                heavy: Bool = false, orientationLock: String? = nil) {
        self.fragile = fragile
        self.keepUpright = keepUpright
        self.cannotSupportWeight = cannotSupportWeight
        self.heavy = heavy
        self.orientationLock = orientationLock
    }

    enum CodingKeys: String, CodingKey {
        case fragile
        case keepUpright = "keep_upright"
        case cannotSupportWeight = "cannot_support_weight"
        case heavy
        case orientationLock = "orientation_lock"
    }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        fragile = try c.decodeIfPresent(Bool.self, forKey: .fragile) ?? false
        keepUpright = try c.decodeIfPresent(Bool.self, forKey: .keepUpright) ?? false
        cannotSupportWeight = try c.decodeIfPresent(Bool.self, forKey: .cannotSupportWeight) ?? false
        heavy = try c.decodeIfPresent(Bool.self, forKey: .heavy) ?? false
        orientationLock = try c.decodeIfPresent(String.self, forKey: .orientationLock)
    }
}

public enum Rigidity: String, Codable, Equatable, Hashable, Sendable {
    case rigid, semi, soft
}

/// One packable item. Named `SceneObject` (not `Object`) to avoid clashing with
/// Objective-C / Foundation names on Apple platforms; JSON key names are unchanged.
public struct SceneObject: Codable, Equatable, Hashable, Sendable {
    public var id: String
    public var dimensions: Vec3
    public var position: Vec3
    public var rotation: Quat = .identity
    public var massKg: Double = 1.0
    public var constraints = Constraints()
    /// Rigid = no compression tolerance, ever.
    public var rigidity: Rigidity = .rigid
    /// >= 1.0. A soft/semi item's loose volume V can occupy a void as small as V/k.
    /// Ignored when `rigidity == .rigid`.
    public var compressibilityK: Double = 1.0
    /// Convex footprint polygon in LOCAL (x, z), meters, any vertex order (hulled
    /// on precompute), every point within +-dimensions.x/2 x +-dimensions.z/2.
    /// nil = rectangular footprint (plain box). See physics/schema.py's module
    /// docstring for the model this mirrors.
    public var footprint: [FootprintPoint]? = nil

    public init(id: String, dimensions: Vec3, position: Vec3, rotation: Quat = .identity,
                massKg: Double = 1.0, constraints: Constraints = Constraints(),
                rigidity: Rigidity = .rigid, compressibilityK: Double = 1.0,
                footprint: [FootprintPoint]? = nil) {
        self.id = id
        self.dimensions = dimensions
        self.position = position
        self.rotation = rotation
        self.massKg = massKg
        self.constraints = constraints
        self.rigidity = rigidity
        self.compressibilityK = compressibilityK
        self.footprint = footprint
    }

    enum CodingKeys: String, CodingKey {
        case id, dimensions, position, rotation, constraints, rigidity, footprint
        case massKg = "mass_kg"
        case compressibilityK = "compressibility_k"
    }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        id = try c.decode(String.self, forKey: .id)
        dimensions = try c.decode(Vec3.self, forKey: .dimensions)
        position = try c.decode(Vec3.self, forKey: .position)
        rotation = try c.decodeIfPresent(Quat.self, forKey: .rotation) ?? .identity
        massKg = try c.decodeIfPresent(Double.self, forKey: .massKg) ?? 1.0
        constraints = try c.decodeIfPresent(Constraints.self, forKey: .constraints) ?? Constraints()
        rigidity = try c.decodeIfPresent(Rigidity.self, forKey: .rigidity) ?? .rigid
        compressibilityK = try c.decodeIfPresent(Double.self, forKey: .compressibilityK) ?? 1.0
        footprint = try c.decodeIfPresent([FootprintPoint].self, forKey: .footprint)
    }
}

public struct Container: Codable, Equatable, Hashable, Sendable {
    public var id: String
    public var dimensions: Vec3
    public var position: Vec3 = .zero
    public var rotation: Quat = .identity

    public init(id: String, dimensions: Vec3, position: Vec3 = .zero, rotation: Quat = .identity) {
        self.id = id
        self.dimensions = dimensions
        self.position = position
        self.rotation = rotation
    }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        id = try c.decode(String.self, forKey: .id)
        dimensions = try c.decode(Vec3.self, forKey: .dimensions)
        position = try c.decodeIfPresent(Vec3.self, forKey: .position) ?? .zero
        rotation = try c.decodeIfPresent(Quat.self, forKey: .rotation) ?? .identity
    }

    enum CodingKeys: String, CodingKey { case id, dimensions, position, rotation }
}

public struct Scene: Codable, Equatable, Hashable, Sendable {
    public var container: Container
    public var objects: [SceneObject]

    public init(container: Container, objects: [SceneObject]) {
        self.container = container
        self.objects = objects
    }

    public func object(id: String) -> SceneObject? {
        objects.first { $0.id == id }
    }
}
