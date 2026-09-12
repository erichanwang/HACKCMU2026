import Foundation

public enum PlanError: Error, Hashable {
    case fileNotFound(name: String)
    case malformedJSON(description: String)
    case duplicateItemIDs([String])
    case nonContiguousSteps([Int])
    case noPlacements
    case invalidGeometry([GeometryIssue])
}

extension PlanError: LocalizedError {
    public var errorDescription: String? {
        switch self {
        case let .fileNotFound(name):
            return "Could not find plan file '\(name)'."
        case let .malformedJSON(description):
            return "Plan JSON could not be decoded: \(description)"
        case let .duplicateItemIDs(ids):
            return "Plan repeats item IDs: \(ids.joined(separator: ", "))."
        case let .nonContiguousSteps(steps):
            return "Plan steps must be 1...n with no gaps or repeats; got \(steps)."
        case .noPlacements:
            return "Plan contains no placements."
        case let .invalidGeometry(issues):
            return "Plan geometry is invalid:\n" + issues.map { "• \($0)" }.joined(separator: "\n")
        }
    }
}

/// Reads packing plans produced by the solver.
///
/// Loading performs *structural* validation only — decoding, unit declaration,
/// unique item IDs, a contiguous step sequence. Geometry is checked separately
/// via `PackingPlan.geometryIssues()` so the app can load a questionable plan and
/// show the user what is wrong with it rather than failing to open at all. Call
/// `validateGeometry()` when you want geometry to be a hard requirement.
public enum PlanLoader {
    /// Filename of the hand-authored mock plan bundled with this module.
    public static let mockPlanResourceName = "plan"

    public static func plan(from data: Data) throws -> PackingPlan {
        let decoder = JSONDecoder()
        let plan: PackingPlan
        do {
            plan = try decoder.decode(PackingPlan.self, from: data)
        } catch let error as DecodingError {
            throw PlanError.malformedJSON(description: Self.describe(error))
        }
        try validateStructure(of: plan)
        return plan
    }

    public static func plan(at url: URL) throws -> PackingPlan {
        let data: Data
        do {
            data = try Data(contentsOf: url)
        } catch {
            throw PlanError.fileNotFound(name: url.lastPathComponent)
        }
        return try plan(from: data)
    }

    /// Loads a plan resource from a bundle — the app target's own bundle in
    /// production, `Bundle.module` for the bundled mock.
    public static func plan(
        resourceNamed name: String,
        extension fileExtension: String = "json",
        in bundle: Bundle
    ) throws -> PackingPlan {
        guard let url = bundle.url(forResource: name, withExtension: fileExtension) else {
            throw PlanError.fileNotFound(name: "\(name).\(fileExtension)")
        }
        return try plan(at: url)
    }

    /// The hand-authored mock plan: the demo carry-on interior, 0.4064 × 0.1524 ×
    /// 0.6096 m (16 × 6 × 24 in), with six items.
    /// Used by tests and by the 2D view's previews, so the UI can be built
    /// without a live solver.
    public static func mockPlan() throws -> PackingPlan {
        try plan(resourceNamed: mockPlanResourceName, in: .module)
    }

    // MARK: - Structural validation

    private static func validateStructure(of plan: PackingPlan) throws {
        guard !plan.placements.isEmpty else { throw PlanError.noPlacements }

        var seen = Set<String>()
        var duplicates: [String] = []
        for placement in plan.placements where !seen.insert(placement.itemID).inserted {
            duplicates.append(placement.itemID)
        }
        guard duplicates.isEmpty else {
            throw PlanError.duplicateItemIDs(duplicates)
        }

        let steps = plan.placements.map(\.step).sorted()
        guard steps == Array(1...plan.placements.count) else {
            throw PlanError.nonContiguousSteps(steps)
        }
    }

    private static func describe(_ error: DecodingError) -> String {
        func path(_ context: DecodingError.Context) -> String {
            let keys = context.codingPath.map(\.stringValue).filter { !$0.isEmpty }
            return keys.isEmpty ? "<root>" : keys.joined(separator: ".")
        }
        switch error {
        case let .keyNotFound(key, context):
            return "missing key '\(key.stringValue)' at \(path(context))"
        case let .typeMismatch(type, context):
            return "expected \(type) at \(path(context))"
        case let .valueNotFound(type, context):
            return "missing value of type \(type) at \(path(context))"
        case let .dataCorrupted(context):
            return "corrupted value at \(path(context)): \(context.debugDescription)"
        @unknown default:
            return String(describing: error)
        }
    }
}
