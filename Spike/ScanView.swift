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
/// Edge length of one colour/occupancy voxel. 5 mm is about the useful floor for iPhone
/// LiDAR: finer mostly records noise, since a single depth sample is only good to ~1 cm.
let voxelSizeMeters: Float = 0.005
/// A voxel seen fewer times than this is dropped as noise when the scan is finished.
let minVoxelObservations: UInt32 = 2
/// Integrate a frame only after the camera has moved this far, so a still phone does not
/// pile thousands of identical observations into the same voxels.
let reintegrateDistanceMeters: Float = 0.02
/// ...or turned this far, which matters when orbiting a small object up close.
let reintegrateRadians: Float = 0.10

/// What the next tap captures: the bag itself, or something to put in it.
enum ScanMode: Hashable {
    case suitcase, item
}

struct ScanView: UIViewRepresentable {
    @Binding var item: ScannedItem?
    @Binding var status: String
    @Binding var suitcaseId: String?
    var mode: ScanMode

    func makeUIView(context: Context) -> ARView {
        let view = ARView(frame: .zero)
        let config = ARWorldTrackingConfiguration()
        config.planeDetection = .horizontal
        config.sceneReconstruction = .mesh
        view.session.run(config)
        view.session.delegate = context.coordinator
        view.debugOptions = [.showSceneUnderstanding]
        view.addGestureRecognizer(UITapGestureRecognizer(target: context.coordinator, action: #selector(Coordinator.tap)))
        context.coordinator.view = view
        return view
    }

    func updateUIView(_ uiView: ARView, context: Context) { context.coordinator.mode = mode }
    func makeCoordinator() -> Coordinator { Coordinator(item: $item, status: $status, suitcaseId: $suitcaseId, mode: mode) }

    /// Walking a LiDAR-equipped phone around an object and fusing every posed frame into one
    /// coloured voxel grid is the same idea photogrammetry rigs use to build a textured 3D
    /// model from photos (see the treehcks-style pipeline this borrows the pixel-projection
    /// math from) -- except there is no photo set and no offline reconstruction step. ARKit
    /// already gives the geometry (the mesh) and the pose for free every frame, so the only
    /// thing borrowed is "project a 3D point into a posed camera image and read its colour"
    /// (`FrameSampler.swift`); it runs live, on-device, as part of the same tap-to-scan gesture
    /// the app already had -- there is no separate scanning mode to trigger.
    final class Coordinator: NSObject, ARSessionDelegate {
        @Binding var item: ScannedItem?
        @Binding var status: String
        @Binding var suitcaseId: String?
        var mode: ScanMode
        weak var view: ARView?
        var overlay: AnchorEntity?

        /// Everything accumulated since the scan started. One grid, many viewpoints -- fusing
        /// passes is just writing into it again, because ARKit reports every frame in the same
        /// world frame.
        private var grid = VoxelGrid(voxelSize: voxelSizeMeters)
        /// The same triangles ARKit already gives us, coloured per vertex -- kept alongside
        /// the voxel grid so the item can be rendered as a filled surface, not just points.
        private var coloredMesh = ColoredMesh()
        private var coverage = ViewCoverage()
        private var seed: SIMD3<Float>?
        private var planeY: Float = 0
        private var lastPose: simd_float4x4?
        private var isScanning = false

        init(item: Binding<ScannedItem?>, status: Binding<String>, suitcaseId: Binding<String?>, mode: ScanMode) {
            _item = item; _status = status; _suitcaseId = suitcaseId; self.mode = mode
        }

        // MARK: - Tap: start, then finish

        @objc func tap(_ g: UITapGestureRecognizer) {
            guard let view, let frame = view.session.currentFrame else { return }
            if isScanning { finish(frame: frame) } else { begin(at: g.location(in: view), frame: frame) }
        }

        private func begin(at point: CGPoint, frame: ARFrame) {
            guard let view else { return }
            // Seed: whatever surface the ray hits (object top, most likely).
            guard let hit = view.raycast(from: point, allowing: .estimatedPlane, alignment: .any).first else {
                status = "No surface under tap"; return
            }
            let s = SIMD3<Float>(hit.worldTransform.columns.3.x, hit.worldTransform.columns.3.y, hit.worldTransform.columns.3.z)

            // Table: the horizontal plane just below the seed. Largest such plane wins.
            let planes = frame.anchors.compactMap { $0 as? ARPlaneAnchor }
                .filter { $0.alignment == .horizontal && $0.transform.columns.3.y < s.y }
            guard let table = planes.max(by: { $0.planeExtent.width * $0.planeExtent.height < $1.planeExtent.width * $1.planeExtent.height }) else {
                status = "No table plane yet — pan around"; return
            }

            seed = s
            planeY = table.transform.columns.3.y
            grid = VoxelGrid(voxelSize: voxelSizeMeters)
            coloredMesh = ColoredMesh()
            coverage = ViewCoverage()
            lastPose = nil
            isScanning = true
            overlay?.removeFromParent()
            status = "Scanning — walk around the object, tap again when done"
            integrate(frame: frame)
        }

        // MARK: - Per-frame accumulation

        func session(_ session: ARSession, didUpdate frame: ARFrame) {
            guard isScanning else { return }
            // Only integrate once the viewpoint has actually changed. Repeated samples from a
            // stationary phone inflate observation counts without adding information, which
            // would defeat the noise filter that keys on those counts.
            let pose = frame.camera.transform
            if let last = lastPose {
                let moved = simd_length(pose.columns.3.xyz - last.columns.3.xyz)
                // Angle between the two camera forward axes (ARKit looks down -Z).
                let a = simd_normalize(-last.columns.2.xyz), b = simd_normalize(-pose.columns.2.xyz)
                let turned = acos(max(-1, min(1, simd_dot(a, b))))
                guard moved > reintegrateDistanceMeters || turned > reintegrateRadians else { return }
            }
            lastPose = pose
            integrate(frame: frame)
        }

        private func integrate(frame: ARFrame) {
            guard let seed else { return }
            let posed = PosedFrame(
                viewMatrix: frame.camera.transform.inverse,
                intrinsics: frame.camera.intrinsics,
                imageSize: SIMD2(Float(frame.camera.imageResolution.width),
                                 Float(frame.camera.imageResolution.height))
            )
            // One lock for the whole frame; locking per point would dominate the cost.
            let sampler = PixelSampler(frame.capturedImage)
            defer { sampler?.release() }

            let radius = mode == .suitcase ? searchRadiusMeters * 2 : searchRadiusMeters
            let r2 = radius * radius
            var added = 0

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
                    for p in densify(a, b, c, spacing: voxelSizeMeters) where p.y - planeY > minHeightMeters {
                        var colour: SIMD3<Float>?
                        if let sampler, let px = posed.project(p) { colour = sampler.colour(at: px) }
                        grid.add(p, colour: colour)
                        added += 1
                    }
                    // Same triangle ARKit already triangulated -- keep it, coloured per vertex,
                    // for a filled render instead of re-deriving a sparse point cloud from it.
                    coloredMesh.add(anchorID: mesh.identifier, triangleIndex: t, a: a, b: b, c: c) { p in
                        guard let sampler, let px = posed.project(p) else { return nil }
                        return sampler.colour(at: px)
                    }
                }
            }
            guard added > 0 else { return }

            coverage.record(direction: posed.directionToCamera(from: seed))
            status = String(format: "%d voxels · %.0f%% covered", grid.count, coverage.fraction * 100)
        }

        // MARK: - Finish

        private func finish(frame: ARFrame) {
            isScanning = false
            guard let view, let seed else { return }

            // Noise first, then connectivity: pruning removes the stray single-sample voxels
            // that would otherwise bridge the object to its surroundings.
            let solid = grid.pruned(minObservations: minVoxelObservations).component(containing: seed)
            guard !solid.isEmpty, let box = fitBox(points: solid.points, planeY: planeY, padding: paddingMeters) else {
                status = "Nothing solid captured — try scanning again, more slowly"
                return
            }

            // Suitcase mode: the fitted box *is* the bag interior. No heightmap, no photo, no label.
            if mode == .suitcase {
                show(box, in: view)
                status = "Creating suitcase…"
                Task { @MainActor in
                    do {
                        self.suitcaseId = try await API.createSuitcase(
                            name: "scanned suitcase", dimensions: [box.width, box.height, box.depth])
                        self.status = "Suitcase captured — switch to Item and tap what goes in"
                    } catch {
                        self.status = "server: \(error.localizedDescription)"
                    }
                }
                return
            }
            guard let suitcaseId else { status = "Scan the suitcase first"; return }

            let heights = heightMap(points: solid.points, box: box, planeY: planeY, cell: shapeCellMeters)
            var scanned = ScannedItem(box, heights: heights, cell: shapeCellMeters)
            scanned.suitcaseId = suitcaseId
            scanned.voxels = solid.payload()
            scanned.mesh = coloredMesh.filtered(by: solid).payload()
            scanned.viewCoverage = coverage.fraction
            item = scanned
            status = String(format: "%d voxels, %d triangles, %.0f%% coloured — labelling…",
                             solid.count, scanned.mesh?.triangleCount ?? 0, solid.colourCoverage * 100)
            print(scanned.asciiMap)

            // Photograph the object before the overlay covers it, then ask the server for label + rigidity.
            let crop = screenRect(of: box, in: view)
            view.snapshot(saveToHDR: false) { [weak self] shot in
                guard let self, let shot, let cg = shot.cgImage?.cropping(to: crop.applying(.init(scaleX: shot.scale, y: shot.scale))) else { return }
                Task { @MainActor in
                    do {
                        self.item = try await API.upload(scanned, image: UIImage(cgImage: cg))
                        self.status = String(format: "%d voxels stored, %.0f%% coloured", solid.count, solid.colourCoverage * 100)
                    } catch {
                        self.status = "server: \(error.localizedDescription)"
                    }
                }
            }
            show(box, in: view)
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
    }
}

private extension SIMD4 where Scalar == Float {
    var xyz: SIMD3<Float> { SIMD3(x, y, z) }
}
