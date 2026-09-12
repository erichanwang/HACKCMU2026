import Foundation

/// A container scanned by the LiDAR spike and stored server-side — the bag
/// itself, as opposed to the items that go in it.
///
/// `dimensions` follows the same team contract as a scanned item:
/// `[width, height, depth]` in metres, X right, Y up, Z forward. That is already
/// this app's frame, so no axis remap is needed — unlike packer3d, which needs
/// `PackerAxes`.
public struct ScannedContainer: Codable, Hashable, Identifiable, Sendable {
    public let id: String
    public let name: String?
    public let dimensions: [Float]
    public let createdAt: String?

    public init(id: String, name: String?, dimensions: [Float], createdAt: String? = nil) {
        self.id = id
        self.name = name
        self.dimensions = dimensions
        self.createdAt = createdAt
    }

    /// The interior extent, or `nil` when the document cannot describe a box.
    public var size: Vector3? {
        guard dimensions.count == 3, dimensions.allSatisfy({ $0 > 0 && $0.isFinite }) else {
            return nil
        }
        return Vector3(dimensions[0], dimensions[1], dimensions[2])
    }

    public var label: String { name ?? "Scanned container" }
}

public enum ScannedContainerLoader {
    /// Filename of the sample container document bundled with this module.
    public static let bundledResourceName = "container"

    public static func container(from data: Data) throws -> ScannedContainer {
        do {
            return try JSONDecoder().decode(ScannedContainer.self, from: data)
        } catch let error as DecodingError {
            throw PlanError.malformedJSON(description: String(describing: error))
        }
    }

    /// A real scanned suitcase, captured by the spike and copied out of Mongo.
    /// Used so the 3D view can show a scanned bag without a live server.
    public static func bundled() throws -> ScannedContainer {
        guard let url = Bundle.module.url(
            forResource: bundledResourceName,
            withExtension: "json"
        ) else {
            throw PlanError.fileNotFound(name: "\(bundledResourceName).json")
        }
        return try container(from: try Data(contentsOf: url))
    }
}

public extension PackingPlan {
    /// The same placements, re-homed into a scanned bag.
    ///
    /// Zones are kept exactly as authored: they describe how the *plan* is
    /// organised, not how the bag is built, so a placement that was inside its
    /// zone still is.
    ///
    /// Nothing is refitted or clamped. If the scanned bag is smaller than the
    /// plan needs, the placements stay where the solver put them and
    /// `geometryIssues()` reports them as out of bounds — which is the honest
    /// answer, and visibly so in the 3D view, where items hang outside the
    /// wireframe.
    ///
    /// Returns `nil` if the document has no usable dimensions, so the caller can
    /// fall back to the plan's own container.
    func replacingContainer(with scanned: ScannedContainer) -> PackingPlan? {
        guard let size = scanned.size else { return nil }

        return PackingPlan(
            version: version,
            units: units,
            container: Container(
                id: scanned.id,
                label: scanned.label,
                dimensions: size,
                zones: container.zones
            ),
            placements: placements
        )
    }
}
