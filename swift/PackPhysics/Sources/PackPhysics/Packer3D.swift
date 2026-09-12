// Adapter for the `packer3d` solver's output JSON (see
// `packer3d/README.md` "Output JSON contract" / "Coordinates") into
// PackPhysics `Scene`s, so the iOS app can validate/render solver results
// directly. Decoding models only -- the solver itself is a separate Python
// process; nothing here re-implements packing.
//
// ## Frame mapping (shared spec -- physics/packer3d_adapter.py implements the
// identical formulas; keep both in lockstep)
//
// packer3d: right-handed, x = length (L), y = width (W), z = UP (H), origin
// at the container's MIN corner; `placement.position` = MIN corner of the
// oriented bbox, `dims` = oriented bbox (orientation permutation already
// applied), `center = position + dims/2`.
//
// PackPhysics (Schema.swift): right-handed, X = right, Y = UP, Z = forward,
// meters, container centered at its `position`, object `dimensions` along
// local axes, quaternion (x,y,z,w).
//
// The proper rotation (det +1) sending packer z -> physics Y and packer
// y -> physics -Z is `physicsPoint(x, y, z) = (x, z, -y)`. Every mapping
// below is that single formula applied to points (position/center) and,
// ignoring sign (extents have none), to dims (swap y/z).
//
// packer3d's orientation permutation is already baked into each placement's
// `dims` (the oriented bbox), so every mapped object keeps `rotation: .identity`
// by default. `scene(fromPacker3D:oriented: false)` gives the other (equivalent)
// form -- the item's own dims with the orientation as the pose; see its doc
// comment, and `rotationFromOrientation` for the quaternion.

import Foundation

// MARK: - Frame mapping

/// packer3d point (x, y, z) -> PackPhysics point (X, Y, Z): `(x, z, -y)`.
@inlinable public func physicsPoint(_ x: Double, _ y: Double, _ z: Double) -> Vec3 {
    Vec3(x, z, -y)
}

/// Convenience over `Vec3`.
@inlinable public func physicsPoint(_ p: Vec3) -> Vec3 { physicsPoint(p.x, p.y, p.z) }

/// Inverse of `physicsPoint`: PackPhysics point -> packer3d point.
@inlinable public func packer3dPoint(_ p: Vec3) -> Vec3 { Vec3(p.x, -p.z, p.y) }

/// Extents (dims) transform by the same axis swap as points, but signs don't
/// matter for a size -- this permutation is its own inverse, so it is used
/// both packer3d -> physics and physics -> packer3d.
@inlinable public func swapYZ(_ v: Vec3) -> Vec3 { Vec3(v.x, v.z, v.y) }

// MARK: - Orientation -> quaternion

/// packer3d orientation name -> axis permutation: world axis k takes the item's
/// own axis `perm[k]` (`packer3d.models.BOX_ORIENTATIONS`). A cylinder's bbox
/// permutes the same way, since its item frame is (2r, 2r, h) with the axis
/// along item z. An unknown name falls back to "xyz" (Python raises `KeyError`;
/// nothing on this side is allowed to trap on decoded JSON).
private let orientationPerm: [String: [Int]] = [
    "xyz": [0, 1, 2], "xzy": [0, 2, 1], "yxz": [1, 0, 2],
    "yzx": [1, 2, 0], "zxy": [2, 0, 1], "zyx": [2, 1, 0],
    "cyl_axis_z": [0, 1, 2], "cyl_axis_x": [2, 1, 0], "cyl_axis_y": [0, 2, 1],
]

private func perm(_ orientation: String) -> [Int] { orientationPerm[orientation] ?? [0, 1, 2] }

/// packer3d basis -> physics basis, i.e. the matrix of `physicsPoint`.
private let packerToPhysicsBasis = Mat3(columns: Vec3(1, 0, 0), Vec3(0, 0, -1), Vec3(0, 1, 0))

