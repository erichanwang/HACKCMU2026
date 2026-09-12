#if canImport(SwiftUI)
import PackingPlan
import SwiftUI

/// The one view an app has to present: the flat layer diagram and the 3D scene
/// under a single picker.
///
/// It chooses between the two and does nothing else — neither child's API
/// changes, and neither knows this exists. Both render the same plan, so
/// switching is free and the picker is the whole of the state.
public struct PlanViewer: View {
    /// Which rendering of the plan is on screen.
    public enum Mode: String, CaseIterable, Identifiable, Sendable {
        /// `PlanDiagramView` — top-down, one horizontal layer at a time.
        case layers = "Layers"

        /// `PlanSceneView` — orbitable box, one packing step at a time.
        case scene = "3D"

        public var id: String { rawValue }
    }

    private let plan: PackingPlan

    @State private var mode: Mode

    /// - Parameter initialMode: which rendering to open on. Layers first: it is
    ///   the fallback path, and it is the one that works at any size.
    public init(plan: PackingPlan, initialMode: Mode = .layers) {
        self.plan = plan
        self._mode = State(initialValue: initialMode)
    }

    public var body: some View {
        VStack(spacing: 0) {
            Picker("View", selection: $mode) {
                ForEach(Mode.allCases) { option in
                    Text(option.rawValue).tag(option)
                }
            }
            .pickerStyle(.segmented)
            .padding([.horizontal, .top])

            switch mode {
            case .layers:
                PlanDiagramView(plan: plan)
            case .scene:
                PlanSceneView(plan: plan)
            }
        }
    }
}

// MARK: - Preview

#Preview("Demo carry-on — layers") {
    if let plan = try? PlanLoader.mockPlan() {
        PlanViewer(plan: plan)
    } else {
        Text("Could not load the bundled mock plan.")
    }
}

#Preview("Demo carry-on — 3D") {
    if let plan = try? PlanLoader.mockPlan() {
        PlanViewer(plan: plan, initialMode: .scene)
    } else {
        Text("Could not load the bundled mock plan.")
    }
}
#endif  // canImport(SwiftUI) -- SwiftUI is Apple-only; the view vanishes on Linux so `swift test` can run
