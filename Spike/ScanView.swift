import ARKit
import PackingPlan
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
/// Suitcase wall + floor thickness subtracted from the scanned outer shell to get the interior.
/// Calibration knob: measure a real bag (outer minus inner, halved) and set it before the demo.
let suitcaseWallMeters: Float = 0.01

/// What the next tap captures: the bag itself, or something to put in it.
enum ScanMode: Hashable {
    case suitcase, item
}

struct ScanView: UIViewRepresentable {
    @Binding var item: ScannedItem?
    @Binding var status: String
    @Binding var suitcaseId: String?
    @Binding var plan: PackingPlan?
    var mode: ScanMode

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

    func updateUIView(_ uiView: ARView, context: Context) {
        context.coordinator.mode = mode
        context.coordinator.showPlan(plan, in: uiView)
    }
    func makeCoordinator() -> Coordinator { Coordinator(item: $item, status: $status, suitcaseId: $suitcaseId, mode: mode) }

    final class Coordinator: NSObject {
        @Binding var item: ScannedItem?
        @Binding var status: String
        @Binding var suitcaseId: String?
        var mode: ScanMode
        weak var view: ARView?
        var overlay: AnchorEntity?
        /// The bag interior from the last Suitcase-mode tap, and the y of its floor. The plan overlay
        /// has nowhere to go until this is set.
        var suitcase: (interior: BoxFit, planeY: Float)?
        var planOverlay: AnchorEntity?
        var shownPlan: PackingPlan?

        init(item: Binding<ScannedItem?>, status: Binding<String>, suitcaseId: Binding<String?>, mode: ScanMode) {
            _item = item; _status = status; _suitcaseId = suitcaseId; self.mode = mode
        }

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
            // Suitcase mode: the fitted box is the bag's outer shell; the interior is a wall thinner.
            // No heightmap, no photo, no label.
            if mode == .suitcase {
                let interior = interiorBox(box, wall: suitcaseWallMeters)
                suitcase = (interior, planeY + suitcaseWallMeters)
                show(interior, in: view)
                status = "Creating suitcase…"
                Task { @MainActor in
                    do {
                        self.suitcaseId = try await API.createSuitcase(
                            name: "scanned suitcase", dimensions: [interior.width, interior.height, interior.depth])
                        self.status = "Suitcase captured — switch to Item and tap what goes in"
                    } catch {
                        self.status = "server: \(error.localizedDescription)"
                    }
                }
                return
            }
            guard let suitcaseId else { status = "Scan the suitcase first"; return }

            let heights = heightMap(points: cluster, box: box, planeY: planeY, cell: shapeCellMeters)
            var scanned = ScannedItem(box, heights: heights, cell: shapeCellMeters)
            scanned.suitcaseId = suitcaseId
            item = scanned
            status = "\(cluster.count) pts, \(heights.count)×\(heights[0].count) cells — labelling…"
            print(scanned.asciiMap)
            print(String(data: try! JSONEncoder().encode(scanned), encoding: .utf8)!)

            // Photograph the object before the overlay covers it, then ask the server for label + rigidity.
            let crop = screenRect(of: box, in: view)
            view.snapshot(saveToHDR: false) { [weak self] shot in
                guard let self, let shot, let cg = shot.cgImage?.cropping(to: crop.applying(.init(scaleX: shot.scale, y: shot.scale))) else { return }
                Task { @MainActor in
                    do {
                        let uploaded = try await API.upload(scanned, image: UIImage(cgImage: cg))
                        self.item = uploaded
                        self.status = "\(cluster.count) pts, \(heights.count)×\(heights[0].count) cells"
                        if uploaded.labelStatus == "pending" { await self.pollLabel(id: uploaded.id) }
                    } catch {
                        self.status = "server: \(error.localizedDescription)"
                    }
                }
            }
            show(box, in: view)
        }

        /// Grok failed at upload and the server is retrying in the background; wait for the label.
        /// ponytail: fixed 3 s poll for 60 s, no backoff — the server retries every 10 s and gives up
        /// after 5 attempts, so a longer wait would only watch it fail.
        @MainActor func pollLabel(id: String) async {
            status = "labelling…"
            for _ in 0..<20 {
                try? await Task.sleep(for: .seconds(3))
                guard let fresh = try? await API.get(id: id), fresh.labelStatus != "pending" else { continue }
                item = fresh
                status = "labelled \(fresh.label ?? "?")"
                return
            }
            status = "still unlabelled — type it in"
        }

        /// Screen-space rectangle around the box's projected corners, padded 15%, clamped to the view.
        func screenRect(of box: BoxFit, in view: ARView) -> CGRect {
            let perp = SIMD3<Float>(-box.axis.z, 0, box.axis.x)
            var pts: [CGPoint] = []
            for sx: Float in [-0.5, 0.5] { for sy: Float in [-0.5, 0.5] { for sz: Float in [-0.5, 0.5] {
                let corner = box.center + box.axis * (sx * box.width) + SIMD3<Float>(0, sy * box.height, 0) + perp * (sz * box.depth)
                if let p = view.project(corner) { pts.append(p) }
            } } }
            guard let minX = pts.map(\.x).min(), let maxX = pts.map(\.x).max(),
                  let minY = pts.map(\.y).min(), let maxY = pts.map(\.y).max() else { return view.bounds }
            return CGRect(x: minX, y: minY, width: maxX - minX, height: maxY - minY)
                .insetBy(dx: -(maxX - minX) * 0.15, dy: -(maxY - minY) * 0.15)
                .intersection(view.bounds)
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

        /// One translucent box per placement, inside the scanned bag. Colour cycles by step so
        /// neighbouring items read apart; the 2D sheet carries the legend, so no text in the scene.
        static let stepColors: [UIColor] = [.systemBlue, .systemOrange, .systemPurple, .systemTeal, .systemPink]

        func showPlan(_ plan: PackingPlan?, in view: ARView) {
            guard plan != shownPlan else { return }
            shownPlan = plan
            planOverlay?.removeFromParent()
            planOverlay = nil
            guard let plan, let suitcase else { return }
            let frame = PlanAnchor(interior: suitcase.interior, planeY: suitcase.planeY)
            let orientation = simd_quatf(from: SIMD3<Float>(1, 0, 0), to: suitcase.interior.axis)
            let anchor = AnchorEntity(world: SIMD3<Float>(0, 0, 0))
            for p in plan.placements {
                let size = SIMD3<Float>(p.size.x, p.size.y, p.size.z)
                let color = Self.stepColors[(p.step - 1) % Self.stepColors.count]
                let mesh = MeshResource.generateBox(width: size.x, height: size.y, depth: size.z)
                let entity = ModelEntity(
                    mesh: mesh,
                    materials: [SimpleMaterial(color: color.withAlphaComponent(0.4), isMetallic: false)])
                entity.orientation = orientation
                entity.position = frame.worldCenter(
                    position: SIMD3<Float>(p.position.x, p.position.y, p.position.z), size: size)
                anchor.addChild(entity)
            }
            view.scene.addAnchor(anchor)
            planOverlay = anchor
        }
    }
}
