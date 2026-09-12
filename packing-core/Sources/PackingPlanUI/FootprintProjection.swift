// Linux has no CoreGraphics module, but its Foundation already provides
// CGFloat/CGPoint/CGSize/CGRect with the same API, so it's a drop-in fallback.
#if canImport(CoreGraphics)
import CoreGraphics
#else
import Foundation
#endif
import PackingPlan

/// Maps the bag's footprint onto a view rect for the top-down diagram.
///
/// The diagram looks straight down the **Y** axis, so the plane is bag **X × Z**:
///
/// - bag `+X` (width) → view `+x`, to the right
/// - bag `+Z` (depth) → view `+y`, down the screen
/// - bag `(0, ·, 0)` → the footprint's top-left corner
///
/// This is a schematic projection and is deliberately independent of the
/// handedness question left open in CLAUDE.md: a 2D floor plan needs a stated
/// screen mapping, not a world frame. Nothing here should be reused for the AR
/// anchor.
///
/// Aspect ratio is preserved and the footprint is centred in the space given.
public struct FootprintProjection: Equatable, Sendable {
    /// View points per metre.
    public let scale: CGFloat

    /// Where the bag's footprint lands in view space.
    public let footprintRect: CGRect

    public init(footprint dimensions: Vector3, in available: CGSize) {
        let width = CGFloat(dimensions.x)
        let depth = CGFloat(dimensions.z)

        guard width > 0, depth > 0, available.width > 0, available.height > 0 else {
            self.scale = 0
            self.footprintRect = .zero
            return
        }

        let scale = min(available.width / width, available.height / depth)
        let size = CGSize(width: width * scale, height: depth * scale)

        self.scale = scale
        self.footprintRect = CGRect(
            x: (available.width - size.width) / 2,
            y: (available.height - size.height) / 2,
            width: size.width,
            height: size.height
        )
    }

    /// The view-space rect for a placement's box, seen from above.
    ///
    /// `CGRect.origin` is the box's **min corner** projected onto X/Z — the same
    /// convention the plan uses, so there is deliberately no `size / 2` term
    /// here. The half-size offset belongs to the 3D renderer, which positions
    /// entities by their centre; a 2D rect is already an origin-and-extent.
    public func rect(for box: BoundingBox) -> CGRect {
        CGRect(
            x: footprintRect.minX + CGFloat(box.minCorner.x) * scale,
            y: footprintRect.minY + CGFloat(box.minCorner.z) * scale,
            width: CGFloat(box.size.x) * scale,
            height: CGFloat(box.size.z) * scale
        )
    }

    public func rect(for placement: Placement) -> CGRect {
        rect(for: placement.box)
    }
}
