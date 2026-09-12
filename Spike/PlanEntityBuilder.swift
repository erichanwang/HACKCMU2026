import PackingPlan
import UIKit
import RealityKit

/// Thickness of the container's wireframe edges, in metres.
private let wireThickness: Float = 0.004
/// Height of the item labels, in metres. Small enough that stacked items in a
/// shallow bag do not overprint each other.
private let labelSize: CGFloat = 0.014

/// Builds the RealityKit entities for a plan's placements.
///
/// Shared by the non-AR scene and the AR overlay so both draw the same boxes
/// from the same rules — same colours, same translucency, same heightmap meshes,
/// and the same min-corner handling. Only the chrome around them differs: the
/// scene adds a wireframe and a camera, AR parents this to the bag anchor.
@MainActor
struct PlanEntityBuilder {
    let plan: PackingPlan
    let scans: [String: ScannedItem]

    /// Everything a view needs to show a plan and peel it apart.
    struct Content {
        /// Parent this to a scene anchor, or to the AR bag anchor.
        let root: Entity
        /// Placement entities bucketed by layer index, bottom layer first.
        let entitiesByLayer: [[Entity]]
        /// Label pivots, if any were built — they need re-aiming when the camera moves.
        let labels: [Entity]

        /// Shows layers 0 through `topLayer`, peeling the bag apart from the top down.
        func show(upTo topLayer: Int) {
            for (index, entities) in entitiesByLayer.enumerated() {
                let visible = index <= topLayer
                for entity in entities { entity.isEnabled = visible }
            }
        }
    }

    /// - Parameters:
    ///   - includeLabels: 3D text costs nothing to place but has to be turned to
    ///     face the camera. The scene does that when its camera moves; in AR the
    ///     camera moves every frame, so the overlay leaves them out.
    ///   - includeWireframe: the container outline. AR needs it to align the bag
    ///     against the real one; it is the only visible cue for the yaw and
    ///     position corrections.
    func build(includeLabels: Bool, includeWireframe: Bool = false) -> Content {
        let root = Entity()
        if includeWireframe {
            root.addChild(Self.wireframeBox(size: plan.container.dimensions))
        }
        var byLayer: [[Entity]] = []
        var labels: [Entity] = []

        for layer in plan.layers() {
            var entities: [Entity] = []
            for placement in layer.placements {
                let box = itemEntity(placement, scan: scans[placement.itemID])
                root.addChild(box)
                entities.append(box)

                if includeLabels {
                    let label = labelPivot(for: placement)
                    root.addChild(label)
                    labels.append(label)
                    entities.append(label)
                }
            }
            byLayer.append(entities)
        }

        return Content(root: root, entitiesByLayer: byLayer, labels: labels)
    }

    func itemEntity(_ placement: Placement, scan: ScannedItem?) -> ModelEntity {
        let material = SimpleMaterial(
            color: color(for: placement).withAlphaComponent(0.45),
            roughness: 0.6,
            isMetallic: false
        )

        if let scan, let mesh = HeightmapMesh.generate(from: scan, fitting: placement) {
            let entity = ModelEntity(mesh: mesh, materials: [material])
            entity.position = placement.position.simd
            return entity
        }

        let entity = ModelEntity(
            mesh: .generateBox(size: placement.size.simd, cornerRadius: 0.002),
            materials: [material]
        )
        // `renderCenter` is the shared min-corner → centre helper; never inline the
        // `size / 2` here.
        entity.position = placement.renderCenter.simd
        return entity
    }

    func labelPivot(for placement: Placement) -> Entity {
        let mesh = MeshResource.generateText(
            placement.label,
            extrusionDepth: 0.0004,
            font: .systemFont(ofSize: labelSize, weight: .medium),
            alignment: .center
        )
        let text = ModelEntity(mesh: mesh, materials: [UnlitMaterial(color: .label)])
        // generateText anchors at the baseline's left edge; recentre on the pivot.
        text.position = -text.visualBounds(relativeTo: nil).center

        let pivot = Entity()
        pivot.addChild(text)
        var centre = placement.renderCenter
        centre.y = placement.box.maxCorner.y + 0.012
        pivot.position = centre.simd
        return pivot
    }

    func color(for placement: Placement) -> UIColor {
        UIColor(
            hue: (CGFloat(placement.step) * 0.17).truncatingRemainder(dividingBy: 1),
            saturation: 0.65,
            brightness: 0.85,
            alpha: 1
        )
    }

    /// Wireframe as twelve thin bars — RealityKit has no line primitive.
    static func wireframeBox(size: Vector3) -> Entity {
        let container = Entity()
        let material = SimpleMaterial(color: .systemGray, roughness: 0.5, isMetallic: false)
        let extent = size.simd

        for axis in 0..<3 {
            var barSize = SIMD3<Float>(repeating: wireThickness)
            barSize[axis] = extent[axis]

            // The four edges parallel to `axis` sit at the corners of the other two.
            let otherA = (axis + 1) % 3
            let otherB = (axis + 2) % 3
            for a in [Float(0), 1] {
                for b in [Float(0), 1] {
                    let bar = ModelEntity(mesh: .generateBox(size: barSize), materials: [material])
                    var position = SIMD3<Float>(repeating: 0)
                    position[axis] = extent[axis] / 2
                    position[otherA] = a * extent[otherA]
                    position[otherB] = b * extent[otherB]
                    bar.position = position
                    container.addChild(bar)
                }
            }
        }
        return container
    }
}
