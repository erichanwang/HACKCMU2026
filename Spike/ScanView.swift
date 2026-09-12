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

/// A horizontal plane this big (m²) counts as the floor for the purpose of advancing
/// the scan prompts — roughly a 30cm square.
private let floorPlaneAreaMeters: Float = 0.09

/// ponytail: below this interior depth a "suitcase" is a closed lid or a tap on the
/// floor, not a bag worth packing. A fixed threshold, not a lid detector; if someone
/// really packs a 6cm case, this is the number to revisit.
private let openSuitcaseInteriorMeters: Float = 0.07

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
        /// One tap at a time: set while a scan's geometry is off on a background thread.
        var scanning = false
        var planOverlay: AnchorEntity?
        /// The real ARKit anchor `planOverlay` tracks (see `showPlan`). Kept so a later rebuild
        /// can remove it from the session instead of leaking a stale anchor into every frame.
        var planARAnchor: ARAnchor?
        /// Child of `planOverlay` that carries only the live planeY correction (see `refreshPlaneY`).
        /// A plain `Entity`, not itself ARKit-tracked, so setting its position every frame can't
        /// race against RealityKit's own sync of `planOverlay`'s transform from `planARAnchor`.
        var planYOffset: Entity?
        var shownPlan: PackingPlan?
        /// One axis fit per suitcase-mode tap. Averaged (see PlanAnchor.averageAxis) so a single
        /// noisy `minAreaRect` fit doesn't set the bag's rotation for the whole session.
        var axisSamples: [SIMD3<Float>] = []

        init(item: Binding<ScannedItem?>, status: Binding<String>, suitcaseId: Binding<String?>, mode: ScanMode) {
            _item = item; _status = status; _suitcaseId = suitcaseId; self.mode = mode
        }

        /// Every string `guide` writes. A prompt may only ever replace another prompt:
        /// a measurement, an error or a label must never be wiped by the next frame's
        /// advice.
        static let prompts: Set<String> = [
            "Move the phone slowly so it can see the room",
            "Point the camera at the floor near your suitcase",
            "Open your suitcase on the floor, then tap inside it",
            "Tap the bag again to steady it, or switch to Item",
            "Switch to Suitcase and scan the bag first",
            "Point at an item next to the bag, then tap it",
        ]

        /// One instruction at a time, advancing as the session actually learns the room:
        /// find the room, find the floor, then do the thing this mode is for.
        func guide(_ frame: ARFrame) {
            guard !scanning, item == nil else { return }          // a result is on screen
            guard status.isEmpty || Coordinator.prompts.contains(status) else { return }
            let next: String
            switch frame.camera.trackingState {
            case .normal:
                if !seesFloor(frame) {
                    next = "Point the camera at the floor near your suitcase"
                } else if mode == .suitcase {
                    next = suitcaseId == nil
                        ? "Open your suitcase on the floor, then tap inside it"
                        : "Tap the bag again to steady it, or switch to Item"
                } else {
                    next = suitcaseId == nil
                        ? "Switch to Suitcase and scan the bag first"
                        : "Point at an item next to the bag, then tap it"
                }
            default:
                next = "Move the phone slowly so it can see the room"
            }
            if status != next { status = next }
        }

        /// A horizontal plane big enough to be a floor rather than a speck of noise.
        private func seesFloor(_ frame: ARFrame) -> Bool {
            frame.anchors.contains { anchor in
                guard let plane = anchor as? ARPlaneAnchor, plane.alignment == .horizontal else { return false }
                return plane.planeExtent.width * plane.planeExtent.height >= floorPlaneAreaMeters
            }
        }

        @objc func tap(_ g: UITapGestureRecognizer) {
            guard !scanning else { status = "Still measuring the last tap"; return }
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

            // Mesh surface above the table near the tap. The vertex/index buffers belong to this
            // frame, so the triangles get copied out here, on the main thread; densifying and
            // fitting them is the slow part and runs off it below.
            var tris: [(SIMD3<Float>, SIMD3<Float>, SIMD3<Float>)] = []
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
                    tris.append((a, b, c))
                }
            }

            scanning = true
            status = "Measuring…"
            let mode = self.mode
            Task { @MainActor [weak self, tris] in
                // The hop. Only value types go across: the copied triangles and a few Floats. Not
                // `self` (Coordinator is not Sendable), not `view`, not the ARKit buffers — their
                // frame is gone by now. Only plain geometry comes back, and everything that touches
                // ARKit/RealityKit/UIKit or the SwiftUI bindings stays on this side, on the main actor.
                let (ptCount, cluster, fitted, heights) = await Task.detached(priority: .userInitiated) {
                    () -> (Int, [SIMD3<Float>], BoxFit?, [[Float]]) in
                    var pts: [SIMD3<Float>] = []
                    for (a, b, c) in tris {
                        pts += densify(a, b, c, spacing: shapeCellMeters / 2).filter { $0.y - planeY > minHeightMeters }
                    }
                    let cluster = connectedCluster(pts, seed: seed, cell: clusterCellMeters)
                    // Only a bag has a lid to trim away; trimming an item truncates it (Geometry.swift).
                    guard let box = fitBox(points: cluster, planeY: planeY, padding: paddingMeters,
                                           trimAboveRim: mode == .suitcase) else {
                        return (pts.count, cluster, nil, [])
                    }
                    // Suitcase mode never looks at the heightmap, so don't build one.
                    return (pts.count, cluster, box, mode == .suitcase
                        ? [] : heightMap(points: cluster, box: box, planeY: planeY, cell: shapeCellMeters))
                }.value
                guard let self else { return }
                defer { self.scanning = false }
                guard let view = self.view else { return }

                guard let box = fitted else {
                    self.status = "Nothing above the table here (\(ptCount) pts)"; return
                }
                guard isUsableBox(width: box.width, height: box.height, depth: box.depth, maxDimension: maxItemDimensionMeters) else {
                    self.status = "That doesn't look like one item — check for a nearby wall or the floor, then rescan"; return
                }
                // Suitcase mode: the fitted box is the bag's outer shell; the interior is a wall thinner.
                // No heightmap, no photo, no label.
                if mode == .suitcase {
                    // Average this tap's axis fit in with earlier ones — one fit alone is noisy
                    // enough to matter (see tests/swift/drift); a re-tap steadies it further.
                    self.axisSamples.append(box.axis)
                    let steadied = BoxFit(width: box.width, depth: box.depth, height: box.height,
                                          center: box.center, axis: averageAxis(self.axisSamples))
                    let interior = interiorBox(steadied, wall: suitcaseWallMeters,
                                               wallHeight: suitcaseFloorWallMeters,
                                               wallDepth: suitcaseHandleWallMeters)
                    guard interior.height >= openSuitcaseInteriorMeters else {
                        self.status = "That looks closed or flat — open the lid and tap inside the bag"
                        return
                    }
                    self.suitcase = (interior, planeY + suitcaseWallMeters)
                    self.show(interior, in: view)
                    self.status = "Creating suitcase… (tap the bag again to steady it, or switch to Item)"
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
                guard let suitcaseId = self.suitcaseId else { self.status = "Scan the suitcase first"; return }

                guard isUsableHeightMap(heights) else {
                    self.status = "Nothing usable captured on top — move closer and rescan"; return
                }
                var scanned = ScannedItem(box, heights: heights, cell: shapeCellMeters)
                scanned.suitcaseId = suitcaseId
                self.item = scanned
                self.status = "\(cluster.count) pts, \(heights.count)×\(heights[0].count) cells — labelling…"
                print(scanned.asciiMap)

                // Photograph the object before the overlay covers it, then ask the server for label + rigidity.
                guard let crop = self.screenRect(of: box, in: view) else {
                    self.status = "Object out of view for the photo — item saved without a label"
                    self.show(box, in: view)
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
                            if uploaded.labelStatus == "pending" {
                                self.status = "\(cluster.count) pts, \(heights.count)×\(heights[0].count) cells"
                                await self.pollLabel(id: uploaded.id)
                            } else {
                                // Terminal already: "unidentified" and "failed" never poll, and
                                // before this they fell through showing "labelled unknown".
                                self.status = labelStatusMessage(labelStatus: uploaded.labelStatus,
                                                                 label: uploaded.label,
                                                                 identifyHint: uploaded.identifyHint)
                            }
                        } catch {
                            self.status = "server: \(error.localizedDescription)"
                        }
                    }
                }
                self.show(box, in: view)
            }
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
                status = labelStatusMessage(labelStatus: fresh.labelStatus, label: fresh.label,
                                            identifyHint: fresh.identifyHint)
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
            planYOffset = nil
            // Drop the old tracked anchor from the session too, or a rebuild leaks a stale one
            // into every subsequent frame update.
            if let old = planARAnchor { view.session.remove(anchor: old) }
            planARAnchor = nil
            guard let plan, let suitcase else { return }
            // A real ARAnchor, not a frozen `AnchorEntity(world:)` snapshot: ARKit keeps correcting
            // any anchor's `transform` as it refines its world-tracking pose graph (the same
            // mechanism `currentPlaneY` already leans on for ARPlaneAnchor specifically — see
            // https://developer.apple.com/documentation/arkit/aranchor,
            // https://developer.apple.com/documentation/arkit/arsessiondelegate/session(_:didupdate:)-2i91y),
            // so parenting the overlay to it corrects horizontal (x/z) origin drift and axis/yaw
            // drift accumulated between the suitcase tap and now — see tests/swift/drift Section 4d.
            // It does NOT correct the one-tap axis/plane/dimension *fit* noise baked into `suitcase`
            // before this anchor is ever created (averageAxis already covers axis fit noise
            // separately by re-tapping). And this correction is NOT as trustworthy as the live plane
            // re-fit below: the table plane is re-observed against live depth data every frame, so a
            // fresh read of it is close to ground truth regardless of how much the world has
            // drifted; a plain ARAnchor at the bag's origin has no equivalent — nothing re-observes
            // "the bag" the way ARKit re-observes the table — so it only gets whatever *partial*
            // correction ARKit's generic pose-graph revision happens to apply, lagging the true pose
            // by however long that revision takes. Modelled as a swept correction fraction, not an
            // assumed 100%, in tests/swift/drift Section 4d; the real fraction needs a device.
            let bagFrame = PlanAnchor(interior: suitcase.interior, planeY: suitcase.planeY)
            let arTransform = simd_float4x4(columns: (
                SIMD4<Float>(bagFrame.axis, 0),
                SIMD4<Float>(0, 1, 0, 0),
                SIMD4<Float>(bagFrame.perp, 0),
                SIMD4<Float>(bagFrame.origin, 1)))
            let arAnchor = ARAnchor(transform: arTransform)
            view.session.add(anchor: arAnchor)
            let anchor = AnchorEntity(anchor: arAnchor)
            // Carries only the live planeY correction (see `refreshPlaneY`) as an offset on top of
            // whatever y ARKit reports for `arAnchor` — a plain Entity, so it's not fighting
            // RealityKit's own sync of `anchor`'s transform from the tracked ARAnchor.
            let yOffset = Entity()
            anchor.addChild(yOffset)
            for p in plan.placements {
                let size = SIMD3<Float>(p.size.x, p.size.y, p.size.z)
                let color = Self.stepColors[(p.step - 1) % Self.stepColors.count]
                let mesh = MeshResource.generateBox(width: size.x, height: size.y, depth: size.z)
                let entity = ModelEntity(
                    mesh: mesh,
                    materials: [SimpleMaterial(color: color.withAlphaComponent(0.4), isMetallic: false)])
                // No rotation here — `arAnchor`'s own transform already carries the bag's axis.
                entity.position = SIMD3<Float>(
                    p.position.x + size.x / 2, p.position.y + size.y / 2, p.position.z + size.z / 2)
                yOffset.addChild(entity)
            }
            view.scene.addAnchor(anchor)
            planOverlay = anchor
            planARAnchor = arAnchor
            planYOffset = yOffset
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
        /// called every AR frame via `ARSessionDelegate`. Only `planYOffset`'s local y moves
        /// (cheap); the boxes underneath keep the bag-frame coordinates `showPlan` built, and
        /// `planOverlay`'s own x/z + rotation are left for ARKit to correct via `planARAnchor`.
        func refreshPlaneY() {
            guard let planYOffset, let suitcase else { return }
            planYOffset.position.y = (currentPlaneY() ?? suitcase.planeY) - suitcase.planeY
        }
    }
}

extension ScanView.Coordinator: ARSessionDelegate {
    /// `nonisolated` + `assumeIsolated`, not a plain `@MainActor` method: `ARSessionDelegate`'s
    /// requirement is nonisolated, and under Swift 6 a main-actor method cannot satisfy it.
    /// ARKit calls the delegate on the main queue, so the assumption holds — it traps loudly
    /// if that ever stops being true, which beats a data race that doesn't.
    nonisolated func session(_ session: ARSession, didUpdate frame: ARFrame) {
        MainActor.assumeIsolated {
            refreshPlaneY()
            guide(frame)
        }
    }
}