/// Rotation matrix -> (x, y, z, w) (Shepperd's method, largest-component branch).
private func quatFromMatrix(_ m: Mat3) -> Quat {
    let t = m[0, 0] + m[1, 1] + m[2, 2]
    if t > 0.0 {
        let s = (t + 1.0).squareRoot() * 2.0
        return Quat(x: (m[2, 1] - m[1, 2]) / s, y: (m[0, 2] - m[2, 0]) / s,
                    z: (m[1, 0] - m[0, 1]) / s, w: 0.25 * s)
    }
    let diagonal = [m[0, 0], m[1, 1], m[2, 2]]
    let i = diagonal.firstIndex(of: diagonal.max()!)!  // numpy argmax: first maximum wins
    let (j, k) = ((i + 1) % 3, (i + 2) % 3)
    let s = (1.0 + m[i, i] - m[j, j] - m[k, k]).squareRoot() * 2.0
    var q = Vec3.zero
    q[i] = 0.25 * s
    q[j] = (m[j, i] + m[i, j]) / s
    q[k] = (m[k, i] + m[i, k]) / s
    return Quat(x: q.x, y: q.y, z: q.z, w: (m[k, j] - m[j, k]) / s)
}

/// packer3d `placement.orientation` -> physics quaternion. Needed whenever the
/// object carries its OWN (unoriented) dimensions and only the pose moves it --
/// `placements(fromPacker3D:)` + `applyPlacements`. (`scene(fromPacker3D:)`
/// instead builds objects straight from the already-oriented `placement.dims`,
/// so those keep identity rotation; both forms produce the same world OBB, which
/// the tests assert.)
///
/// Derivation: the permutation matrix `M[k, perm[k]] = 1` maps an item-frame
/// vector to the packer world frame. An odd permutation has det -1 (a reflection,
/// not a rotation), so one column is negated -- a 180-degree flip, which leaves a
/// box's (symmetric) extents untouched and makes det +1. The physics-frame
/// rotation is then `R = P M P^T` with `P` = the matrix of `physicsPoint`.
public func rotationFromOrientation(_ orientation: String) -> Quat {
    var columns = [Vec3.zero, Vec3.zero, Vec3.zero]
    for (k, src) in perm(orientation).enumerated() { columns[src][k] = 1.0 }
    var m = Mat3(columns: columns[0], columns[1], columns[2])
    if dot(cross(m.c0, m.c1), m.c2) < 0.0 { m.c0 = -m.c0 }
    return quatFromMatrix(packerToPhysicsBasis * m * packerToPhysicsBasis.transposed)
}

/// Undo the solver's axis permutation: the oriented bbox `placement.dims` -> the
/// item's OWN extents. `M` above puts item axis `perm[k]` on world axis `k`, so
/// extents map back with `own[perm[k]] = dims[k]`. Pairs with
/// `rotationFromOrientation`.
private func unorientedDims(_ dims: Vec3, _ orientation: String) -> Vec3 {
    var own = Vec3.zero
    for (k, src) in perm(orientation).enumerated() { own[src] = dims[k] }
    return own
}

// MARK: - Codable models (packer3d result JSON contract)

/// `{"id", "position", "dims"}` -- `position` is the obstacle's MIN corner
/// (like a placement), not its center.
public struct Packer3DObstacle: Codable, Equatable {
    public var id: String
    public var position: Vec3
    public var dims: Vec3

    public init(id: String, position: Vec3, dims: Vec3) {
        self.id = id
        self.position = position
        self.dims = dims
    }
}

/// `container` block of a packer3d result OR a scenario file. Both shapes
/// are the same superset (a scenario simply omits `obstacles`/`com_target`/
/// `com_axis_weights`, which default the same way `packer3d.scenario`
/// defaults them), so one type serves both -- no near-duplicate second type.
public struct Packer3DContainer: Codable, Equatable {
    public var id: String
    public var shape: String
    public var dims: Vec3
    public var gravity: Bool
    public var maxMass: Double?
    public var minSupport: Double?
    public var obstacles: [Packer3DObstacle]
    public var comTarget: Vec3?
    public var comAxisWeights: Vec3?

    public init(id: String = "container", shape: String = "box", dims: Vec3, gravity: Bool = true,
                maxMass: Double? = nil, minSupport: Double? = nil, obstacles: [Packer3DObstacle] = [],
                comTarget: Vec3? = nil, comAxisWeights: Vec3? = nil) {
        self.id = id
        self.shape = shape
        self.dims = dims
        self.gravity = gravity
        self.maxMass = maxMass
        self.minSupport = minSupport
        self.obstacles = obstacles
        self.comTarget = comTarget
        self.comAxisWeights = comAxisWeights
    }

