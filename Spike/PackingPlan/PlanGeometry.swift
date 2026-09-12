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
}
