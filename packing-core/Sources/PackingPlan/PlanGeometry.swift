import Foundation

/// A geometric problem with a plan.
///
/// Geometry is validated separately from decoding, and reported rather than
/// repaired: the app surfaces a bad plan instead of nudging items around to hide
/// a solver bug.
public enum GeometryIssue: Hashable, Sendable {
    /// The placement pokes outside the container interior by `overshoot` metres per axis.
    case outOfBounds(itemID: String, overshoot: Vector3)

    /// Two placements share interior volume, overlapping by `overlap` metres per axis.
    case intersection(itemID: String, otherItemID: String, overlap: Vector3)

    /// The placement sits outside the bounds of the zone it claims.
    case outsideZone(itemID: String, zoneID: String, overshoot: Vector3)

    /// The placement names a zone the container does not define.
    case unknownZone(itemID: String, zoneID: String)

    /// A size component is zero or negative.
    case degenerateSize(itemID: String, size: Vector3)

    /// The placement claims to nest inside an item the plan does not contain.
    /// Reported rather than quietly dropped: the nesting is ignored either way,
    /// but a host ID that names nothing is a producer bug worth seeing.
    case unknownNestingHost(itemID: String, hostItemID: String)

    /// The placement declares a cavity whose floor it is not resting on: its base
    /// is `gap` metres above the declared floor (positive: air beneath it) or
    /// below it (negative: sunk into it). See `restingContactTolerance` for the
    /// slack, and the block comment in `geometryIssues()` for what this catches
    /// and — just as importantly — what it does not.
    case nestedOffCavityFloor(itemID: String, hostItemID: String, gap: Float)

    /// The placement declares a cavity that is not inside the host it names,
    /// poking out of the host's box by `overshoot` metres per axis. A cavity is a
    /// void in the host, so one hanging outside it describes nothing — and it
    /// would still license the pair's overlap and hold the guest up.
    case cavityOutsideHost(itemID: String, hostItemID: String, overshoot: Vector3)

    // MARK: - Stability (see `stabilityIssues()`)
    //
    // Reported separately from the cases above: those mean the solver emitted
    // something impossible, these mean it emitted something that falls over.

    /// The placement's base is neither on the floor nor on top of anything: it
    /// hangs `gap` metres above the highest surface under its footprint.
    case floating(itemID: String, gap: Float)

    /// The placement rests on something, but its centre of mass is not over the
    /// area it actually rests on, so it tips off. `supportedFraction` is the
    /// share of its footprint in contact with whatever is below it, `0...1`.
    case unsupportedOverhang(itemID: String, supportedFraction: Float)

    /// `volumeAbove` cubic metres of other items sit in the column above this
    /// one — volume, not mass, because the plan carries no masses.
    case overloadedStack(itemID: String, volumeAbove: Float)

    /// The top of the placement is `overshoot` metres above the interior
    /// ceiling, so the lid does not close over it.
    case exceedsHeadroom(itemID: String, overshoot: Float)
}

