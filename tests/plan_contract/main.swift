// The phone's half of the plan contract: decode a real server document with the real
// PlanLoader and check every invariant packing-core/CLAUDE.md states.
// Usage: plan_contract <plan_doc.json> <item_dims.json>  (both written by make_plan.py)
import Foundation
import PackingPlan

func fail(_ message: String) -> Never {
    FileHandle.standardError.write(Data("plan contract FAIL: \(message)\n".utf8))
    exit(1)
}

let args = CommandLine.arguments
guard args.count == 3 else { fail("usage: plan_contract <plan_doc.json> <item_dims.json>") }

let plan: PackingPlan
let localDims: [String: Vector3]
do {
    // The live path: the whole `{suitcaseId, createdAt, solver, validation, plan, ...}` document,
    // not a bare plan. Structural validation (units, unique item IDs, contiguous steps) happens here.
    plan = try PlanLoader.plan(fromServerDocument: Data(contentsOf: URL(fileURLWithPath: args[1])))
    localDims = try JSONDecoder().decode([String: Vector3].self,
                                         from: Data(contentsOf: URL(fileURLWithPath: args[2])))
} catch {
    fail("\(error)")
}

guard plan.units == .meters else { fail("units are \(plan.units), not meters") }

let issues = plan.geometryIssues()
guard issues.isEmpty else { fail("geometry: " + issues.map(\.description).joined(separator: "; ")) }

let ordered = plan.orderedPlacements
guard ordered.map(\.step) == Array(1...ordered.count) else {
    fail("steps are not 1...\(ordered.count): \(ordered.map(\.step))")
}

let zoneIDs = Set(plan.container.zones.map(\.id))
for p in ordered {
    guard zoneIDs.contains(p.zone) else { fail("\(p.itemID) names unknown zone '\(p.zone)'") }
    guard let local = localDims[p.itemID] else { fail("no source dimensions for \(p.itemID)") }
    // `size` is the extent in bag axes, i.e. the item's own dimensions permuted by `rotation`.
    let expected = p.rotation.bagExtent(ofLocalSize: local)
    guard Axis.allCases.allSatisfy({ abs(p.size[$0] - expected[$0]) <= 1e-6 }) else {
        fail("\(p.itemID) size \(p.size) != item \(local) permuted by \(p.rotation.rawValue) = \(expected)")
    }
}

let rotations = Set(ordered.map(\.rotation.rawValue)).sorted().joined(separator: ",")
print("plan contract ok: \(ordered.count) placements in \(plan.container.dimensions) m, "
    + "zones [\(zoneIDs.sorted().joined(separator: ","))], rotations [\(rotations)], "
    + "steps 1...\(ordered.count), \(String(format: "%.0f%%", plan.packedVolumeFraction * 100)) of the interior")
