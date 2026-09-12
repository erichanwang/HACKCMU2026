import Foundation

/// Declared unit system of a plan file. Exists purely so a plan that forgets the
/// project's metre convention fails loudly at the boundary instead of rendering
/// a suitcase 100× too large.
public enum PlanUnits: String, Codable, Sendable {
    case meters
}

/// A named sub-volume of the container the solver assigns placements to
/// (e.g. a base layer, or a lid pocket).
///
/// `origin` is the zone's min corner in the bag frame, same convention as a
/// placement's `position`.
public struct Zone: Codable, Hashable, Identifiable, Sendable {
    public let id: String
    public let label: String
    public let origin: Vector3
    public let size: Vector3

    public init(id: String, label: String, origin: Vector3, size: Vector3) {
        self.id = id
        self.label = label
        self.origin = origin
        self.size = size
    }

    public var box: BoundingBox {
        BoundingBox(minCorner: origin, size: size)
    }
}

/// The bag interior. `dimensions` describes the interior cavity only — not the
/// outer shell and not the lid.
public struct Container: Codable, Hashable, Identifiable, Sendable {
    public let id: String
    public let label: String
    public let dimensions: Vector3
    public let zones: [Zone]

    public init(id: String, label: String, dimensions: Vector3, zones: [Zone]) {
        self.id = id
        self.label = label
        self.dimensions = dimensions
        self.zones = zones
    }

    /// The valid coordinate range for every placement: `[0, dimensions]` on each axis.
    public var interior: BoundingBox {
        BoundingBox(minCorner: .zero, size: dimensions)
    }

    public func zone(id: String) -> Zone? {
        zones.first { $0.id == id }
    }
}

/// The solver's claim that a placement sits inside another item's scanned cavity —
/// socks in a shoe, a charger in the dip of a dopp kit. Such a pair shares volume
/// legitimately, which is why the geometry checks need to be told about it.
///
/// It is an **assertion by the producer, not a fact**. `geometryIssues()` honours it
/// only as far as `cavity` reaches: overlap outside that box is still an overlap.
/// A host the plan does not contain, a self-reference, or a loop of hosts is
/// ignored outright — see `honouredNesting()`.
public struct Nesting: Codable, Hashable, Sendable {
    /// `Placement.itemID` of the host whose cavity holds this item.
    public let itemID: String

    /// The host's cavity, in the **bag frame**: min corner plus full extent, the
    /// same convention as `Placement.position` and `Placement.size`.
    public let cavity: Cavity

    public struct Cavity: Codable, Hashable, Sendable {
        /// **Min corner** of the cavity, not its center.
        public let position: Vector3
        public let size: Vector3

        public init(position: Vector3, size: Vector3) {
            self.position = position
            self.size = size
        }

        public var box: BoundingBox {
            BoundingBox(minCorner: position, size: size)
        }
    }

    public init(itemID: String, cavity: Cavity) {
        self.itemID = itemID
        self.cavity = cavity
    }

    private enum CodingKeys: String, CodingKey {
        case itemID = "itemId"
        case cavity
    }
}

/// One item in one spot, at one step of the packing sequence.
public struct Placement: Codable, Hashable, Identifiable, Sendable {
    /// 1-based position in the packing sequence. The AR view highlights the
    /// current step and dims lower ones.
    public let step: Int

    /// Stable identifier from the solver's item list.
    public let itemID: String

    /// Human-readable name, shown in the UI.
    public let label: String

    /// `Zone.id` this placement belongs to.
    public let zone: String

    /// **Min corner** of the item's bounding box, in metres, bag frame.
    /// Not the center — renderers must add `size / 2`.
    public let position: Vector3

    /// Full extent in bag axes, in metres, already accounting for `rotation`.
    public let size: Vector3

    public let rotation: AxisRotation

    /// One short line of guidance shown with the step ("Soles down, heels to the
    /// end wall"). Presentation only; never parsed.
    public let note: String

    /// Set when the solver put this item inside another item's cavity. Absent or
    /// `null` in the JSON — the common case — decodes to `nil`.
    public let nestedIn: Nesting?

    public init(
        step: Int,
        itemID: String,
        label: String,
        zone: String,
        position: Vector3,
        size: Vector3,
        rotation: AxisRotation,
        note: String,
        nestedIn: Nesting? = nil
    ) {
        self.step = step
        self.itemID = itemID
        self.label = label
        self.zone = zone
        self.position = position
        self.size = size
        self.rotation = rotation
        self.note = note
        self.nestedIn = nestedIn
    }

    public var id: String { itemID }

    /// The volume this placement occupies: `[position, position + size]`.
    public var box: BoundingBox {
        BoundingBox(minCorner: position, size: size)
    }

    /// Where a RealityKit box entity for this item goes. The whole reason this
    /// property exists is so no call site writes the offset by hand.
    public var renderCenter: Vector3 { box.center }

    private enum CodingKeys: String, CodingKey {
        case step
        case itemID = "itemId"
        case label
        case zone
        case position
        case size
        case rotation
        case note
        case nestedIn
    }
}

/// A complete packing plan as emitted by the solver.
public struct PackingPlan: Codable, Hashable, Sendable {
    public let version: Int
    public let units: PlanUnits
    public let container: Container
    public let placements: [Placement]

    public init(
        version: Int,
        units: PlanUnits,
        container: Container,
        placements: [Placement]
    ) {
        self.version = version
        self.units = units
        self.container = container
        self.placements = placements
    }

    /// Placements in packing order.
    public var orderedPlacements: [Placement] {
        placements.sorted { $0.step < $1.step }
    }

    public func placement(step: Int) -> Placement? {
        placements.first { $0.step == step }
    }

    /// Fraction of the interior cavity the plan fills, 0...1. Useful as a
    /// sanity read-out and in the 2D view's summary.
    public var packedVolumeFraction: Float {
        let interior = container.interior.volume
        guard interior > 0 else { return 0 }
        return placements.reduce(0) { $0 + $1.box.volume } / interior
    }
}
