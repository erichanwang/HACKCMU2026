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
    private let issues: [GeometryIssue]
    private let stability: [GeometryIssue]

    @State private var mode: Mode

    /// - Parameter initialMode: which rendering to open on. Layers first: it is
    ///   the fallback path, and it is the one that works at any size.
    public init(plan: PackingPlan, initialMode: Mode = .layers) {
        self.plan = plan
        self.issues = plan.geometryIssues()
        self.stability = plan.stabilityIssues()
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

            // Only over the 3D scene. `PlanDiagramView` raises these itself, so
            // showing them here too would double every banner in Layers mode;
            // `PlanSceneView` reports nothing, so without this a plan that is
            // wrong or tips over looks perfectly fine in 3D.
            if mode == .scene {
                VStack(spacing: 8) {
                    if !issues.isEmpty {
                        PlanIssueBanner(kind: .geometry, issues: issues)
                    }
                    if !stability.isEmpty {
                        PlanIssueBanner(kind: .stability, issues: stability)
                    }
                }
                .padding([.horizontal, .top])
            }

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

/// The banner over the 3D scene, which has no issue reporting of its own.
/// Switching to Layers must show it once, not twice.
#Preview("Unstable plan — 3D banner") {
    PlanViewer(plan: unstableDemoPlan(), initialMode: .scene)
}
#endif  // canImport(SwiftUI) -- SwiftUI is Apple-only; the view vanishes on Linux so `swift test` can run
