import PackingPlan
import SwiftUI

/// The Pack tab: pick a bag, solve it, and look at the answer flat, in 3D, or anchored
/// to the real suitcase in AR.
///
/// The solve and the three views already existed — this is the same `API.plan` call and
/// the same `PlanSheet` the scan panel's cube button presents, given a home of its own so
/// the plan is reachable without standing in front of a suitcase.
struct PackScreen: View {
    /// Raised while the AR overlay owns the camera, so the Scan tab stands its own
    /// ARSession down. iOS runs one at a time: a second `run()` takes the camera from
    /// the first, which then never recovers.
    @Binding var arActive: Bool

    @State private var suitcases: [API.Suitcase] = []
    @State private var selected: String?
    @State private var plan: PackingPlan?
    @State private var notice: String?
    @State private var status = ""
    @State private var packing = false

    var body: some View {
        NavigationStack {
            VStack(spacing: 0) {
                if suitcases.count > 1 { bagPicker }
                if !status.isEmpty { statusRow }
                if let plan {
                    PlanSheet(plan: plan, notice: notice, arActive: $arActive)
                } else {
                    empty
                }
            }
            .navigationTitle("Pack")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    Button { pack() } label: {
                        if packing { ProgressView() } else { Label("Pack", systemImage: "shippingbox.fill") }
                    }
                    .disabled(selected == nil || packing)
                }
            }
        }
        .task { await loadSuitcases() }
    }

    // MARK: - Pieces

    private var bagPicker: some View {
        Picker("Suitcase", selection: $selected) {
            ForEach(suitcases) { bag in Text(bag.displayName).tag(Optional(bag.id)) }
        }
        .pickerStyle(.menu)
        .padding(.horizontal)
    }

    private var statusRow: some View {
        HStack(spacing: 8) {
            Image(systemName: "exclamationmark.triangle.fill").foregroundStyle(.orange)
            Text(status).font(.footnote)
            Spacer()
        }
        .padding(.horizontal)
        .padding(.top, 8)
    }

    private var empty: some View {
        ContentUnavailableView {
            Label(suitcases.isEmpty ? "No suitcase yet" : "Nothing packed yet", systemImage: "cube.transparent")
        } description: {
            Text(suitcases.isEmpty
                 ? "Scan a suitcase on the Scan tab, then scan what goes in it."
                 : "Tap Pack to solve \(selectedBagName) and see where everything goes.")
        }
        .frame(maxHeight: .infinity)
    }

    private var selectedBagName: String {
        suitcases.first { $0.id == selected }?.displayName ?? "this bag"
    }

    // MARK: - Server calls

    private func loadSuitcases() async {
        do {
            suitcases = try await API.suitcases()
            // Newest bag first, which is the one just scanned.
            if selected == nil || !suitcases.contains(where: { $0.id == selected }) { selected = suitcases.first?.id }
            status = ""
        } catch {
            status = "suitcases: \(error.localizedDescription)"
        }
    }

    private func pack() {
        guard let selected else { return }
        packing = true
        Task {
            defer { packing = false }
            do {
                let (solved, unpacked, pendingLabels) = try await API.plan(suitcaseId: selected)
                plan = solved
                notice = nil
                status = unpacked.isEmpty ? "" : "Didn't fit: \(unpacked.map(\.label).joined(separator: ", "))"
                if pendingLabels > 0 {
                    status += status.isEmpty ? "\(pendingLabels) still labelling" : ", \(pendingLabels) still labelling"
                }
            } catch {
                // The mock stands in so the views are still usable, but never silently:
                // PlanSheet's own banner says it is a mock and why.
                let reason = PlanFallback.message(for: error)
                status = "plan: \(reason)"
                if let mock = PlanFallback.mockPlan() {
                    notice = reason
                    plan = mock
                }
            }
        }
    }
}
