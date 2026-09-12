import ARKit
import CoreImage
import RealityKit
import SwiftUI

/// Added to every dimension. 0 = report the true size; the packing solver applies its own tolerance.
let paddingMeters: Float = 0.0
/// Points closer to the table than this are treated as the table itself.
let minHeightMeters: Float = 0.01
/// Max horizontal distance from the tap to consider points.
let searchRadiusMeters: Float = 0.5
/// Grid cell for separating the tapped object from its neighbours.
let clusterCellMeters: Float = 0.02
/// Resolution of the captured shape (heightmap cell).
let shapeCellMeters: Float = 0.01
/// Fewer depth points than this on the object means it's too small or too far; refuse rather than save junk.
let minClusterPoints = 400
/// Depth pixels below this ARConfidenceLevel (0 low, 1 medium, 2 high) are ignored — that's where flying pixels live.
let minDepthConfidence: UInt8 = 2
/// Fraction of outermost points ignored on each side when fitting the box.
let trimFraction: Float = 0.01
/// A closed suitcase is scanned from outside. Usable interior per axis ≈ exterior × this: shell, wheel wells and
/// handle housing eat roughly 8% per axis (0.92³ ≈ 78% of the exterior volume). Tune per bag.
let suitcaseInteriorScale: Float = 0.92

enum ScanMode { case item, suitcase }

/// What the item scanner is doing, so the UI can say so.
enum ScanPhase {
    case idle(String)                           // hint to show
    case measured(ScannedItem)                  // dimensions known, identifying the object
    case review(ScannedItem, rescanned: Bool)   // guess shown, nothing stored yet — user confirms or edits
    case saving(ScannedItem)
    case saved(ScannedItem, rescanned: Bool)
    case failed(ScannedItem, String)

    var key: String {
        switch self {
        case .idle(let h): return "idle-\(h)"
        case .measured(let i): return "measured-\(i.id)"
        case .review(let i, _): return "review-\(i.id)"
        case .saving(let i): return "saving-\(i.id)"
        case .saved(let i, _): return "saved-\(i.id)-\(i.label ?? "")-\(i.rigidity ?? "")"
        case .failed(let i, _): return "failed-\(i.id)"
        }
    }
}

struct ScanView: UIViewRepresentable {
    var mode: ScanMode = .item
    var suitcaseId = ""
    /// When set, the next tap re-measures this existing item instead of creating a new one.
    var rescanId: String? = nil
    var phase: Binding<ScanPhase> = .constant(.idle(""))
    @Binding var status: String
    /// Interior [width, height, depth] of the last scanned suitcase (suitcase mode only).
    var suitcaseDims: Binding<[Float]?> = .constant(nil)

