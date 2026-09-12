import ARKit
import RealityKit
import SwiftUI

/// LiDAR reads slightly inside true edges; nudge every dimension outward. Tune against a known box.
let paddingMeters: Float = 0.005
/// Points closer to the table than this are treated as the table itself.
let minHeightMeters: Float = 0.01
/// Max horizontal distance from the tap to consider points.
let searchRadiusMeters: Float = 0.5
/// Grid cell for separating the tapped object from its neighbours.
let clusterCellMeters: Float = 0.02
/// Resolution of the captured shape (heightmap cell). Mesh triangles are sampled at half this spacing.
let shapeCellMeters: Float = 0.01

struct ScanView: UIViewRepresentable {
    @Binding var item: ScannedItem?
    @Binding var status: String

    func makeUIView(context: Context) -> ARView {
        let view = ARView(frame: .zero)
        let config = ARWorldTrackingConfiguration()
        config.planeDetection = .horizontal
        config.sceneReconstruction = .mesh
        view.session.run(config)
        view.debugOptions = [.showSceneUnderstanding]
        view.addGestureRecognizer(UITapGestureRecognizer(target: context.coordinator, action: #selector(Coordinator.tap)))
        context.coordinator.view = view
        return view
    }

    func updateUIView(_ uiView: ARView, context: Context) {}
    func makeCoordinator() -> Coordinator { Coordinator(item: $item, status: $status) }

    final class Coordinator: NSObject {
        @Binding var item: ScannedItem?
        @Binding var status: String
        weak var view: ARView?
        var overlay: AnchorEntity?

        init(item: Binding<ScannedItem?>, status: Binding<String>) { _item = item; _status = status }

        @objc func tap(_ g: UITapGestureRecognizer) {
            guard let view, let frame = view.session.currentFrame else { return }
            let point = g.location(in: view)

            // Seed: whatever surface the ray hits (object top, most likely).
            guard let hit = view.raycast(from: point, allowing: .estimatedPlane, alignment: .any).first else {
                status = "No surface under tap"; return
            }
            let seed = SIMD3<Float>(hit.worldTransform.columns.3.x, hit.worldTransform.columns.3.y, hit.worldTransform.columns.3.z)

            // Table: the horizontal plane just below the seed. Largest such plane wins.
            let planes = frame.anchors.compactMap { $0 as? ARPlaneAnchor }
                .filter { $0.alignment == .horizontal && $0.transform.columns.3.y < seed.y }
            guard let table = planes.max(by: { $0.planeExtent.width * $0.planeExtent.height < $1.planeExtent.width * $1.planeExtent.height }) else {
                status = "No table plane yet — pan around"; return
            }
            let planeY = table.transform.columns.3.y

            // Mesh surface above the table near the tap, sampled densely across each triangle.
            var pts: [SIMD3<Float>] = []
            let r2 = searchRadiusMeters * searchRadiusMeters
            for mesh in frame.anchors.compactMap({ $0 as? ARMeshAnchor }) {
                let v = mesh.geometry.vertices, f = mesh.geometry.faces
                func vertex(_ i: UInt32) -> SIMD3<Float> {
                    let raw = v.buffer.contents().advanced(by: v.offset + v.stride * Int(i)).assumingMemoryBound(to: SIMD3<Float>.self).pointee
                    let w = mesh.transform * SIMD4<Float>(raw, 1)
                    return SIMD3<Float>(w.x, w.y, w.z)
                }
                let idx = f.buffer.contents().assumingMemoryBound(to: UInt32.self)
                for t in 0..<f.count {
                    let a = vertex(idx[t * 3]), b = vertex(idx[t * 3 + 1]), c = vertex(idx[t * 3 + 2])
                    let m = (a + b + c) / 3
                    let dx = m.x - seed.x, dz = m.z - seed.z
                    guard dx * dx + dz * dz < r2, max(a.y, b.y, c.y) - planeY > minHeightMeters else { continue }
                    pts += densify(a, b, c, spacing: shapeCellMeters / 2).filter { $0.y - planeY > minHeightMeters }
                }
            }

            let cluster = connectedCluster(pts, seed: seed, cell: clusterCellMeters)
            guard let box = fitBox(points: cluster, planeY: planeY, padding: paddingMeters) else {
                status = "Nothing above the table here (\(pts.count) pts)"; return
            }
            let heights = heightMap(points: cluster, box: box, planeY: planeY, cell: shapeCellMeters)
            let scanned = ScannedItem(box, heights: heights, cell: shapeCellMeters)
            item = scanned
            status = "\(cluster.count) pts, \(heights.count)×\(heights[0].count) cells"
            print(scanned.asciiMap)
            print(String(data: try! JSONEncoder().encode(scanned), encoding: .utf8)!)
            show(box, in: view)
        }

        func show(_ box: BoxFit, in view: ARView) {
            overlay?.removeFromParent()
            let mesh = MeshResource.generateBox(width: box.width, height: box.height, depth: box.depth)
            let mat = SimpleMaterial(color: .systemGreen.withAlphaComponent(0.4), isMetallic: false)
            let entity = ModelEntity(mesh: mesh, materials: [mat])
            entity.orientation = simd_quatf(from: SIMD3<Float>(1, 0, 0), to: box.axis)
            let anchor = AnchorEntity(world: box.center)
            anchor.addChild(entity)
            view.scene.addAnchor(anchor)
            overlay = anchor
        }
    }
}
