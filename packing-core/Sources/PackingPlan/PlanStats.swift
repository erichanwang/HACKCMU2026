import Foundation

/// The numbers a person actually wants when they look at a packing plan: how
/// full the bag is, whether the leftover space is one usable block or scraps,
/// what each layer holds, and what the packing order means in practice.
///
/// Everything here is derived from plan **geometry** alone. In particular:
///
/// > **Gap: the plan document carries no masses.** The solver knows each item's
/// > mass (and a `fragile` flag) — see the `solver.placements[].mass` field in a
/// > server document — but `PackingPlan` does not carry either, so nothing here
/// > can rank items by weight or call an item fragile. `loadBearing` is
/// > therefore a *geometric* answer ("something is stacked on this"), not a
/// > "this will get crushed" answer. Add mass to `Placement` and this type can
/// > say which layer is the heavy one.
///
/// All lengths are metres and all volumes cubic metres, per CLAUDE.md.
/// Formatting lives in the `Display` section at the bottom of this file and
/// never in the computation above it.
public struct PlanStats: Hashable, Sendable {

    /// Floors within this distance of each other count as one layer.
    ///
    /// Same 5 mm rule as the 2D view's layer grouping, restated here because the
    /// `PackingPlan` module cannot see `PackingPlanUI` (which imports SwiftUI).
    /// `layerStats(tolerance:)` below is the shareable grouping: `PlanLayer` can
    /// be built from it once someone wants one source of truth.
    public static let layerTolerance: Float = 0.005

    /// Two faces this close count as touching — used to decide "resting on".
    /// 0.1 mm: below any real gap a solver would leave, above float noise.
    public static let contactTolerance: Float = 1e-4

    /// One horizontal layer, and what it holds.
    public struct Layer: Hashable, Sendable, Identifiable {
        /// 0 is the layer resting on the bag floor.
        public let index: Int
        /// Height of this layer's floor above the bag floor, metres, `>= 0`.
        public let floorY: Float
        /// Distance from this layer's floor to the top of its tallest item,
        /// metres, `>= 0`.
        public let thickness: Float
        /// Fraction of the bag's floor cross-section this layer's items cover,
        /// `0...1`. Union area, so nothing is double-counted.
        public let floorCoverage: Float
        /// This layer's placements, in packing order.
        public let placements: [Placement]

        public var id: Int { index }
        /// Number of items in the layer, `>= 1`.
        public var itemCount: Int { placements.count }
    }

    /// Interior cavity volume, m³, `> 0` for any sane container.
    public let interiorVolume: Float
    /// Volume the placement boxes occupy, counted once, m³, `>= 0`. Boxes, not
    /// items: an item's real shape is smaller than the box the solver reserved
    /// for it.
    ///
    /// Nested items share volume with their host legitimately (see the "Nested
    /// placements" section of CLAUDE.md), so the sum of the boxes counts the
    /// shared part twice. The volume each honoured nesting shares with its host
    /// is subtracted, which makes this exactly the union of the boxes — the same
    /// solid the free-space figures below measure against. Only nesting the plan
    /// actually honours counts; a dangling, self-referential or cyclic
    /// `nestedIn` is not a licence to discount volume.
    public let packedVolume: Float
    /// `packedVolume / interiorVolume`, `0...1`.
    ///
    /// Equals the solver's `fill_ratio` to float precision for a plan with no
    /// nesting. For a nested plan it is *lower*: `fill_ratio` (like
    /// `PackingPlan.packedVolumeFraction`) sums the boxes and so counts the
    /// shared volume twice.
    public let fillFraction: Float

    /// The largest single empty axis-aligned box left in the interior, in bag
    /// coordinates. `nil` only when the container is degenerate or completely
    /// full. Exact: an empty box can always be grown until every face touches an
    /// item face or a wall, so the search over item-face planes below misses
    /// nothing.
    ///
    /// Nesting needs no special case here, and deliberately gets none: the search
    /// avoids the *union* of the boxes, so a nested guest's box is avoided whether
    /// or not its host already covered that space (the guest may stick out of the
    /// host — a cup's rim above a bowl's — and then it is the guest's box that
    /// blocks). A host's cavity stays solid, because the space inside a shoe is
    /// not somewhere a plan can put another item, and treating it as free would
    /// let a reported block straddle the cavity and the open air outside it.
    public let largestGap: BoundingBox?
    /// `largestGap` volume, m³, `>= 0`; 0 when there is none.
    public let largestGapVolume: Float
    /// `largestGapVolume` over all free volume, `0...1`. Near 1 means the
    /// leftover space is one usable block; near 0 means scraps.
    public let largestGapFractionOfFree: Float

