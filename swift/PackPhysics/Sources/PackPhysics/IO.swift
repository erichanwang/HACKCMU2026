// JSON integration boundary for the physics layer. Mirrors `physics/io.py`:
// 1. Round-trip Scene <-> JSON (`sceneToJSON`/`sceneFromJSON`) and dump a
//    `ValidationResult` to JSON (`resultToJSON`/`resultFromJSON`).
// 2. Adapt the iOS LiDAR scanner's shapes into `SceneObject`s: `sceneObject(from:)`
//    (the scanner's `ScannedItem`, centimetres, no pose) and `sceneObject(id:widthM:...)`
//    (the scanner's `BoxFit`, metres, yaw-only world pose).
// 3. Adapt the packing solver's placement format (`Placement`/`applyPlacements`),
//    accepting both `position`/`rotation` and `target_position`/`target_rotation`
//    spellings used across the team's docs.
// 4. Model the PAN renderer contract (`candidates.json`) as plain Codable data.
//
// A `SceneObject` built by `sceneObject(from:)` has no real pose (the scanner
// supplies none) -- callers pass a placeholder `position`/`rotation` (default:
// origin/identity). That is a legitimate input to hand the packing *solver*
// (which only cares about dimensions), but not a meaningful input to
// `validateLayout` until the solver (or `applyPlacements`) has given it a
// real pose.

import Foundation

// MARK: - Scene / Result JSON round-trip

public func sceneFromJSON(_ data: Data) throws -> Scene {
    try JSONDecoder().decode(Scene.self, from: data)
}

public func sceneToJSON(_ scene: Scene, pretty: Bool = false) throws -> Data {
    let encoder = JSONEncoder()
    encoder.outputFormatting = pretty ? [.prettyPrinted, .sortedKeys] : [.sortedKeys]
    return try encoder.encode(scene)
}

public func resultToJSON(_ r: ValidationResult, pretty: Bool = false) throws -> Data {
    let encoder = JSONEncoder()
    encoder.outputFormatting = pretty ? [.prettyPrinted, .sortedKeys] : [.sortedKeys]
    return try encoder.encode(r)
}

public func resultFromJSON(_ data: Data) throws -> ValidationResult {
    try JSONDecoder().decode(ValidationResult.self, from: data)
}

// MARK: - LiDAR scanner adapters

/// The iOS scanner's per-item output (see `Spike/Geometry.swift`): dimensions
/// only, in **centimetres**, no pose. Keys are exactly `id`, `width`, `depth`,
/// `height` -- both a plain string id and a UUID's string form decode the
/// same way, since either is just a JSON string.
public struct ScannedItem: Codable, Equatable {
    public var id: String
    public var width: Double
    public var depth: Double
    public var height: Double

    public init(id: String, width: Double, depth: Double, height: Double) {
        self.id = id
        self.width = width
        self.depth = depth
        self.height = height
    }
}

/// Adapt a `ScannedItem` (centimetres, no pose) into a `SceneObject`.
///
/// cm -> m (/100); `dimensions = (width_m, height_m, depth_m)` so the
/// scanner's width lands on local x, height on local y (vertical, unrotated),
/// depth on local z -- matching `Schema.swift`'s axis convention.
///
/// The scanner never has a pose for the item, so `position`/`rotation`
/// default to the origin/identity; pass real values once a solver or
/// `applyPlacements` has placed it.
public func sceneObject(
    from item: ScannedItem,
    position: Vec3 = .zero,
    rotation: Quat = .identity,
    massKg: Double = 1,
    constraints: Constraints = Constraints(),
    rigidity: Rigidity = .rigid,
    compressibilityK: Double = 1
) -> SceneObject {
    SceneObject(
        id: item.id,
        dimensions: Vec3(item.width / 100.0, item.height / 100.0, item.depth / 100.0),
        position: position,
        rotation: rotation,
        massKg: massKg,
        constraints: constraints,
        rigidity: rigidity,
        compressibilityK: compressibilityK
    )
}