extension GeometryIssue: CustomStringConvertible {
    public var description: String {
        switch self {
        case let .outOfBounds(itemID, overshoot):
            return "\(itemID) is outside the container interior by \(overshoot) m"
        case let .intersection(itemID, otherItemID, overlap):
            return "\(itemID) overlaps \(otherItemID) by \(overlap) m"
        case let .outsideZone(itemID, zoneID, overshoot):
            return "\(itemID) is outside zone '\(zoneID)' by \(overshoot) m"
        case let .unknownZone(itemID, zoneID):
            return "\(itemID) names unknown zone '\(zoneID)'"
        case let .degenerateSize(itemID, size):
            return "\(itemID) has a non-positive size \(size)"
        case let .unknownNestingHost(itemID, hostItemID):
            return "\(itemID) claims to nest in '\(hostItemID)', which the plan does not contain"
        case let .nestedOffCavityFloor(itemID, hostItemID, gap):
            return String(
                format: "%@ sits %.3f m %@ the floor of the cavity it declares in '%@'",
                itemID,
                abs(gap),
                gap > 0 ? "above" : "below",
                hostItemID
            )
        case let .cavityOutsideHost(itemID, hostItemID, overshoot):
            return "\(itemID) declares a cavity outside '\(hostItemID)' by \(overshoot) m"
        case let .floating(itemID, gap):
            return String(format: "%@ floats %.3f m above anything that could hold it up", itemID, gap)
        case let .unsupportedOverhang(itemID, supportedFraction):
            return String(
                format: "%@ rests on only %.0f%% of its footprint, with its centre of mass past the supported area",
                itemID,
                supportedFraction * 100
            )
        case let .overloadedStack(itemID, volumeAbove):
            return String(format: "%@ carries %.1f L of items stacked above it", itemID, volumeAbove * 1000)
        case let .exceedsHeadroom(itemID, overshoot):
            return String(format: "%@ stands %.3f m above the interior ceiling", itemID, overshoot)
        }
    }
}

public extension PackingPlan {
    /// The `nestedIn` assertions the geometry checks honour, keyed by the nested
    /// item's ID.
    ///
    /// `nestedIn` is the producer's claim, and a wrong claim must not be able to
    /// hide a collision, so every dubious form is dropped and the placement is
    /// treated as not nested: a host the plan does not contain (also reported as
    /// `.unknownNestingHost`), a self-reference, and any chain of hosts that
    /// loops back on itself.
    func honouredNesting() -> [String: Nesting] {
        let itemIDs = Set(placements.map(\.itemID))
        let host = Dictionary(
            placements.compactMap { p in p.nestedIn.map { (p.itemID, $0.itemID) } },
            uniquingKeysWith: { first, _ in first }
        )

        var honoured: [String: Nesting] = [:]
        for placement in placements {
            guard let nesting = placement.nestedIn, itemIDs.contains(nesting.itemID) else { continue }
            // Walk up the host chain. Ending on an ID already seen means the chain
            // loops; a self-reference is just the shortest such loop.
            var seen: Set<String> = []
            var cursor: String? = placement.itemID
            while let current = cursor, seen.insert(current).inserted {
                cursor = host[current]
            }
            if cursor == nil {
                honoured[placement.itemID] = nesting
            }
        }
        return honoured
    }