    enum CodingKeys: String, CodingKey {
        case id, shape, dims, gravity, obstacles
        case maxMass = "max_mass"
        case minSupport = "min_support"
        case comTarget = "com_target"
        case comAxisWeights = "com_axis_weights"
    }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        id = try c.decodeIfPresent(String.self, forKey: .id) ?? "container"
        shape = try c.decodeIfPresent(String.self, forKey: .shape) ?? "box"
        dims = try c.decode(Vec3.self, forKey: .dims)
        gravity = try c.decodeIfPresent(Bool.self, forKey: .gravity) ?? true
        maxMass = try c.decodeIfPresent(Double.self, forKey: .maxMass)
        minSupport = try c.decodeIfPresent(Double.self, forKey: .minSupport)
        obstacles = try c.decodeIfPresent([Packer3DObstacle].self, forKey: .obstacles) ?? []
        comTarget = try c.decodeIfPresent(Vec3.self, forKey: .comTarget)
        comAxisWeights = try c.decodeIfPresent(Vec3.self, forKey: .comAxisWeights)
    }
}

/// One placed item, exactly as packer3d's `Placement.to_dict()` writes it.
/// `axis`/`radius`/`height`/`scan_yaw_deg` are cylinder/mesh-only and absent
/// for boxes.
public struct Packer3DPlacement: Codable, Equatable {
    public var itemId: String
    public var shape: String
    /// MIN corner of the oriented bbox (packer3d local frame).
    public var position: Vec3
    /// Oriented bbox extents (orientation permutation already applied).
    public var dims: Vec3
    public var center: Vec3
    /// Boxes: axis permutation ("xyz", "xzy", ...). Cylinders: "cyl_axis_x/y/z".
    public var orientation: String
    public var axis: String?
    public var radius: Double?
    public var height: Double?
    public var mass: Double
    public var fragile: Bool
    public var scanYawDeg: Double?

    public init(itemId: String, shape: String, position: Vec3, dims: Vec3, center: Vec3, orientation: String,
                axis: String? = nil, radius: Double? = nil, height: Double? = nil, mass: Double,
                fragile: Bool = false, scanYawDeg: Double? = nil) {
        self.itemId = itemId
        self.shape = shape
        self.position = position
        self.dims = dims
        self.center = center
        self.orientation = orientation
        self.axis = axis
        self.radius = radius
        self.height = height
        self.mass = mass
        self.fragile = fragile
        self.scanYawDeg = scanYawDeg
    }

    enum CodingKeys: String, CodingKey {
        case shape, position, dims, center, orientation, axis, radius, height, mass, fragile
        case itemId = "item_id"
        case scanYawDeg = "scan_yaw_deg"
    }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        itemId = try c.decode(String.self, forKey: .itemId)
        shape = try c.decode(String.self, forKey: .shape)
        position = try c.decode(Vec3.self, forKey: .position)
        dims = try c.decode(Vec3.self, forKey: .dims)
        center = try c.decode(Vec3.self, forKey: .center)
        orientation = try c.decode(String.self, forKey: .orientation)
        axis = try c.decodeIfPresent(String.self, forKey: .axis)
        radius = try c.decodeIfPresent(Double.self, forKey: .radius)
        height = try c.decodeIfPresent(Double.self, forKey: .height)
        mass = try c.decode(Double.self, forKey: .mass)
        fragile = try c.decodeIfPresent(Bool.self, forKey: .fragile) ?? false
        scanYawDeg = try c.decodeIfPresent(Double.self, forKey: .scanYawDeg)
    }
}

/// `{"id", "reason"}` -- an item the solver could not place.
public struct Packer3DUnpacked: Codable, Equatable {
    public var id: String
    public var reason: String

    public init(id: String, reason: String) {
        self.id = id
        self.reason = reason
    }
}

/// One packer3d run (either the bare result, or one branch of a `--compare`
/// wrapper). `metrics` is a free-form `JSONValue` (the exact dictionary
/// packer3d's `objective.py` emits); `stats` likewise, and may be absent.
public struct Packer3DResult: Codable, Equatable {
    public var strategy: String
    public var container: Packer3DContainer
    public var placements: [Packer3DPlacement]
    public var unpacked: [Packer3DUnpacked]
    public var metrics: JSONValue
    public var stats: JSONValue?

