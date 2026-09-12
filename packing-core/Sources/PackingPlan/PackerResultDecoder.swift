import Foundation

/// The axis convention gap between packer3d and this app, in one place.
///
/// - packer3d: `x = length, y = width, z = up`, right-handed, origin at the
///   container's min corner.
/// - ours: `x = width, y = up, z = depth`, right-handed, origin at the interior
///   min corner.
///
/// So our X takes packer3d's Y, our Y takes its Z, and our Z takes its X. That is
/// a cyclic — therefore even — permutation, so handedness is preserved: no
/// mirroring and no sign flips anywhere. Both frames put the origin at the same
/// physical corner and measure positively into the box, so a min corner stays a
/// min corner.
///
/// If the team standardises on a different convention, change
/// `packerAxisForOurs` and nothing else in this file needs to move.
public enum PackerAxes {
    /// Index into a packer3d `[x, y, z]` triple that supplies each of our axes.
    public static let packerAxisForOurs = (x: 1, y: 2, z: 0)

    /// Re-express a packer3d vector (position, size, or any other triple) in our frame.
    public static func toBagFrame(_ packer: SIMD3<Double>) -> Vector3 {
        Vector3(
            Float(packer[packerAxisForOurs.x]),
            Float(packer[packerAxisForOurs.y]),
            Float(packer[packerAxisForOurs.z])
        )
    }

    /// Re-express a packer3d box orientation in our frame.
    ///
    /// packer3d writes `"yxz"` as *world axis ← item axis*, the same reading our
    /// `AxisRotation` uses, so only the world side needs permuting: our axis *j*
    /// takes whatever item axis packer3d assigned to the packer axis that feeds
    /// *j*.
    ///
    /// Two caveats, both deliberate:
    /// - the item-local letters stay in packer3d's naming (its item x/y/z), because
    ///   the scanner→solver item convention is owned upstream and is not ours to
    ///   reinterpret;
    /// - cylinders carry `"cyl_axis_x"` instead of a permutation. We render them as
    ///   their bounding box, which `dims` already describes, so identity is the
    ///   honest answer rather than a fabricated rotation.
    public static func toBagRotation(_ packerOrientation: String) -> AxisRotation {
        let letters = Array(packerOrientation.uppercased())
        guard letters.count == 3 else { return .xyz }
        let permuted = [packerAxisForOurs.x, packerAxisForOurs.y, packerAxisForOurs.z]
            .map { letters[$0] }
        return AxisRotation(rawValue: String(permuted)) ?? .xyz
    }
}

/// An item the solver could not fit, and why.
///
/// Our `PackingPlan` has nowhere to put these, but dropping them silently would
/// let five of eighteen items vanish from a demo with no explanation.
public struct UnpackedItem: Hashable, Sendable {
    public let id: String
    public let reason: String
}

/// A packer3d result translated into our model.
public struct DecodedPackerResult: Sendable {
    public let plan: PackingPlan
    public let unpacked: [UnpackedItem]
}

public enum PackerDecodeError: Error, LocalizedError {
    case unrecognisedShape(String)
    case badVector(field: String, count: Int)

    public var errorDescription: String? {
        switch self {
        case let .unrecognisedShape(description):
            return "Not a packer3d result: \(description)"
        case let .badVector(field, count):
            return "packer3d \(field) should hold 3 numbers, got \(count)"
        }
    }
}

/// Reads packer3d's result JSON into a `PackingPlan`.
///
/// packer3d emits vectors as bare arrays and uses its own axis convention; both
/// are normalised here so nothing downstream has to know packer3d exists.
public enum PackerResultDecoder {
    /// The zone every decoded placement belongs to.
    ///
    /// packer3d has no concept of zones, so rather than invent layers that the
    /// solver did not intend, one zone spans the whole interior. The 3D and 2D
    /// views group by height at render time instead.
    public static let wholeContainerZoneID = "container"

    /// Decodes a packer3d result.
    ///
    /// Accepts a bare result and a `--compare` file (`{"naive": …, "optimized": …}`),
    /// preferring `optimized` in the latter.
    public static func decode(_ data: Data) throws -> DecodedPackerResult {
        let decoder = JSONDecoder()

        let result: RawResult
        if let bare = try? decoder.decode(RawResult.self, from: data) {
            result = bare
        } else if let compare = try? decoder.decode(RawCompare.self, from: data),
                  let best = compare.optimized ?? compare.naive {
            result = best
        } else {
            throw PackerDecodeError.unrecognisedShape(
                "expected `container` and `placements`, or a `--compare` file with `optimized`"
            )
        }

        return try translate(result)
    }

    // MARK: - Translation

    private static func translate(_ raw: RawResult) throws -> DecodedPackerResult {
        let dimensions = try vector(raw.container.dims, field: "container.dims")

        let container = Container(
            id: raw.container.id,
            label: raw.container.id,
            dimensions: dimensions,
            zones: [
                Zone(
                    id: wholeContainerZoneID,
                    label: "Container",
                    origin: .zero,
                    size: dimensions
                )
            ]
        )

        // packer3d places items one at a time in array order, which is exactly the
        // order a person should pack them, so the index is the step.
        let placements = try raw.placements.enumerated().map { index, item in
            Placement(
                step: index + 1,
                itemID: item.itemID,
                label: item.itemID,
                zone: wholeContainerZoneID,
                position: try vector(item.position, field: "placements[\(index)].position"),
                size: try vector(item.dims, field: "placements[\(index)].dims"),
                rotation: PackerAxes.toBagRotation(item.orientation ?? "xyz"),
                note: ""
            )
        }

        let plan = PackingPlan(
            version: 1,
            units: .meters,
            container: container,
            placements: placements
        )

        let unpacked = (raw.unpacked ?? []).map {
            UnpackedItem(id: $0.id, reason: $0.reason ?? "no reason given")
        }

        return DecodedPackerResult(plan: plan, unpacked: unpacked)
    }

    private static func vector(_ values: [Double], field: String) throws -> Vector3 {
        guard values.count == 3 else {
            throw PackerDecodeError.badVector(field: field, count: values.count)
        }
        return PackerAxes.toBagFrame(SIMD3(values[0], values[1], values[2]))
    }

    // MARK: - packer3d's wire format

    private struct RawCompare: Decodable {
        let naive: RawResult?
        let optimized: RawResult?
    }

    private struct RawResult: Decodable {
        let container: RawContainer
        let placements: [RawPlacement]
        let unpacked: [RawUnpacked]?
    }

    private struct RawContainer: Decodable {
        let id: String
        let dims: [Double]
    }

    /// Only the fields we can represent. packer3d also sends `mass`, `fragile`,
    /// `center`, and cylinder `radius`/`height`/`axis`; our model has no home for
    /// them and the renderer draws bounding boxes, so they are dropped.
    private struct RawPlacement: Decodable {
        let itemID: String
        let position: [Double]
        let dims: [Double]
        let orientation: String?

        private enum CodingKeys: String, CodingKey {
            case itemID = "item_id"
            case position
            case dims
            case orientation
        }
    }

    private struct RawUnpacked: Decodable {
        let id: String
        let reason: String?
    }
}
