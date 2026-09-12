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
    /// Every geometric problem in the plan, in a stable order.
    ///
    /// Empty means the plan is geometrically sound: all placements inside the
    /// interior, inside their declared zones, and no two sharing volume.
    /// Face contact (stacking) is allowed.
    func geometryIssues(tolerance: Float = 1e-6) -> [GeometryIssue] {
        var issues: [GeometryIssue] = []
        let ordered = orderedPlacements
        let interior = container.interior

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
        }

        // Pairwise: n is the number of items a person packs, so O(n²) is fine.
        for i in ordered.indices {
            for j in ordered.index(after: i)..<ordered.endIndex {
                let a = ordered[i]
                let b = ordered[j]
                guard a.box.intersects(b.box, tolerance: tolerance) else { continue }
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
    /// `validation.valid` verdict. A plan can be worth showing with a warning
    /// and still trip several of these — the hand-authored mock plan does.
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
    ///     at the bottom, the bundled mock 1.15× over the jeans), which is how a
    ///     warning banner teaches people to ignore it. `2.0` still catches the
    ///     case that matters — something small under a mountain.
    func stabilityIssues(tolerance: Float = 1e-6, maxLoadRatio: Float = 2.0) -> [GeometryIssue] {
        var issues: [GeometryIssue] = []
        let ordered = orderedPlacements
        let ceiling = container.dimensions.y

        for placement in ordered {
            let box = placement.box
            let base = box.minCorner.y
            let others = ordered.filter { $0.itemID != placement.itemID }

            // Anything that could hold this item up: a box under our footprint
            // whose top is at or below our base. The floor counts, at y = 0.
            let below = others.filter {
                $0.box.maxCorner.y <= base + tolerance && footprintOverlap(box, $0.box) > 0
            }
            let surface = max(below.map(\.box.maxCorner.y).max() ?? 0, 0)
            let gap = base - surface

            if gap > tolerance {
                issues.append(.floating(itemID: placement.itemID, gap: gap))
            } else if base > tolerance {
                // Resting on other items rather than the floor. The contact
                // rectangles are the footprints topping out at our base; they are
                // disjoint, because two boxes ending at the same height with
                // overlapping footprints would share volume and `geometryIssues()`
                // already reports that. So the supported areas simply add up.
                let contacts = below.filter { abs($0.box.maxCorner.y - base) <= tolerance }
                let footprint = box.size.x * box.size.z
                let supported = contacts.reduce(0) { $0 + footprintOverlap(box, $1.box) }
                let com = box.center
                // The centre of mass is inside our own footprint by construction,
                // so "inside a supporter's footprint" and "inside the contact
                // rectangle" are the same test.
                let held = contacts.contains { supporter in
                    let s = supporter.box
                    return com.x >= s.minCorner.x - tolerance && com.x <= s.maxCorner.x + tolerance
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

/// Contact area of two boxes seen from above, in square metres. Zero when their
/// footprints only touch or miss entirely.
private func footprintOverlap(_ a: BoundingBox, _ b: BoundingBox) -> Float {
    let extents = a.overlapExtents(with: b)
    return max(extents.x, 0) * max(extents.z, 0)
}
