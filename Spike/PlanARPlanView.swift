import ARKit
import PackingPlan
import RealityKit
import SwiftUI

/// The packing plan drawn inside the real suitcase.
///
/// The plan arrives already loaded — same `PackingPlan` the 2D and 3D views get —
/// and the boxes are the same entities `PlanEntityBuilder` makes for the 3D
/// scene, just parented to the bag anchor instead of a scene anchor. Finding the
/// bag is `PlanFrameController`'s job, shared with the axes view.
struct PlanARPlanView: View {
    @Environment(\.dismiss) private var dismiss

    @StateObject private var controller: PlanFrameController
    /// The shared layer index, so peeling matches the other two views.
    @Binding private var topLayer: Int

    /// Built once, up front: the anchor callback only has to parent it.
    private let content: PlanEntityBuilder.Content
    private let plan: PackingPlan

    /// World tracking is the floor; without it there is nothing to anchor to.
    static var isSupported: Bool { ARWorldTrackingConfiguration.isSupported }

    init(plan: PackingPlan, scans: [String: ScannedItem] = [:], topLayer: Binding<Int>) {
        self.plan = plan
        self._topLayer = topLayer

        let built = PlanEntityBuilder(plan: plan, scans: scans).build(includeLabels: false)
        self.content = built
        _controller = StateObject(
            wrappedValue: PlanFrameController(
                populate: { anchor in anchor.addChild(built.root) },
                wantsOcclusion: true
            )
        )
    }

    var body: some View {
        ZStack(alignment: .bottom) {
            // The camera feed is up from the first frame, so the corner prompts
            // sit over the real bag rather than over a blank screen.
            PlanARPlanContainer(controller: controller).ignoresSafeArea()

            VStack(spacing: 12) {
                Text(controller.status)
                    .font(.system(.headline, design: .monospaced))
                    .multilineTextAlignment(.center)

                if controller.isAnchored {
                    Text(layerCaption)
                        .font(.system(.footnote, design: .monospaced))
                    if layerCount > 1 {
                        Slider(
                            value: sliderBinding,
                            in: 0...Double(layerCount - 1),
                            step: 1
                        )
                    }
                } else {
                    Text("Corner \(min(controller.cornerCount + 1, 3)) of 3")
                        .font(.footnote)
                }

                HStack {
                    Button("Reset", role: .destructive) { controller.reset() }
                        .buttonStyle(.bordered)
                    Button("Done") { dismiss() }
                        .buttonStyle(.borderedProminent)
                }
            }
            .padding()
            .background(.black.opacity(0.6))
            .foregroundStyle(.white)
            .clipShape(RoundedRectangle(cornerRadius: 12))
            .padding(.bottom, 40)
        }
        .onAppear { content.show(upTo: clamped(topLayer)) }
        .onChange(of: topLayer) { _, value in content.show(upTo: clamped(value)) }
        .onDisappear { controller.pause() }
    }

    private var layerCount: Int { max(content.entitiesByLayer.count, 1) }

    private func clamped(_ index: Int) -> Int {
        min(max(index, 0), layerCount - 1)
    }

    private var sliderBinding: Binding<Double> {
        Binding(
            get: { Double(clamped(topLayer)) },
            set: { topLayer = clamped(Int($0)) }
        )
    }

    private var layerCaption: String {
        let shown = clamped(topLayer) + 1
        let items = plan.layers().prefix(shown).reduce(0) { $0 + $1.placements.count }
        return "Layers 1–\(shown) of \(layerCount) · \(items) items"
    }
}

private struct PlanARPlanContainer: UIViewRepresentable {
    let controller: PlanFrameController

    func makeUIView(context: Context) -> ARView {
        controller.start()
        return controller.arView
    }

    func updateUIView(_ uiView: ARView, context: Context) {}
}
