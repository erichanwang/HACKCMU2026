import Foundation
import PackingPlan

func makePlacement(step: Int, itemID: String, label: String) -> Placement {
    Placement(step: step, itemID: itemID, label: label, zone: "z",
               position: Vector3(0, 0, 0), size: Vector3(0.1, 0.1, 0.1),
               rotation: .xyz, note: "")
}

func makePlan(_ n: Int) -> PackingPlan {
    let container = Container(id: "c", label: "c", dimensions: Vector3(1, 1, 1), zones: [])
    let placements = (1...max(n, 1)).prefix(n).map { makePlacement(step: $0, itemID: "item-\($0)", label: "Item \($0)") }
    return PackingPlan(version: 1, units: .meters, container: container, placements: Array(placements))
}

// --- packing order: real multi-layer plan.json --------------------------------------------------
let planPath = "packing-core/Sources/PackingPlan/Resources/plan.json"
let realPlan = try! PlanLoader.plan(from: Data(contentsOf: URL(fileURLWithPath: planPath)))
assert(realPlan.placements.count == 6, "plan.json placements \(realPlan.placements.count)")

var state = PlanOverlayState(plan: realPlan)
assert(state.step == 0, "initial step")
assert(state.current?.itemID == realPlan.orderedPlacements[0].itemID, "step 0 current")
assert(state.stepDescription == "step 1 of 6", "stepDescription \(state.stepDescription)")
var roles = state.placementsWithRole
assert(roles[0].role == .current, "roles[0]")
assert(roles[1...].allSatisfy { $0.role == .upcoming }, "roles[1...] all upcoming")

state.advance()
assert(state.step == 1, "advance once")
roles = state.placementsWithRole
assert(roles[0].role == .placed, "roles[0] placed after advance")
assert(roles[1].role == .current, "roles[1] current after advance")
assert(roles[2...].allSatisfy { $0.role == .upcoming }, "roles[2...] still upcoming")

for _ in 0..<10 { state.advance() }  // clamps at count, does not overshoot
assert(state.step == 6, "advance clamps at count")
assert(state.current == nil, "no current once complete")
assert(state.isComplete, "isComplete")
assert(state.stepDescription == "packed 6 of 6", "stepDescription at completion \(state.stepDescription)")
assert(state.placementsWithRole.allSatisfy { $0.role == .placed }, "all placed at completion")

state.retreat()
assert(state.step == 5, "retreat once")
for _ in 0..<10 { state.retreat() }  // clamps at 0
assert(state.step == 0, "retreat clamps at zero")

// --- colours: stable across rebuilds, distinct for adjacent items --------------------------------
let stateA = PlanOverlayState(plan: realPlan)
let stateB = PlanOverlayState(plan: realPlan)  // a fresh rebuild of the same plan
for p in realPlan.orderedPlacements {
    assert(stateA.colorIndex(for: p) == stateB.colorIndex(for: p), "colour stable across rebuilds for \(p.itemID)")
}
let ordered = realPlan.orderedPlacements
for i in 0..<(ordered.count - 1) {
    let a = stateA.colorIndex(for: ordered[i]), b = stateA.colorIndex(for: ordered[i + 1])
    assert(a != b, "adjacent colours differ at \(i): \(a) vs \(b)")
}

// --- step model bounds: empty plan, single item, step 0, last step -------------------------------
let empty = PlanOverlayState(plan: makePlan(0))
assert(empty.count == 0, "empty count")
assert(empty.current == nil, "empty current")
assert(empty.stepDescription == "nothing to pack", "empty description \(empty.stepDescription)")
assert(empty.placementsWithRole.isEmpty, "empty roles")
var emptyMut = empty
emptyMut.advance(); emptyMut.retreat()  // must not crash or go negative
assert(emptyMut.step == 0, "empty step stays 0")

let single = PlanOverlayState(plan: makePlan(1))
assert(single.count == 1, "single count")
assert(single.current?.itemID == "item-1", "single current")
assert(single.stepDescription == "step 1 of 1", "single description \(single.stepDescription)")
var singleMut = single
singleMut.advance()
assert(singleMut.isComplete, "single completes after one advance")
assert(singleMut.current == nil, "single has no current once complete")

// step "0" (nothing placed yet) is the state's own init default — already exercised above via `state`.
// "last step" = count - 1 (the final item is `current`, everything before it `placed`):
var last = PlanOverlayState(plan: realPlan, step: realPlan.placements.count - 1)
assert(last.current?.itemID == realPlan.orderedPlacements.last?.itemID, "last step current is the final item")
assert(!last.isComplete, "last step is not yet complete — the final item is still to place")
assert(last.placementsWithRole.dropLast().allSatisfy { $0.role == .placed }, "everything before the last step is placed")

// step given out of range clamps into 0...count on init, both directions:
let clampedHigh = PlanOverlayState(plan: realPlan, step: 999)
assert(clampedHigh.step == realPlan.placements.count, "init clamps high")
let clampedLow = PlanOverlayState(plan: realPlan, step: -5)
assert(clampedLow.step == 0, "init clamps low")

// --- unpacked items: no position, surfaced as a count + list -------------------------------------
let noneUnpacked = UnpackedSummary(items: [])
assert(noneUnpacked.isEmpty, "no unpacked items")
assert(noneUnpacked.summaryText.isEmpty, "empty summary text")

let someUnpacked = UnpackedSummary(items: [
    UnpackedPlacement(itemID: "umbrella", label: "Umbrella"),
    UnpackedPlacement(itemID: "boots", label: "Winter boots"),
])
assert(someUnpacked.count == 2, "unpacked count")
assert(someUnpacked.summaryText == "2 items didn't fit: Umbrella, Winter boots", "unpacked summary \(someUnpacked.summaryText)")

let oneUnpacked = UnpackedSummary(items: [UnpackedPlacement(itemID: "hat", label: "Hat")])
assert(oneUnpacked.summaryText == "1 item didn't fit: Hat", "singular unpacked summary \(oneUnpacked.summaryText)")

print("plan overlay: step sequencing, colour stability, bounds, unpacked summary — ok")