    /// Fraction of the bag floor's `width × depth` covered by *some* item,
    /// looking straight down, `0...1`. Union area — items stacked over the same
    /// patch count once, and so do a nested item and its host, which is why
    /// nesting needs no adjustment here.
    public let floorCoverage: Float

    /// Layers bottom-first. Empty only for a plan with no placements.
    public let layers: [Layer]

    /// What goes in first (step 1).
    public let firstIn: Placement?
    /// What goes in last (highest step) — the item the lid closes on.
    public let lastIn: Placement?
    /// Whatever reaches highest in the bag. Often, but not always, `lastIn`.
    public let topmost: Placement?
    /// Items with something resting directly on them, in packing order — the
    /// "don't put anything breakable here" list, as far as geometry can tell.
    /// See the mass gap in this type's doc comment.
    public let loadBearing: [Placement]

    public init(plan: PackingPlan) {
        let interior = plan.container.interior
        let dimensions = interior.size
        let boxes = plan.placements.map(\.box)

        interiorVolume = interior.volume
        packedVolume = max(boxes.reduce(0) { $0 + $1.volume } - PlanStats.nestedOverlapVolume(plan), 0)
        fillFraction = interiorVolume > 0 ? min(packedVolume / interiorVolume, 1) : 0

        let gap = PlanStats.largestEmptyBox(dimensions: dimensions, boxes: boxes)
        largestGap = gap
        largestGapVolume = gap?.volume ?? 0
        let freeVolume = max(interiorVolume - packedVolume, 0)
        largestGapFractionOfFree = freeVolume > 0 ? min(largestGapVolume / freeVolume, 1) : 0

        let floorArea = dimensions.x * dimensions.z
        floorCoverage = floorArea > 0
            ? min(PlanStats.unionFootprintArea(boxes) / floorArea, 1)
            : 0

        layers = plan.layerStats()

        let ordered = plan.orderedPlacements
        firstIn = ordered.first
        lastIn = ordered.last
        topmost = ordered.max { $0.box.maxCorner.y < $1.box.maxCorner.y }
        loadBearing = ordered.filter { lower in
            ordered.contains { upper in
                upper.itemID != lower.itemID
                    && abs(upper.position.y - lower.box.maxCorner.y) <= PlanStats.contactTolerance
                    && PlanStats.footprintsOverlap(upper.box, lower.box)
            }
        }
    }
}

// MARK: - Layer grouping

public extension PackingPlan {
    /// Placements grouped into horizontal layers by floor height, bottom first,
    /// with each layer's thickness and floor coverage.
    ///
    /// Grouping is by `position.y` (the min corner), and each item is compared
    /// against its *group's* floor, so a run of slightly increasing heights
    /// cannot chain into one oversized layer. Every placement lands in exactly
    /// one layer.
    func layerStats(tolerance: Float = PlanStats.layerTolerance) -> [PlanStats.Layer] {
        let sorted = placements.sorted {
            $0.position.y == $1.position.y ? $0.step < $1.step : $0.position.y < $1.position.y
        }

        var groups: [(floor: Float, items: [Placement])] = []
        for placement in sorted {
            if let last = groups.last, placement.position.y - last.floor <= tolerance {
                groups[groups.count - 1].items.append(placement)
            } else {
                groups.append((floor: placement.position.y, items: [placement]))
            }
        }

        let dimensions = container.dimensions
        let floorArea = dimensions.x * dimensions.z

        return groups.enumerated().map { index, group in
            let items = group.items.sorted { $0.step < $1.step }
            let ceiling = items.map { $0.box.maxCorner.y }.max() ?? group.floor
            let coverage = floorArea > 0
                ? min(PlanStats.unionFootprintArea(items.map(\.box)) / floorArea, 1)
                : 0
            return PlanStats.Layer(
                index: index,
                floorY: group.floor,
                thickness: ceiling - group.floor,
                floorCoverage: coverage,
                placements: items
            )
        }
    }
}

