// Spike/InventoryRings.swift's pure ring layout, on Linux (see run.sh).
import Foundation

var checks = 0
func check(_ ok: Bool, _ what: String) {
    checks += 1
    if !ok { print("FAIL: \(what)"); exit(1) }
}

check(ringSlots(count: 0).isEmpty, "no items, no slots")
check(ringSlots(count: 1) == [RingSlot(ring: 1, angle: 0)], "one item sits alone on ring 1")

func evenly(_ n: Int) -> [Double] { (0..<n).map { i in 2.0 * Double.pi * Double(i) / Double(n) } }

let six = ringSlots(count: 6)
check(six.allSatisfy { $0.ring == 1 }, "six items fill ring 1")
let sixAngles: [Double] = six.map { $0.angle }
check(sixAngles == evenly(6), "ring 1 is evenly spaced")

let seven = ringSlots(count: 7)
check(seven[6] == RingSlot(ring: 2, angle: 0), "the seventh item opens ring 2")

let ten = ringSlots(count: 10)
let outerAngles: [Double] = ten.filter { $0.ring == 2 }.map { $0.angle }
check(outerAngles == evenly(4), "a half-full ring spreads its items evenly, not bunched at the start")

let big = ringSlots(count: 36)
check(big.filter { $0.ring == 3 }.count == 18 && big.last?.ring == 3, "6 + 12 + 18 = 36 fills three rings")
check(ringSlots(count: 37).last?.ring == 4, "the 37th item opens ring 4")

check(ringSpin(ring: 1, at: 0) == 0, "no spin at t = 0")
let turn: Double = 2.0 * Double.pi
check(abs(ringSpin(ring: 1, at: 45) - turn) < 1e-9, "ring 1 turns once in 45 s")
check(abs(ringSpin(ring: 2, at: 60) + turn) < 1e-9, "ring 2 turns once in 60 s, the other way")

print("rings: \(checks) checks passed")