    public init(strategy: String, container: Packer3DContainer, placements: [Packer3DPlacement],
                unpacked: [Packer3DUnpacked], metrics: JSONValue, stats: JSONValue? = nil) {
        self.strategy = strategy
        self.container = container
        self.placements = placements
        self.unpacked = unpacked
        self.metrics = metrics
        self.stats = stats
    }
}

/// `--compare` output: `{"naive": {...}, "optimized": {...}}`.
public struct Packer3DCompare: Codable, Equatable {
    public var naive: Packer3DResult?
    public var optimized: Packer3DResult?

    public init(naive: Packer3DResult?, optimized: Packer3DResult?) {
        self.naive = naive
        self.optimized = optimized
    }
}

// MARK: - Codable models (scenario JSON, `packer3d.scenario.load_scenario`)

/// One scenario item. Only the plain box/cylinder form is modeled (the
/// lidar-payload `length`/`depth`/`allow_lay_down` spelling from
/// `packer3d.scenario` is a separate input path the frontend doesn't need
/// here) -- add it if a scenario using that form shows up.
public struct Packer3DScenarioItem: Codable, Equatable {
    public var id: String
    public var shape: String
    public var dims: Vec3?
    public var radius: Double?
    public var height: Double?
    public var mass: Double
    public var fragile: Bool
    public var keepUpright: Bool
    public var priority: Double
    public var count: Int

    public init(id: String, shape: String = "box", dims: Vec3? = nil, radius: Double? = nil, height: Double? = nil,
                mass: Double = 0.0, fragile: Bool = false, keepUpright: Bool = false, priority: Double = 1.0,
                count: Int = 1) {
        self.id = id
        self.shape = shape
        self.dims = dims
        self.radius = radius
        self.height = height
        self.mass = mass
        self.fragile = fragile
        self.keepUpright = keepUpright
        self.priority = priority
        self.count = count
    }

    enum CodingKeys: String, CodingKey {
        case id, shape, dims, radius, height, mass, fragile, priority, count
        case keepUpright = "keep_upright"
    }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        id = try c.decode(String.self, forKey: .id)
        shape = try c.decodeIfPresent(String.self, forKey: .shape) ?? "box"
        dims = try c.decodeIfPresent(Vec3.self, forKey: .dims)
        radius = try c.decodeIfPresent(Double.self, forKey: .radius)
        height = try c.decodeIfPresent(Double.self, forKey: .height)
        mass = try c.decodeIfPresent(Double.self, forKey: .mass) ?? 0.0
        fragile = try c.decodeIfPresent(Bool.self, forKey: .fragile) ?? false
        keepUpright = try c.decodeIfPresent(Bool.self, forKey: .keepUpright) ?? false
        priority = try c.decodeIfPresent(Double.self, forKey: .priority) ?? 1.0
        count = try c.decodeIfPresent(Int.self, forKey: .count) ?? 1
    }
}

/// A scenario file: `{"container": {...}, "items": [...]}` (the
/// `optimizer`/`weights` blocks are solver tuning knobs the frontend never
/// needs, so they aren't modeled).
public struct Packer3DScenario: Codable, Equatable {
    public var container: Packer3DContainer
    public var items: [Packer3DScenarioItem]

    public init(container: Packer3DContainer, items: [Packer3DScenarioItem]) {
        self.container = container
        self.items = items
    }
}

// MARK: - Decoders

public struct Packer3DDecodeError: Error, CustomStringConvertible, Equatable {
    public let description: String
}

/// Decode a packer3d result JSON. Accepts either a bare `Packer3DResult` or a
/// `--compare` wrapper `{"naive": {...}, "optimized": {...}}`; for the
/// wrapper, `strategy` (default `"optimized"`) picks the branch. Throws
/// `Packer3DDecodeError` if the requested branch is absent from a wrapper.
public func packer3dResult(from data: Data, strategy: String = "optimized") throws -> Packer3DResult {
    let decoder = JSONDecoder()
    let compare = try decoder.decode(Packer3DCompare.self, from: data)
    if compare.naive != nil || compare.optimized != nil {
        switch strategy {
        case "naive":
            guard let r = compare.naive else {
                throw Packer3DDecodeError(description: "packer3d compare result has no \"naive\" branch")
            }
            return r
        case "optimized":
            guard let r = compare.optimized else {
                throw Packer3DDecodeError(description: "packer3d compare result has no \"optimized\" branch")
            }
            return r
        default:
            throw Packer3DDecodeError(description: "packer3d compare result has no \"\(strategy)\" branch (expected \"naive\" or \"optimized\")")
        }
    }
    // Not a compare wrapper (both branches absent) -- decode as a bare result.
    return try decoder.decode(Packer3DResult.self, from: data)
}