    /// Every geometric problem in the plan, in a stable order.
    ///
    /// Empty means the plan is geometrically sound: all placements inside the
    /// interior, inside their declared zones, and no two sharing volume.
    /// Face contact (stacking) is allowed.
    func geometryIssues(tolerance: Float = 1e-6) -> [GeometryIssue] {
        var issues: [GeometryIssue] = []
        let ordered = orderedPlacements
        let interior = container.interior
        let itemIDs = Set(ordered.map(\.itemID))
        let nesting = honouredNesting()
        let boxByID = Dictionary(
            ordered.map { ($0.itemID, $0.box) },
            uniquingKeysWith: { first, _ in first }
        )

        for placement in ordered {
            let box = placement.box

            if Axis.allCases.contains(where: { box.size[$0] <= 0 }) {
                issues.append(.degenerateSize(itemID: placement.itemID, size: box.size))
            }

            let overshoot = box.overshoot(outOf: interior, tolerance: tolerance)
            if overshoot != .zero {
                issues.append(.outOfBounds(itemID: placement.itemID, overshoot: overshoot))
            }

            if let zone = container.zone(id: placement.zone) {
                let zoneOvershoot = box.overshoot(outOf: zone.box, tolerance: tolerance)
                if zoneOvershoot != .zero {
                    issues.append(
                        .outsideZone(
                            itemID: placement.itemID,
                            zoneID: zone.id,
                            overshoot: zoneOvershoot
                        )
                    )
                }
            } else {
                issues.append(.unknownZone(itemID: placement.itemID, zoneID: placement.zone))
            }

            if let nesting = placement.nestedIn, !itemIDs.contains(nesting.itemID) {
                issues.append(
                    .unknownNestingHost(itemID: placement.itemID, hostItemID: nesting.itemID)
                )
            }

            // The declaration checked against the plan's own numbers. WHAT THIS CAN
            // AND CANNOT CATCH, because it is easy to read it as more than it is:
            //
            // The plan carries boxes and the declared cavity cell. It does NOT carry
            // the host's solid decomposition — the max-pooled blocks the solver
            // actually placed against (`oriented_solid_boxes`). So:
            //
            //   CAUGHT: the declaration contradicting the plan itself. A guest whose
            //   base is not resting on the floor it claims, and a cavity that is not
            //   inside the host it names. Both are producer bugs, and both would
            //   otherwise be invisible, because the cavity is exactly what licenses
            //   the host/guest overlap below and what builds the plinth that
            //   suppresses `.floating` in `stabilityIssues()`. Before this, the
            //   cavity only had to exist.
            //
            //   NOT CAUGHT, and not catchable from a plan: a declaration that is
            //   self-consistent and still wrong — guest and declared floor both
            //   lifted 8 mm, so the guest rests exactly on the floor it claims and
            //   there is real air between it and the host's material. Only the host's
            //   solids can tell, so that case belongs to `check_nested_support` in
            //   tools/pipeline_check/check_seams.py, which asks the solver's own
            //   decomposition. Do not "strengthen" the two checks here into claiming
            //   it; they cannot see the host's insides.
            if let nesting = nesting[placement.itemID] {
                let cavity = nesting.cavity.box
                let gap = box.minCorner.y - cavity.minCorner.y
                if abs(gap) > restingContactTolerance {
                    issues.append(
                        .nestedOffCavityFloor(
                            itemID: placement.itemID,
                            hostItemID: nesting.itemID,
                            gap: gap
                        )
                    )
                }
                // Honoured nesting names a host the plan contains, so the lookup holds.
                if let host = boxByID[nesting.itemID] {
                    let outside = cavity.overshoot(outOf: host, tolerance: tolerance)
                    if outside != .zero {
                        issues.append(
                            .cavityOutsideHost(
                                itemID: placement.itemID,
                                hostItemID: nesting.itemID,
                                overshoot: outside
                            )
                        )
                    }
                }
            }
        }

        // Pairwise: n is the number of items a person packs, so O(n²) is fine.
        for i in ordered.indices {
            for j in ordered.index(after: i)..<ordered.endIndex {
                let a = ordered[i]
                let b = ordered[j]
                guard a.box.intersects(b.box, tolerance: tolerance) else { continue }
                // A nested item shares volume with its host legitimately — but only
                // inside the cavity the producer named. The shared volume of two
                // boxes is itself a box, so the test is exactly "is that box inside
                // the cavity": a cup in a bowl's well passes, the same cup shoved
                // through the bowl's wall does not, and neither does anything the
                // pair happens to overlap elsewhere. Skipping the pair outright
                // would turn a real interpenetration into a silent feature.
                if let cavity = cavity(sharedBy: a, b, nesting),
                   intersection(a.box, b.box).isContained(in: cavity, tolerance: tolerance) {
                    continue
                }
                issues.append(
                    .intersection(
                        itemID: a.itemID,
                        otherItemID: b.itemID,
                        overlap: a.box.overlapExtents(with: b.box)
                    )
                )
            }
        }

        return issues
    }

    var isGeometricallyValid: Bool {
        geometryIssues().isEmpty
    }

    /// Throwing form, for call sites that want to refuse a bad plan outright.
    func validateGeometry(tolerance: Float = 1e-6) throws {
        let issues = geometryIssues(tolerance: tolerance)
        guard issues.isEmpty else {
            throw PlanError.invalidGeometry(issues)
        }
    }