/// Adapt a `BoxFit` (metres, yaw-only world pose -- see `Spike/Geometry.swift`)
/// into a posed `SceneObject`.
///
/// `axis` is the world unit vector of the box's WIDTH edge. Rotating local +x
/// about world Y by theta gives `(cos theta, 0, -sin theta)`, so matching that
/// to `axis` gives `theta = atan2(-axis.z, axis.x)`; `Quat.yaw(radians:)`
/// builds the corresponding quaternion. Verified numerically in
/// `IOTests.testBoxFitOrientation*` (`obbVertices` of the resulting
/// `SceneObject` reproduces `axis` and `widthM` exactly).
public func sceneObject(
    id: String,
    widthM: Double,
    depthM: Double,
    heightM: Double,
    center: Vec3,
    axis: Vec3,
    massKg: Double = 1,
    constraints: Constraints = Constraints(),
    rigidity: Rigidity = .rigid,
    compressibilityK: Double = 1
) -> SceneObject {
    let theta = atan2(-axis.z, axis.x)
    return SceneObject(
        id: id,
        dimensions: Vec3(widthM, heightM, depthM),
        position: center,
        rotation: .yaw(radians: theta),
        massKg: massKg,
        constraints: constraints,
        rigidity: rigidity,
        compressibilityK: compressibilityK
    )
}

/// Float convenience over the same formula, for the scanner's `SIMD3<Float>`
/// `BoxFit.center`/`.axis`.
public func sceneObject(
    id: String,
    widthM: Double,
    depthM: Double,
    heightM: Double,
    center: SIMD3<Float>,
    axis: SIMD3<Float>,
    massKg: Double = 1,
    constraints: Constraints = Constraints(),
    rigidity: Rigidity = .rigid,
    compressibilityK: Double = 1
) -> SceneObject {
    sceneObject(
        id: id, widthM: widthM, depthM: depthM, heightM: heightM,
        center: Vec3(Double(center.x), Double(center.y), Double(center.z)),
        axis: Vec3(Double(axis.x), Double(axis.y), Double(axis.z)),
        massKg: massKg, constraints: constraints, rigidity: rigidity, compressibilityK: compressibilityK
    )
}

// MARK: - Solver placement adapters

/// One object's new pose from the solver. Decodes from either
/// `{"id","position","rotation"}` or `{"id","target_position","target_rotation"}`
/// (both spellings appear across the team's docs). `rotation` is optional --
/// absent/null means "keep the object's current rotation" in `applyPlacements`.
public struct Placement: Codable, Equatable {
    public var id: String
    public var position: Vec3
    public var rotation: Quat?

    public init(id: String, position: Vec3, rotation: Quat? = nil) {
        self.id = id
        self.position = position
        self.rotation = rotation
    }

    enum CodingKeys: String, CodingKey {
        case id, position, rotation
        case targetPosition = "target_position"
        case targetRotation = "target_rotation"
    }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        id = try c.decode(String.self, forKey: .id)
        if let p = try c.decodeIfPresent(Vec3.self, forKey: .position) {
            position = p
        } else {
            position = try c.decode(Vec3.self, forKey: .targetPosition)
        }
        if let r = try c.decodeIfPresent(Quat.self, forKey: .rotation) {
            rotation = r
        } else {
            rotation = try c.decodeIfPresent(Quat.self, forKey: .targetRotation)
        }
    }

    public func encode(to encoder: Encoder) throws {
        var c = encoder.container(keyedBy: CodingKeys.self)
        try c.encode(id, forKey: .id)
        try c.encode(position, forKey: .position)
        try c.encodeIfPresent(rotation, forKey: .rotation)
    }
}

/// The solver's wrapper shape `{"placements": [...]}` -- a bare array `[...]`
/// works too.
public func placementsFromJSON(_ data: Data) throws -> [Placement] {
    struct Wrapper: Decodable { let placements: [Placement] }
    if let wrapper = try? JSONDecoder().decode(Wrapper.self, from: data) {
        return wrapper.placements
    }
    return try JSONDecoder().decode([Placement].self, from: data)
}