/// Decode a packer3d scenario JSON (`packer3d.scenario.load_scenario`'s input shape).
public func packer3dScenario(from data: Data) throws -> Packer3DScenario {
    try JSONDecoder().decode(Packer3DScenario.self, from: data)
}

// MARK: - Scenario item id expansion

/// Expand one scenario item's `count` into placement/unpacked ids, exactly
/// like packer3d's `scenario._item_from_dict`: `count == 1` -> `[id]`;
/// `count > 1` -> `[id_1, id_2, ..., id_n]`.
public func expandedIds(_ item: Packer3DScenarioItem) -> [String] {
    item.count <= 1 ? [item.id] : (1...item.count).map { "\(item.id)_\($0)" }
}

/// Every expanded id of every scenario item with `keep_upright: true` -- used
/// to attach `keepUpright` to placements, since packer3d's placement JSON
/// doesn't carry it (only the scenario item does).
private func expandedKeepUprightIds(_ scenario: Packer3DScenario) -> Set<String> {
    var ids: Set<String> = []
    for item in scenario.items where item.keepUpright {
        ids.formUnion(expandedIds(item))
    }
    return ids
}

// MARK: - Adapters: packer3d -> PackPhysics

/// Map a packer3d `container` block to a PackPhysics `Container`.
/// `dims = (L, W, H)` -> `(L, H, W)`; the min-corner-origin container's
/// center `(L/2, W/2, H/2)` maps to `(L/2, H/2, -W/2)`.
public func container(fromPacker3D c: Packer3DContainer) -> Container {
    Container(id: c.id, dimensions: swapYZ(c.dims), position: physicsPoint(c.dims / 2.0), rotation: .identity)
}

/// Map one placement to a `SceneObject`. `keepUpright` isn't in the placement
/// JSON (only the scenario item has it) -- pass it in from the scenario, or
/// leave `false` when validating a result with no scenario at hand. `oriented`
/// picks the form (see `scene(fromPacker3D:scenario:includeObstacles:oriented:)`).
public func sceneObject(fromPlacement p: Packer3DPlacement, keepUpright: Bool = false,
                        oriented: Bool = true) -> SceneObject {
    SceneObject(
        id: p.itemId,
        dimensions: swapYZ(oriented ? p.dims : unorientedDims(p.dims, p.orientation)),
        position: physicsPoint(p.center),
        rotation: oriented ? .identity : rotationFromOrientation(p.orientation),
        massKg: p.mass,
        constraints: Constraints(fragile: p.fragile, keepUpright: keepUpright, cannotSupportWeight: p.fragile),
        rigidity: .rigid
    )
}

/// Map one obstacle to a mass-0 rigid `SceneObject` named `"obstacle:<id>"`.
/// `position` is the obstacle's MIN corner (like a placement), so its center
/// is `position + dims/2` before the frame mapping.
public func sceneObject(fromObstacle o: Packer3DObstacle) -> SceneObject {
    SceneObject(
        id: "obstacle:\(o.id)",
        dimensions: swapYZ(o.dims),
        position: physicsPoint(o.position + o.dims / 2.0),
        rotation: .identity,
        massKg: 0,
        constraints: Constraints(),
        rigidity: .rigid
    )
}