    func makeUIView(context: Context) -> ARView {
        let view = ARView(frame: .zero)
        let config = ARWorldTrackingConfiguration()
        config.planeDetection = .horizontal
        config.sceneReconstruction = .mesh  // display only; measurement uses the depth map
        config.frameSemantics = ARWorldTrackingConfiguration.supportsFrameSemantics(.smoothedSceneDepth) ? .smoothedSceneDepth : .sceneDepth
        view.session.run(config)
        view.debugOptions = [.showSceneUnderstanding]
        view.addGestureRecognizer(UITapGestureRecognizer(target: context.coordinator, action: #selector(Coordinator.tap)))
        context.coordinator.view = view
        updateUIView(view, context: context)
        return view
    }

    func updateUIView(_ uiView: ARView, context: Context) {
        context.coordinator.mode = mode
        context.coordinator.suitcaseId = suitcaseId
        context.coordinator.rescanId = rescanId
        context.coordinator.phase = phase
        context.coordinator.suitcaseDims = suitcaseDims
    }
    func makeCoordinator() -> Coordinator { Coordinator(status: $status) }

    final class Coordinator: NSObject {
        @Binding var status: String
        weak var view: ARView?
        var overlay: AnchorEntity?
        var mode = ScanMode.item
        var suitcaseId = ""
        var rescanId: String?
        var phase: Binding<ScanPhase> = .constant(.idle(""))
        var suitcaseDims: Binding<[Float]?> = .constant(nil)

        init(status: Binding<String>) { _status = status }

        /// A problem with the tap itself: shown as the hint in item mode, as status in suitcase mode.
        func hint(_ text: String) {
            if mode == .item { phase.wrappedValue = .idle(text) } else { status = text }
        }

        @objc func tap(_ g: UITapGestureRecognizer) {
            guard let view, let frame = view.session.currentFrame else { return }
            let point = g.location(in: view)

            // Seed: whatever surface the ray hits (object top, most likely).
            guard let hit = view.raycast(from: point, allowing: .estimatedPlane, alignment: .any).first else {
                hint("No surface under tap"); return
            }
            let seed = SIMD3<Float>(hit.worldTransform.columns.3.x, hit.worldTransform.columns.3.y, hit.worldTransform.columns.3.z)

            guard let depth = frame.smoothedSceneDepth ?? frame.sceneDepth else { hint("No depth data yet — hold still a moment"); return }
            let radius = mode == .suitcase ? searchRadiusMeters * 2 : searchRadiusMeters
            let maxHeight: Float = mode == .suitcase ? 1.0 : 0.6
            let all = depthPoints(frame: frame, depth: depth, near: seed, radius: radius)

            // The surface the object sits on: from the depth points (a lid or box top can be an ARKit plane
            // too, so plane anchors are only the fallback when the surface around the object isn't in view).
            let planes = frame.anchors.compactMap { $0 as? ARPlaneAnchor }
                .filter { $0.alignment == .horizontal }.map { $0.transform.columns.3.y }
                .filter { seed.y - $0 >= 0.015 && seed.y - $0 <= maxHeight }
            guard let planeY = supportHeight(ys: all.map(\.y), seedY: seed.y, maxBelow: maxHeight) ?? planes.max() else {
                if mode == .item, all.contains(where: { abs($0.y - seed.y) < 0.015 }) {
                    hint("Too thin to measure — LiDAR needs about 2 cm. Add it by hand from the items list."); return
                }
                hint("Can't see the surface it's on — step back so the floor/table around it is in view"); return
            }
            let pts = all.filter { $0.y - planeY > minHeightMeters }

            let cluster = connectedCluster(pts, seed: seed, cell: clusterCellMeters)
            if mode == .item, cluster.count < minClusterPoints {
                hint("Not enough detail (\(cluster.count) pts) — move closer, pan slowly over it, tap again"); return
            }
            guard let box = fitBox(points: cluster, planeY: planeY, padding: paddingMeters, trim: trimFraction) else {
                hint("Nothing above the surface here — tap the object itself"); return
            }
            if mode == .suitcase {
                let k = suitcaseInteriorScale
                suitcaseDims.wrappedValue = [box.width * k, box.height * k, box.depth * k]
                status = String(format: "outside %.0f × %.0f × %.0f cm → usable %.0f × %.0f × %.0f cm",
                                box.width * 100, box.height * 100, box.depth * 100, box.width * k * 100, box.height * k * 100, box.depth * k * 100)
                show(box, in: view)
                return
            }
            let heights = heightMap(points: cluster, box: box, planeY: planeY, cell: shapeCellMeters)
            var scanned = ScannedItem(box, heights: heights, cell: shapeCellMeters, suitcaseId: suitcaseId)
            let rescanned = rescanId != nil
            if let rescanId { scanned.id = rescanId }
            phase.wrappedValue = .measured(scanned)
            print(scanned.asciiMap)
            print(String(data: try! JSONEncoder().encode(scanned), encoding: .utf8)!)

            // Crop the object out of the raw camera frame (no mesh overlay) and ask the server for label + rigidity.
            var crop = screenRect(of: box, in: view)
            if crop.width < 100 || crop.height < 100 { crop = view.bounds }  // vision models reject tiny images
            guard let photo = cameraCrop(frame, viewRect: crop, viewSize: view.bounds.size) else {
                phase.wrappedValue = .failed(scanned, "couldn't capture a photo"); return
            }
            Task { @MainActor in
                var item = scanned
                do {
                    let g = try await API.label(photo)
                    item.label = g.label; item.labelSource = "auto"; item.description = g.description
                    item.rigidity = g.rigidity; item.rigiditySource = "auto"
                    item.compressibility = g.compressibility; item.compressibilitySource = "auto"
                    item.mass = g.mass; item.keepUpright = g.keepUpright
                } catch {
                    item.label = ""; item.rigidity = "rigid"  // user fills it in
                    item.description = "Couldn't identify: \(error.localizedDescription)"
                }
                self.phase.wrappedValue = .review(item, rescanned: rescanned)
            }
            show(box, in: view)
        }

        /// World-space points from the depth map: confident pixels within `radius` (horizontally) of the seed.
        func depthPoints(frame: ARFrame, depth: ARDepthData, near seed: SIMD3<Float>, radius: Float) -> [SIMD3<Float>] {
            let map = depth.depthMap
            CVPixelBufferLockBaseAddress(map, .readOnly)
            defer { CVPixelBufferUnlockBaseAddress(map, .readOnly) }
            let w = CVPixelBufferGetWidth(map), h = CVPixelBufferGetHeight(map), rowBytes = CVPixelBufferGetBytesPerRow(map)
            guard let base = CVPixelBufferGetBaseAddress(map) else { return [] }
            var confBase: UnsafeMutableRawPointer?, confRow = 0
            if let c = depth.confidenceMap {
                CVPixelBufferLockBaseAddress(c, .readOnly)
                confBase = CVPixelBufferGetBaseAddress(c); confRow = CVPixelBufferGetBytesPerRow(c)
            }
            defer { if let c = depth.confidenceMap { CVPixelBufferUnlockBaseAddress(c, .readOnly) } }

            // Intrinsics are for the full camera image; scale them to the depth map.
            let K = frame.camera.intrinsics, res = frame.camera.imageResolution
            let sx = Float(w) / Float(res.width), sy = Float(h) / Float(res.height)
            let fx = K[0][0] * sx, fy = K[1][1] * sy, cx = K[2][0] * sx, cy = K[2][1] * sy
            let cam = frame.camera.transform, r2 = radius * radius
            var out: [SIMD3<Float>] = []
            for v in 0..<h {
                let row = base.advanced(by: v * rowBytes).assumingMemoryBound(to: Float32.self)
                for u in 0..<w {
                    if let confBase, confBase.advanced(by: v * confRow + u).assumingMemoryBound(to: UInt8.self).pointee < minDepthConfidence { continue }
                    let d = row[u]
                    guard d > 0, d.isFinite else { continue }
                    let local = SIMD4<Float>((Float(u) - cx) * d / fx, -(Float(v) - cy) * d / fy, -d, 1)
                    let p4 = cam * local
                    let p = SIMD3<Float>(p4.x, p4.y, p4.z)
                    let dx = p.x - seed.x, dz = p.z - seed.z
                    if dx * dx + dz * dz < r2 { out.append(p) }
                }
            }
            return out
        }

        /// The camera image under a screen rectangle. The view shows the camera aspect-filled, so
        /// view points map to camera pixels by one scale and offset once the frame is rotated upright.
        func cameraCrop(_ frame: ARFrame, viewRect: CGRect, viewSize: CGSize) -> UIImage? {
            let image = CIImage(cvPixelBuffer: frame.capturedImage).oriented(.right)  // portrait
            let iw = image.extent.width, ih = image.extent.height
            let scale = max(viewSize.width / iw, viewSize.height / ih)
            let ox = (viewSize.width - iw * scale) / 2, oy = (viewSize.height - ih * scale) / 2
            var r = CGRect(x: (viewRect.minX - ox) / scale, y: (viewRect.minY - oy) / scale,
                           width: viewRect.width / scale, height: viewRect.height / scale)
            r.origin.y = ih - r.maxY  // Core Image's origin is bottom-left
            r = r.intersection(image.extent)
            guard !r.isEmpty, let cg = CIContext().createCGImage(image.cropped(to: r), from: r) else { return nil }
            return UIImage(cgImage: cg)
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
