import Foundation
import PackingPlan

// MARK: - Pure model (no RealityKit/ARKit/SwiftUI import — compiles and is tested on Linux)

/// How a placement should read at the current step: already in the bag, the one to place next,
/// or still to come.
enum PlacementRole: Equatable {
    case placed
    case current
    case upcoming
}

/// Drives the AR plan overlay: which placement is "next", how each one should look, and a stable
/// colour per item. Everything here is pure data — `Spike/PlanOverlay.swift`'s RealityKit section
/// is the only part that turns this into entities.
struct PlanOverlayState {
    let plan: PackingPlan
    /// Number of placements already put in the bag, 0...count. 0 = nothing placed yet (the first
    /// placement is `current`); `count` = done (no `current`).
    private(set) var step: Int

    private let ordered: [Placement]
    /// itemID -> palette index. Built once from packing order, so it's stable across rebuilds of
    /// the same plan and always differs between consecutive steps (see `paletteSize`).
    private let colorIndexByItem: [String: Int]

    /// Any distinct colour count >= 2 guarantees adjacent placements differ, since their indices
    /// are consecutive integers mod this count.
    static let paletteSize = 5

    init(plan: PackingPlan, step: Int = 0) {
        self.plan = plan
        let ordered = plan.orderedPlacements
        self.ordered = ordered
        var idx: [String: Int] = [:]
        for (i, p) in ordered.enumerated() { idx[p.itemID] = i % Self.paletteSize }
        self.colorIndexByItem = idx
        self.step = max(0, min(step, ordered.count))
    }

    var count: Int { ordered.count }
    var isComplete: Bool { step >= count }
    var current: Placement? { step < count ? ordered[step] : nil }

    var stepDescription: String {
        guard count > 0 else { return "nothing to pack" }
        return isComplete ? "packed \(count) of \(count)" : "step \(step + 1) of \(count)"
    }

    /// Every placement with the role it should be drawn in at the current step, in packing order.
    /// `upcoming` placements are included (rather than filtered here) so a caller can still decide
    /// to render them faintly; the RealityKit builder below chooses to skip them.
    var placementsWithRole: [(placement: Placement, role: PlacementRole)] {
        ordered.enumerated().map { i, p in
            let role: PlacementRole = i < step ? .placed : (i == step ? .current : .upcoming)
            return (p, role)
        }
    }

    func colorIndex(for placement: Placement) -> Int { colorIndexByItem[placement.itemID] ?? 0 }

    mutating func advance() { step = min(step + 1, count) }
    mutating func retreat() { step = max(step - 1, 0) }
}

/// One item the solver could not fit. Mirrors `API.UnpackedItem`'s two fields without depending on
/// that type (which lives behind `#if !PACKAR_TEST_ONLY` in Spike/API.swift) — keeps this file
/// buildable standalone. The call site maps `API.UnpackedItem` -> `UnpackedPlacement`.
struct UnpackedPlacement: Equatable {
    let itemID: String
    let label: String
}

/// `API.UnpackedItem` values have no position, so they can never be drawn inside the bag. This
/// surfaces them instead as a count + list, for a label/banner near the AR view.
struct UnpackedSummary {
    let items: [UnpackedPlacement]

    var count: Int { items.count }
    var isEmpty: Bool { items.isEmpty }

    /// e.g. "2 items didn't fit: Umbrella, Boots". Empty string when everything fit.
    var summaryText: String {
        guard !items.isEmpty else { return "" }
        let names = items.map(\.label).joined(separator: ", ")
        return "\(items.count) item\(items.count == 1 ? "" : "s") didn't fit: \(names)"
    }
}

// MARK: - RealityKit entity construction

#if canImport(RealityKit)
import RealityKit
import UIKit
#if canImport(simd)
import simd
#endif

/// Builds the AR entities for one `PlanOverlayState`. Legibility choice: the current item is drawn
/// solid so it reads as "put this one in now"; already-placed items stay as faint translucent
/// boxes for context; upcoming items are not created at all — stacking every future box as another
/// translucent layer is exactly the "pile of grey rectangles" mud this file exists to avoid.
enum PlanOverlayBuilder {
    static let palette: [UIColor] = [.systemBlue, .systemOrange, .systemPurple, .systemTeal, .systemPink]

    static func entities(for state: PlanOverlayState, anchor: PlanAnchor, orientation: simd_quatf) -> [ModelEntity] {
        state.placementsWithRole.compactMap { placement, role -> ModelEntity? in
            guard role != .upcoming else { return nil }
            let size = SIMD3<Float>(placement.size.x, placement.size.y, placement.size.z)
            let color = palette[state.colorIndex(for: placement) % palette.count]
            let alpha: CGFloat = role == .current ? 0.85 : 0.15
            let mesh = MeshResource.generateBox(width: size.x, height: size.y, depth: size.z)
            let box = ModelEntity(mesh: mesh, materials: [SimpleMaterial(color: color.withAlphaComponent(alpha), isMetallic: false)])
            box.name = placement.itemID
            box.orientation = orientation
            box.position = anchor.worldCenter(
                position: SIMD3<Float>(placement.position.x, placement.position.y, placement.position.z), size: size)

            // Label floats just above the box so it reads even when the box itself is faint.
            let textMesh = MeshResource.generateText(placement.label, extrusionDepth: 0.001, font: .systemFont(ofSize: 0.02))
            let text = ModelEntity(mesh: textMesh, materials: [SimpleMaterial(color: .white, isMetallic: false)])
            text.position = SIMD3<Float>(-size.x / 2, size.y / 2 + 0.01, 0)
            box.addChild(text)
            return box
        }
    }
}
#endif