    /// Every way the plan can be geometrically legal and still dump the shoes on
    /// the floor, in a stable order.
    ///
    /// Deliberately *not* folded into `geometryIssues()`. Those cases mean the
    /// solver emitted something impossible and a call site may refuse the plan
    /// outright; these are the client's own physics judgement, made because
    /// `PlanLoader` decodes only the `plan` key and never sees the server's
    /// `validation.valid` verdict. A plan that trips one of these is still worth
    /// opening and showing the user — it is a warning, not a refusal.
    ///
    /// **The plan carries no masses.** Every judgement here therefore assumes
    /// **uniform density**: an item's centre of mass is the centre of its box,
    /// and stack load is measured in cubic metres of box above it, not in
    /// kilograms. A dense item under a light bulky one reads as overloaded and a
    /// heavy item on a wide light one does not — that is the price of not having
    /// the server's verdict.
    ///
    /// - Parameters:
    ///   - tolerance: face-contact slack, as in `geometryIssues()`. An item
    ///     within this of a surface below is resting on it, not floating.
    ///   - maxLoadRatio: how much box volume may sit in the column above an item,
    ///     as a multiple of the item's own volume, before it is reported. A
    ///     density-free proxy for "crushed", and the one number here that wants
    ///     calibrating against real bags: at `1.0` every honest multi-layer plan
    ///     we have trips it (a three-layer solver plan puts 1.10× over the jacket
    ///     at the bottom, the bundled mock 1.15× over the jeans and 1.18× over the
    ///     shirts), which is how a warning banner teaches people to ignore it.
    ///     `2.0` still catches the case that matters — something small under a
    ///     mountain.
    func stabilityIssues(tolerance: Float = 1e-6, maxLoadRatio: Float = 2.0) -> [GeometryIssue] {
        var issues: [GeometryIssue] = []
        let ordered = orderedPlacements
        let ceiling = container.dimensions.y

        let nesting = honouredNesting()

        for placement in ordered {
            let box = placement.box
            let base = box.minCorner.y
            let others = ordered.filter { $0.itemID != placement.itemID }

            // Anything that could hold this item up: a box under our footprint
            // whose top is at or below our base. The floor counts, at y = 0.
            //
            // A nested item is held up by the floor of its host's cavity, which is
            // a void inside the host's own box — the host box is therefore *not*
            // below it, and without this the socks in a shoe read as floating.
            //
            // The plinth comes from the declaration, so it is only as good as the
            // declaration: `geometryIssues()` now reports the guest whose base is
            // not on the floor it claims (`.nestedOffCavityFloor`) and the cavity
            // that is not inside its host (`.cavityOutsideHost`), which is what
            // keeps this from being a blank exemption. What neither can check is
            // whether the declared floor is where the host's material actually is —
            // the plan does not carry the host's solids. See that block comment.
            var supporters = others.map(\.box)
            if let cavity = nesting[placement.itemID]?.cavity.box {
                supporters.append(plinth(under: cavity))
            }
            let below = supporters.filter {
                $0.maxCorner.y <= base + tolerance && footprintOverlap(box, $0) > 0
            }
            let surface = max(below.map(\.maxCorner.y).max() ?? 0, 0)
            let gap = base - surface

            if gap > tolerance {
                issues.append(.floating(itemID: placement.itemID, gap: gap))
            } else if base > tolerance {
                // Resting on other items rather than the floor. The contact
                // rectangles are the footprints topping out at our base; they are
                // disjoint, because two boxes ending at the same height with
                // overlapping footprints would share volume and `geometryIssues()`
                // already reports that. So the supported areas simply add up. The
                // one exception is a cavity plinth, which does overlap its host's
                // footprint — a nested item's reported fraction can over-count.
                let contacts = below.filter { abs($0.maxCorner.y - base) <= tolerance }
                let footprint = box.size.x * box.size.z
                let supported = contacts.reduce(0) { $0 + footprintOverlap(box, $1) }
                let com = box.center
                // The centre of mass is inside our own footprint by construction,
                // so "inside a supporter's footprint" and "inside the contact
                // rectangle" are the same test.
                let held = contacts.contains { s in
                    com.x >= s.minCorner.x - tolerance && com.x <= s.maxCorner.x + tolerance
                        && com.z >= s.minCorner.z - tolerance && com.z <= s.maxCorner.z + tolerance
                }
                if !held {
                    issues.append(
                        .unsupportedOverhang(
                            itemID: placement.itemID,
                            supportedFraction: footprint > 0 ? supported / footprint : 0
                        )
                    )
                }
            }

            // Everything stacked in the column over this item, direct or not.
            let load = others
                .filter {
                    $0.box.minCorner.y >= box.maxCorner.y - tolerance
                        && footprintOverlap(box, $0.box) > 0
                }
                .reduce(0) { $0 + $1.box.volume }
            if box.volume > 0, load > box.volume * maxLoadRatio {
                issues.append(.overloadedStack(itemID: placement.itemID, volumeAbove: load))
            }

            // Y-axis only, so it overlaps `outOfBounds` — but this function is
            // callable on its own, and "the lid will not close" is the reading a
            // user acts on.
            let overshoot = box.maxCorner.y - ceiling
            if overshoot > tolerance {
                issues.append(.exceedsHeadroom(itemID: placement.itemID, overshoot: overshoot))
            }
        }

        return issues
    }
}