/// Return a NEW `Scene` with each placed object's pose replaced. Matched by
/// `id`; objects not mentioned keep their current pose. Unknown ids throw
/// `MalformedSceneError` naming the first one. Never mutates `scene` or
/// `placements` (value types -- Swift does this for free).
public func applyPlacements(_ scene: Scene, _ placements: [Placement]) throws -> Scene {
    let known = Set(scene.objects.map(\.id))
    var unknown: [String] = []
    for p in placements where !known.contains(p.id) && !unknown.contains(p.id) {
        unknown.append(p.id)
    }
    if let first = unknown.first {
        throw MalformedSceneError(objectId: first, message: "unknown placement ids: \(unknown)")
    }

    var byId: [String: Placement] = [:]
    for p in placements { byId[p.id] = p }  // last one wins, matching Python's dict construction

    let newObjects = scene.objects.map { o -> SceneObject in
        guard let p = byId[o.id] else { return o }
        var updated = o
        updated.position = p.position
        if let r = p.rotation { updated.rotation = r }
        return updated
    }
    return Scene(container: scene.container, objects: newObjects)
}

// MARK: - PAN renderer contract (candidates.json)

/// One `CandidateReport`/step, exactly as `pan/demo.py` writes it into
/// `candidates.json` (see `docs/PAN_INTEGRATION.md`). Purely data -- no PAN
/// logic lives here.
public struct PANCandidateReport: Codable, Equatable {
    public var candidateId: String
    public var label: String
    public var physicsStatus: String
    public var simulationStatus: String
    public var executionRisk: String?
    public var actionText: String
    public var panPreviewVideo: String?
    public var panFinalFrame: String?
    public var riskMetadata: JSONValue?
    public var scoreComponents: [String: JSONValue]

    enum CodingKeys: String, CodingKey {
        case candidateId = "candidate_id"
        case label
        case physicsStatus = "physics_status"
        case simulationStatus = "simulation_status"
        case executionRisk = "execution_risk"
        case actionText = "action_text"
        case panPreviewVideo = "pan_preview_video"
        case panFinalFrame = "pan_final_frame"
        case riskMetadata = "risk_metadata"
        case scoreComponents = "score_components"
    }

    public init(
        candidateId: String, label: String, physicsStatus: String, simulationStatus: String,
        executionRisk: String?, actionText: String, panPreviewVideo: String?, panFinalFrame: String?,
        riskMetadata: JSONValue?, scoreComponents: [String: JSONValue]
    ) {
        self.candidateId = candidateId
        self.label = label
        self.physicsStatus = physicsStatus
        self.simulationStatus = simulationStatus
        self.executionRisk = executionRisk
        self.actionText = actionText
        self.panPreviewVideo = panPreviewVideo
        self.panFinalFrame = panFinalFrame
        self.riskMetadata = riskMetadata
        self.scoreComponents = scoreComponents
    }
}

/// A step-level report has the same shape as a candidate-level one (rolled up
/// vs. finer-grained), so `pan/demo.py` writes both from the same
/// `CandidateReport.to_dict()`.
public typealias PANStepReport = PANCandidateReport

/// `out_dir/candidates.json` as written by `pan/demo.py`'s `run_demo`.
public struct PANCandidatesFile: Codable, Equatable {
    public var backend: String
    public var panAvailable: Bool
    public var returnedAfterMs: Double
    public var candidates: [PANCandidateReport]
    public var steps: [PANStepReport]

    enum CodingKeys: String, CodingKey {
        case backend
        case panAvailable = "pan_available"
        case returnedAfterMs = "returned_after_ms"
        case candidates, steps
    }

    public init(
        backend: String, panAvailable: Bool, returnedAfterMs: Double,
        candidates: [PANCandidateReport], steps: [PANStepReport]
    ) {
        self.backend = backend
        self.panAvailable = panAvailable
        self.returnedAfterMs = returnedAfterMs
        self.candidates = candidates
        self.steps = steps
    }
}

public func panCandidates(from data: Data) throws -> PANCandidatesFile {
    try JSONDecoder().decode(PANCandidatesFile.self, from: data)
}