/// Build a validate-able `Scene` from a packer3d result: every placement plus
/// (by default) every container obstacle. Pass `scenario` so `keep_upright`
/// (scenario-only metadata) reaches the mapped placements. Also returns the
/// solver's `unpacked` list (never part of the scene) and a lookup back to
/// each placement's original packer3d record -- shape/radius/height/axis for
/// a renderer that wants to draw a real cylinder instead of its bounding box.
///
/// `oriented` picks which of the two equivalent forms the placed objects take.
/// Both occupy exactly the same world box, so the validator's verdict is the same:
///
/// * `true` (default) -- the solver's already-oriented `dims` with identity
///   rotation. Axis-aligned and exact, so this stays the canonical form for
///   everything that only GRADES a finished layout (`validatePacker3D`, the CLI):
///   those never apply placements on top.
/// * `false` -- the item's own dims with the orientation carried in the pose. Use
///   this, and only this, when the scene is then moved by
///   `placements(fromPacker3D:)` (`applyPlacements`); pairing those rotations with
///   the default oriented dims applies the permutation twice and the physics gate
///   rejects the solver's own valid plan.
public func scene(
    fromPacker3D result: Packer3DResult, scenario: Packer3DScenario? = nil, includeObstacles: Bool = true,
    oriented: Bool = true
) -> (scene: Scene, unpacked: [Packer3DUnpacked], shapes: [String: Packer3DPlacement]) {
    let keepUprightIds = scenario.map(expandedKeepUprightIds) ?? []
    var objects = result.placements.map { p in
        sceneObject(fromPlacement: p, keepUpright: keepUprightIds.contains(p.itemId), oriented: oriented)
    }
    if includeObstacles {
        objects += result.container.obstacles.map(sceneObject(fromObstacle:))
    }
    let shapes = Dictionary(uniqueKeysWithValues: result.placements.map { ($0.itemId, $0) })
    return (Scene(container: container(fromPacker3D: result.container), objects: objects), result.unpacked, shapes)
}

/// Map a packer3d result's placements to our `Placement` (IO.swift) records.
/// `rotation` is `rotationFromOrientation(p.orientation)`, NOT identity: those
/// objects carry their own unoriented dimensions, so the solver's axis
/// permutation has to travel in the pose or the item lands rotated 90 degrees
/// wrong.
///
/// So the scene these are applied to must be in the own-dims form --
/// `unpackedState(fromScenario:)` or `scene(fromPacker3D:oriented: false)`, never
/// the default oriented scene (that double-rotates; see `scene(fromPacker3D:)`).
public func placements(fromPacker3D result: Packer3DResult) -> [Placement] {
    result.placements.map { p in
        Placement(id: p.itemId, position: physicsPoint(p.center),
                  rotation: rotationFromOrientation(p.orientation))
    }
}

/// The "before" state a scenario describes: the container plus every
/// expanded item laid out OUTSIDE it in a row along +X (never packed), so a
/// solver run can be visualized starting from "everything unpacked". Row
/// layout: first item's near face at `X = L + 0.15`; each following item
/// starts `previous item's length + 0.05` after the previous one's start;
/// every item rests on the floor (`Y = dims_y/2`) at a constant depth
/// (`Z = -W/2`, the container's own mapped center).
public func unpackedState(fromScenario scenario: Packer3DScenario) -> Scene {
    let c = scenario.container
    let rowZ = -c.dims.y / 2.0
    var nextStartX = c.dims.x + 0.15
    var objects: [SceneObject] = []
    for item in scenario.items {
        let packerDims: Vec3
        if item.shape == "cylinder" {
            let d = 2.0 * (item.radius ?? 0)
            packerDims = Vec3(d, d, item.height ?? 0)
        } else {
            packerDims = item.dims ?? Vec3(0, 0, 0)
        }
        let physDims = swapYZ(packerDims)  // (length, height, depth) slot order
        let constraints = Constraints(fragile: item.fragile, keepUpright: item.keepUpright, cannotSupportWeight: item.fragile)
        for id in expandedIds(item) {
            let position = Vec3(nextStartX + packerDims.x / 2.0, physDims.y / 2.0, rowZ)
            objects.append(SceneObject(
                id: id, dimensions: physDims, position: position, rotation: .identity,
                massKg: item.mass, constraints: constraints, rigidity: .rigid
            ))
            nextStartX += packerDims.x + 0.05
        }
    }
    return Scene(container: container(fromPacker3D: c), objects: objects)
}

/// Convenience: build the scene from `result` (+ `scenario` for
/// `keep_upright`) and run it through `validateLayout`.
public func validatePacker3D(_ result: Packer3DResult, scenario: Packer3DScenario? = nil) -> ValidationResult {
    let built = scene(fromPacker3D: result, scenario: scenario)
    return validateLayout(built.scene)
}