/// Resting-contact slack for a nested guest's base against its declared cavity
/// floor: 1 mm, deliberately NOT the 1e-6 float slack the rest of the geometry
/// checks use, because it answers a different question. 1e-6 asks "are these two
/// floats the same number"; this asks "is this thing resting on that thing",
/// across two independently authored faces that survived a frame swap and a
/// Float32 round-trip. It is the physics layer's own `RESTING_CONTACT_EPS_M`
/// (`physics/constraints.py`), which `tools/pipeline_check/check_seams.py` asks
/// the same question with, so the Python and Swift halves of the seam agree on
/// what resting means. A scan's cell size is 1 cm, so nothing finer than a
/// millimetre is a discrepancy a producer could act on anyway.
private let restingContactTolerance: Float = 1e-3

/// The box two boxes share. Only meaningful when they do intersect; a separated
/// pair gives a box with a negative extent.
private func intersection(_ a: BoundingBox, _ b: BoundingBox) -> BoundingBox {
    let low = Vector3(
        max(a.minCorner.x, b.minCorner.x),
        max(a.minCorner.y, b.minCorner.y),
        max(a.minCorner.z, b.minCorner.z)
    )
    let high = Vector3(
        min(a.maxCorner.x, b.maxCorner.x),
        min(a.maxCorner.y, b.maxCorner.y),
        min(a.maxCorner.z, b.maxCorner.z)
    )
    return BoundingBox(minCorner: low, size: high - low)
}

/// The cavity one of the two placements is nested in the other by, if either is —
/// the only region in which the pair may share volume.
private func cavity(
    sharedBy a: Placement,
    _ b: Placement,
    _ nesting: [String: Nesting]
) -> BoundingBox? {
    if let n = nesting[a.itemID], n.itemID == b.itemID { return n.cavity.box }
    if let n = nesting[b.itemID], n.itemID == a.itemID { return n.cavity.box }
    return nil
}

/// The cavity floor as a support surface: a column from the bag floor up to the
/// underside of the cavity, with the cavity's own footprint.
private func plinth(under cavity: BoundingBox) -> BoundingBox {
    BoundingBox(
        minCorner: Vector3(cavity.minCorner.x, 0, cavity.minCorner.z),
        size: Vector3(cavity.size.x, cavity.minCorner.y, cavity.size.z)
    )
}

/// Contact area of two boxes seen from above, in square metres. Zero when their
/// footprints only touch or miss entirely.
private func footprintOverlap(_ a: BoundingBox, _ b: BoundingBox) -> Float {
    let extents = a.overlapExtents(with: b)
    return max(extents.x, 0) * max(extents.z, 0)
}
