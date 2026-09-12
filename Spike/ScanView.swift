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
/// How far below the tap a plane can be and still count as "the table". Keeps a room's
/// floor from being accepted when no real table plane has been found near the object yet.
let maxTableDropMeters: Float = 1.0
/// No single packed item or suitcase is bigger than this in any dimension. Catches a
/// cluster that swallowed a wall or the floor before it reaches the server.
let maxItemDimensionMeters: Float = 1.2
/// Grid cell for separating the tapped object from its neighbours.
let clusterCellMeters: Float = 0.02
/// Resolution of the captured shape (heightmap cell). Mesh triangles are sampled at half this spacing.
let shapeCellMeters: Float = 0.01
/// Suitcase wall + floor thickness subtracted from the scanned outer shell to get the interior.
/// Calibration knob: measure a real bag (outer minus inner, halved) and set it before the demo.
let suitcaseWallMeters: Float = 0.01
/// Extra floor clearance for the wheel well. Calibration knob: measure a real bag and set it
/// before the demo. Defaults to `suitcaseWallMeters` (bit-identical to no calibration).
let suitcaseFloorWallMeters: Float = suitcaseWallMeters
/// Extra depth clearance for the telescoping handle's spine. Calibration knob: measure a real
/// bag and set it before the demo. Defaults to `suitcaseWallMeters` (bit-identical to no calibration).
let suitcaseHandleWallMeters: Float = suitcaseWallMeters

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
        view.session.delegate = context.coordinator
        return view
    }

    func updateUIView(_ uiView: ARView, context: Context) {
        context.coordinator.mode = mode
        context.coordinator.showPlan(plan, in: uiView)
    }
    func makeCoordinator() -> Coordinator { Coordinator(item: $item, status: $status, suitcaseId: $suitcaseId, mode: mode) }

    @MainActor final class Coordinator: NSObject {
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
        /// One axis fit per suitcase-mode tap. Averaged (see PlanAnchor.averageAxis) so a single
        /// noisy `minAreaRect` fit doesn't set the bag's rotation for the whole session.
        var axisSamples: [SIMD3<Float>] = []

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

            // Table: the horizontal plane nearest below the seed (see pickTablePlane).
            let planeYs = frame.anchors.compactMap { anchor -> Float? in
                guard let plane = anchor as? ARPlaneAnchor, plane.alignment == .horizontal else { return nil }
                return plane.transform.columns.3.y
            }
            guard let planeY = pickTablePlane(planeYs: planeYs, seedY: seed.y, maxDrop: maxTableDropMeters) else {
                status = "No table plane close enough — pan the table, then tap the object"; return
            }
            guard seed.y - planeY > minHeightMeters else {
                status = "Tap the object, not the table"; return
            }

            // Mesh surface above the table near the tap, sampled densely across each triangle.
            var pts: [SIMD3<Float>] = []
            let r2 = searchRadiusMeters * searchRadiusMeters
            for mesh in frame.anchors.compactMap({ $0 as? ARMeshAnchor }) {
                let v = mesh.geometry.vertices, f = mesh.geometry.faces
                for (a, b, c) in meshTriangles(
                    vertexBuffer: v.buffer.contents(), vertexOffset: v.offset, vertexStride: v.stride,
                    indexBuffer: f.buffer.contents(), primitiveCount: f.count,
                    indexCountPerPrimitive: f.indexCountPerPrimitive, bytesPerIndex: f.bytesPerIndex,
                    worldVertex: { let w = mesh.transform * SIMD4<Float>($0, 1); return SIMD3<Float>(w.x, w.y, w.z) }
                ) {
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
            guard isUsableBox(width: box.width, height: box.height, depth: box.depth, maxDimension: maxItemDimensionMeters) else {
                status = "That doesn't look like one item — check for a nearby wall or the floor, then rescan"; return
            }
            // Suitcase mode: the fitted box is the bag's outer shell; the interior is a wall thinner.
            // No heightmap, no photo, no label.
            if mode == .suitcase {
                // Average this tap's axis fit in with earlier ones — one fit alone is noisy
                // enough to matter (see tests/swift/drift); a re-tap steadies it further.
                axisSamples.append(box.axis)
                let steadied = BoxFit(width: box.width, depth: box.depth, height: box.height,
                                      center: box.center, axis: averageAxis(axisSamples))
                let interior = interiorBox(steadied, wall: suitcaseWallMeters,
                                            wallHeight: suitcaseFloorWallMeters, wallDepth: suitcaseHandleWallMeters)
                suitcase = (interior, planeY + suitcaseWallMeters)
                show(interior, in: view)
                status = "Creating suitcase… (tap the bag again to steady it, or switch to Item)"
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
            guard isUsableHeightMap(heights) else {
                status = "Nothing usable captured on top — move closer and rescan"; return
            }
            var scanned = ScannedItem(box, heights: heights, cell: shapeCellMeters)
            scanned.suitcaseId = suitcaseId
            item = scanned
            status = "\(cluster.count) pts, \(heights.count)×\(heights[0].count) cells — labelling…"
            print(scanned.asciiMap)

            // Photograph the object before the overlay covers it, then ask the server for label + rigidity.
            guard let crop = screenRect(of: box, in: view) else {
                status = "Object out of view for the photo — item saved without a label"
                show(box, in: view)
                return
            }
            view.snapshot(saveToHDR: false) { [weak self] shot in
                guard let self else { return }
                guard let shot, let cg = shot.cgImage?.cropping(to: crop.applying(.init(scaleX: shot.scale, y: shot.scale))) else {
                    Task { @MainActor in self.status = "Couldn't capture a photo — item saved without a label" }
                    return
                }
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
        /// Nil when no corner projects (e.g. the box is behind the camera) — there's nothing sane to crop.
        func screenRect(of box: BoxFit, in view: ARView) -> CGRect? {
            let perp = SIMD3<Float>(-box.axis.z, 0, box.axis.x)
            var pts: [CGPoint] = []
            for sx: Float in [-0.5, 0.5] { for sy: Float in [-0.5, 0.5] { for sz: Float in [-0.5, 0.5] {
                let corner = box.center + box.axis * (sx * box.width) + SIMD3<Float>(0, sy * box.height, 0) + perp * (sz * box.depth)
                if let p = view.project(corner) { pts.append(p) }
            } } }
            guard let minX = pts.map(\.x).min(), let maxX = pts.map(\.x).max(),
                  let minY = pts.map(\.y).min(), let maxY = pts.map(\.y).max() else { return nil }
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
            // planeY: 0 here — the floor height is baked into the anchor's own position below,
            // not into these local (bag-frame) coordinates, so refreshPlaneY can move the whole
            // overlay by updating one number instead of rebuilding every box.
            let frame = PlanAnchor(interior: suitcase.interior, planeY: 0)
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
            refreshPlaneY()
        }

        /// The table plane's y as ARKit currently estimates it — refined continuously as ARKit
        /// tracks the plane, unlike the `Float` frozen in `suitcase` at scan time, which never
        /// hears about any correction ARKit makes afterwards. Falls back to that stored value
        /// when no plane is visible this frame (e.g. the table's out of view).
        func currentPlaneY() -> Float? {
            guard let suitcase, let frame = view?.session.currentFrame else { return nil }
            let planeYs = frame.anchors.compactMap { anchor -> Float? in
                guard let plane = anchor as? ARPlaneAnchor, plane.alignment == .horizontal else { return nil }
                return plane.transform.columns.3.y
            }
            return pickTablePlane(planeYs: planeYs, seedY: suitcase.interior.center.y, maxDrop: maxTableDropMeters)
        }

        /// Re-anchors the plan overlay's height to the table plane ARKit reports *right now*,
        /// called every AR frame via `ARSessionDelegate`. Only `planOverlay`'s own position
        /// moves (cheap); the boxes underneath keep the bag-frame coordinates `showPlan` built.
        func refreshPlaneY() {
            guard let planOverlay, let suitcase else { return }
            planOverlay.position.y = currentPlaneY() ?? suitcase.planeY
        }
    }
}

extension ScanView.Coordinator: ARSessionDelegate {
    func session(_ session: ARSession, didUpdate frame: ARFrame) {
        refreshPlaneY()
    }
}
