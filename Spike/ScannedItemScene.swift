import PackingPlan
import SwiftUI

/// One scanned item on its own — no suitcase, no other placements — so the
/// heightmap mesh can be inspected before it goes into a plan.
///
/// It reuses `PlanSceneView` by wrapping the scan in a one-item plan whose
/// container is exactly the grid's own extent. That keeps the scale factor at 1,
/// so what you orbit around is the shape the scanner recorded rather than a
/// version stretched to fit somebody's packed box.
struct ScannedItemScene: View {
    let item: ScannedItem

    private static let soloItemID = "scanned-item"

    var body: some View {
        PlanSceneView(plan: Self.soloPlan(for: item), scans: [Self.soloItemID: item])
    }

    static func soloPlan(for item: ScannedItem) -> PackingPlan {
        let rows = Float(item.heights.count)
        let columns = Float(item.heights.first?.count ?? 0)
        let tallest = item.heights.flatMap { $0 }.max() ?? item.cellSize

        // The grid's own extent, not the declared bounding box: the two can differ
        // by a fraction of a cell and we want no stretch at all here.
        let extent = Vector3(
            max(rows * item.cellSize, item.cellSize),
            max(tallest, item.cellSize),
            max(columns * item.cellSize, item.cellSize)
        )

        return PackingPlan(
            version: 1,
            units: .meters,
            container: Container(
                id: "solo",
                label: item.label ?? "Scanned item",
                dimensions: extent,
                zones: [Zone(id: "solo", label: "Item", origin: .zero, size: extent)]
            ),
            placements: [
                Placement(
                    step: 1,
                    itemID: soloItemID,
                    label: item.label ?? "Scanned item",
                    zone: "solo",
                    position: .zero,
                    size: extent,
                    rotation: .xyz,
                    note: ""
                )
            ]
        )
    }
}

/// Entry point from the app's root: the scanned item bundled in packing-core.
struct ScannedItemScreen: View {
    var body: some View {
        if let item = Self.bundledItem() {
            ScannedItemScene(item: item)
        } else {
            ContentUnavailableView(
                "No scan",
                systemImage: "cube.transparent",
                description: Text("The bundled scanned item could not be loaded.")
            )
        }
    }

    static func bundledItem() -> ScannedItem? {
        guard let data = try? ScannedItemFixture.data() else { return nil }
        return try? JSONDecoder().decode(ScannedItem.self, from: data)
    }
}

#Preview("Scanned item on its own") {
    NavigationStack {
        ScannedItemScreen()
    }
}