// MARK: - Geometry helpers

private extension PlanStats {
    /// Overlap below this counts as no overlap (float noise on touching faces).
    static let overlapTolerance: Float = 1e-6

    /// Volume counted twice by summing every placement box: the part of each
    /// nested item's box that lies inside its host's, m³, `>= 0`.
    ///
    /// Which nesting counts is `honouredNesting()`'s answer and not ours — a
    /// dangling host, a self-reference or a cycle is not nesting, and must not
    /// buy a discount on the fill figure. The shared volume is measured against
    /// the *host's box*, not the declared cavity: the double count is however much
    /// of the two boxes coincides, whether or not it stays inside the cell the
    /// producer named. Overlap outside the cell is a `.intersection` issue from
    /// `geometryIssues()`; reporting a truthful volume for it is not endorsing it.
    ///
    /// ponytail: pairwise, which is exact for one guest per host and for a chain
    /// where each guest sits inside its host's box. A guest that pokes out of its
    /// host and into its host's host would be over-subtracted; no producer emits
    /// that, and full inclusion–exclusion is the fix if one ever does.
    static func nestedOverlapVolume(_ plan: PackingPlan) -> Float {
        let boxes = Dictionary(
            plan.placements.map { ($0.itemID, $0.box) },
            uniquingKeysWith: { first, _ in first }
        )
        return plan.honouredNesting().reduce(0) { total, entry in
            guard let guest = boxes[entry.key], let host = boxes[entry.value.itemID] else { return total }
            let overlap = guest.overlapExtents(with: host)
            return total + max(overlap.x, 0) * max(overlap.y, 0) * max(overlap.z, 0)
        }
    }

    static func footprintsOverlap(_ a: BoundingBox, _ b: BoundingBox) -> Bool {
        let overlap = a.overlapExtents(with: b)
        return overlap.x > overlapTolerance && overlap.z > overlapTolerance
    }

    /// Union of the boxes' top-down footprints, m². Sweeps the x face planes and
    /// merges z intervals in each slab.
    static func unionFootprintArea(_ boxes: [BoundingBox]) -> Float {
        let xs = Set(boxes.flatMap { [$0.minCorner.x, $0.maxCorner.x] }).sorted()
        guard xs.count > 1 else { return 0 }

        var area: Float = 0
        for index in 0..<(xs.count - 1) {
            let x0 = xs[index], x1 = xs[index + 1]
            let width = x1 - x0
            guard width > overlapTolerance else { continue }

            let spans = boxes
                .filter { $0.minCorner.x <= x0 + overlapTolerance && $0.maxCorner.x >= x1 - overlapTolerance }
                .map { ($0.minCorner.z, $0.maxCorner.z) }
                .sorted { $0.0 < $1.0 }

            var covered: Float = 0
            var cursor = -Float.greatestFiniteMagnitude
            for span in spans {
                let low = max(span.0, cursor)
                if span.1 > low {
                    covered += span.1 - low
                    cursor = span.1
                }
            }
            area += width * covered
        }
        return area
    }

