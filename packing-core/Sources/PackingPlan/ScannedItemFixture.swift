import Foundation

/// A real scanned item, captured by the LiDAR spike and copied out of Mongo, so
/// the heightmap renderer can be exercised without a live server.
///
/// The document is returned as raw JSON rather than a decoded model: the
/// `ScannedItem` type lives in the app target alongside the scanner that
/// produces it, and duplicating it here would give the team two definitions of
/// the same wire format to keep in step.
public enum ScannedItemFixture {
    public static let resourceName = "scanned-item"

    public static func data() throws -> Data {
        guard let url = Bundle.module.url(forResource: resourceName, withExtension: "json") else {
            throw PlanError.fileNotFound(name: "\(resourceName).json")
        }
        return try Data(contentsOf: url)
    }
}
