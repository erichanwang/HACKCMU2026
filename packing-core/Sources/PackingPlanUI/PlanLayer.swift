import Foundation
import PackingPlan

/// One horizontal layer of a plan: the placements whose **floor** sits at the
/// same height, grouped for the 2D top-down view.
///
/// Grouping is by `position.y` — the min corner — not by the middle or top of an
/// item, so an item belongs to the layer it rests on.
public struct PlanLayer: Identifiable, Hashable, Sendable {
    /// 0 is the layer resting on the bag floor.
    public let index: Int

    /// Height of this layer's floor, in metres.
    public let floorY: Float

    /// The layer's own placements, in packing order.
    public let placements: [Placement]

    public init(index: Int, floorY: Float, placements: [Placement]) {
        self.index = index
        self.floorY = floorY
        self.placements = placements
    }

    public var id: Int { index }

    /// The highest point reached by anything in this layer, in metres.
    public var ceilingY: Float {
        placements.map { $0.box.maxCorner.y }.max() ?? floorY
    }

    /// How tall the layer is, in metres — the thickest item in it.
    public var thickness: Float { ceilingY - floorY }
}

public extension PackingPlan {
    /// Floors within this distance of each other count as the same layer.
    /// 5 mm: tight enough to keep real layers apart in a 15 cm cavity, loose
    /// enough that a solver emitting 0.0750001 does not create a phantom layer.
    static let defaultLayerTolerance: Float = 0.005

    /// Placements grouped into horizontal layers, bottom first.
    ///
    /// Each placement appears in exactly one layer. Items are compared against
    /// the *group's* floor rather than the previous item, so a run of slightly
    /// increasing heights cannot chain into one oversized layer.
    func layers(tolerance: Float = PackingPlan.defaultLayerTolerance) -> [PlanLayer] {
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

        return groups.enumerated().map { index, group in
            PlanLayer(
                index: index,
                floorY: group.floor,
                placements: group.items.sorted { $0.step < $1.step }
            )
        }
    }

    /// Items from **lower** layers that poke up through `layer`'s floor.
    ///
    /// A top-down diagram that showed only the layer's own items would imply
    /// free floor where a tall item from below is actually in the way — in the
    /// demo plan the shoes and dopp kit stand 11.5 cm and 14 cm tall in a bag
    /// whose second layer starts at 7.5 cm. The view draws these as outlines.
    func protrusions(
        into layer: PlanLayer,
        tolerance: Float = PackingPlan.defaultLayerTolerance
    ) -> [Placement] {
        placements
            .filter { placement in
                placement.position.y < layer.floorY - tolerance
                    && placement.box.maxCorner.y > layer.floorY + tolerance
            }
            .sorted { $0.step < $1.step }
    }
}