    /// Largest empty axis-aligned box inside `[0, dimensions]` avoiding `boxes`.
    ///
    /// For every candidate x span × z span (the item faces plus the walls), the
    /// blocked y intervals are exactly the y extents of the items crossing that
    /// column, so the tallest free run in the column gives the best box with
    /// that footprint. Taking the max over all spans is exact.
    ///
    /// ponytail: O(n⁴) spans × n items. Fine for a suitcase (tens of items,
    /// ~20k checks at n=5); if a plan ever carries hundreds, switch to a
    /// maximal-empty-box sweep or cap the candidate set.
    static func largestEmptyBox(dimensions: Vector3, boxes: [BoundingBox]) -> BoundingBox? {
        guard dimensions.x > 0, dimensions.y > 0, dimensions.z > 0 else { return nil }

        func candidates(_ axis: Axis) -> [Float] {
            var values: Set<Float> = [0, dimensions[axis]]
            for box in boxes {
                for value in [box.minCorner[axis], box.maxCorner[axis]] where value > 0 && value < dimensions[axis] {
                    values.insert(value)
                }
            }
            return values.sorted()
        }

        let xs = candidates(.x)
        let zs = candidates(.z)

        var best: BoundingBox?
        var bestVolume: Float = 0

        for i in 0..<(xs.count - 1) {
            for j in (i + 1)..<xs.count {
                let x0 = xs[i], x1 = xs[j]
                let width = x1 - x0
                guard width * dimensions.y * dimensions.z > bestVolume else { continue }

                for k in 0..<(zs.count - 1) {
                    for l in (k + 1)..<zs.count {
                        let z0 = zs[k], z1 = zs[l]
                        let depth = z1 - z0
                        let columnArea = width * depth
                        guard columnArea * dimensions.y > bestVolume else { continue }

                        let blocked = boxes
                            .filter {
                                $0.minCorner.x < x1 - overlapTolerance && $0.maxCorner.x > x0 + overlapTolerance
                                    && $0.minCorner.z < z1 - overlapTolerance && $0.maxCorner.z > z0 + overlapTolerance
                            }
                            .map { ($0.minCorner.y, $0.maxCorner.y) }
                            .sorted { $0.0 < $1.0 }

                        var cursor: Float = 0
                        var runs: [(Float, Float)] = []
                        for span in blocked {
                            if span.0 > cursor { runs.append((cursor, span.0)) }
                            cursor = max(cursor, span.1)
                        }
                        if cursor < dimensions.y { runs.append((cursor, dimensions.y)) }

                        for run in runs {
                            let volume = columnArea * (run.1 - run.0)
                            if volume > bestVolume {
                                bestVolume = volume
                                best = BoundingBox(
                                    minCorner: Vector3(x0, run.0, z0),
                                    size: Vector3(width, run.1 - run.0, depth)
                                )
                            }
                        }
                    }
                }
            }
        }
        return best
    }
}

// MARK: - Display

/// Strings for the 2D view, the 3D view and (later) AR. Formatting only — no
/// number here is computed, every one comes from the stored properties above.
/// Metres are converted to centimetres at this boundary and nowhere else.
public extension PlanStats {
    static func percentText(_ fraction: Float) -> String {
        String(format: "%.0f%%", fraction * 100)
    }

    /// A box as `22.0 × 5.0 × 30.0 cm`. Millimetre precision on purpose: a
    /// gap rounded to whole centimetres can claim 0.5 cm the bag does not have.
    static func sizeText(_ size: Vector3) -> String {
        String(format: "%.1f × %.1f × %.1f cm", size.x * 100, size.y * 100, size.z * 100)
    }

    static func lengthText(_ metres: Float) -> String {
        String(format: "%.1f cm", metres * 100)
    }

    /// "27% full".
    var fullnessText: String { "\(PlanStats.percentText(fillFraction)) full" }

    /// "Biggest free block 12 × 20 × 50 cm — 46% of the free space", or a plain
    /// note when the leftover space is scraps or there is none.
    var gapText: String {
        guard let gap = largestGap, largestGapVolume > 0 else { return "No free block left" }
        return "Biggest free block \(PlanStats.sizeText(gap.size)) — "
            + "\(PlanStats.percentText(largestGapFractionOfFree)) of the free space"
    }

    /// "68% of the floor used".
    var floorCoverageText: String { "\(PlanStats.percentText(floorCoverage)) of the floor used" }

    /// "1 layer" / "2 layers".
    var layerCountText: String { layers.count == 1 ? "1 layer" : "\(layers.count) layers" }

    /// Packing order in words, one line per fact that geometry supports.
    var orderText: [String] {
        var lines: [String] = []
        if let first = firstIn { lines.append("First in: \(first.label)") }
        if let last = lastIn, last.itemID != firstIn?.itemID { lines.append("Last in: \(last.label)") }
        if let top = topmost { lines.append("Highest in the bag: \(top.label)") }
        if !loadBearing.isEmpty {
            lines.append("Weight sits on: " + loadBearing.map(\.label).joined(separator: ", "))
        }
        return lines
    }
}

public extension PlanStats.Layer {
    /// "Layer 1 · 3 items · 9.0 cm thick · 62% of the floor".
    var summaryText: String {
        let items = itemCount == 1 ? "1 item" : "\(itemCount) items"
        return "Layer \(index + 1) · \(items) · \(PlanStats.lengthText(thickness)) thick · "
            + "\(PlanStats.percentText(floorCoverage)) of the floor"
    }
}
