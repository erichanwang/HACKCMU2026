import PackingPlan
import PackingPlanUI
import SwiftUI

@main
struct SpikeApp: App {
    var body: some Scene { WindowGroup { ContentView() } }
}

struct ContentView: View {
    @State private var item: ScannedItem?
    @State private var status = "Point at your open suitcase on the floor, then tap it"
    @State private var suitcaseId: String?
    @State private var mode = ScanMode.suitcase
    /// The plan the solver actually produced, shown in the sheet. Nil = no sheet.
    @State private var plan: PackingPlan?

    var body: some View {
        ZStack(alignment: .bottom) {
            ScanView(item: $item, status: $status, suitcaseId: $suitcaseId, mode: mode).ignoresSafeArea()
            VStack(spacing: 8) {
                Picker("mode", selection: $mode) {
                    Text("Suitcase").tag(ScanMode.suitcase)
                    Text("Item").tag(ScanMode.item)
                }
                .pickerStyle(.segmented)
                if mode == .item, suitcaseId == nil {
                    Text("Scan the suitcase first").font(.footnote)
                }
                if let item {
                    Text(String(format: "%.1f × %.1f × %.1f cm", item.width * 100, item.depth * 100, item.height * 100))
                    if item.label != nil { ItemEditor(item: Binding($item)!) }
                }
                Text(status).font(.footnote)
                Button("Pack") { pack() }.disabled(suitcaseId == nil)
            }
                .font(.system(.title2, design: .monospaced))
                .padding()
                .background(.black.opacity(0.6))
                .foregroundStyle(.white)
                .clipShape(RoundedRectangle(cornerRadius: 12))
                .padding(.bottom, 40)
        }
        .sheet(isPresented: Binding(get: { plan != nil }, set: { if !$0 { plan = nil } })) {
            if let plan { PlanDiagramView(plan: plan) }
        }
    }

    private func pack() {
        guard let suitcaseId else { return }
        status = "Packing…"
        Task {
            do {
                plan = try await API.plan(suitcaseId: suitcaseId)
                status = "Packed \(plan?.placements.count ?? 0) items"
            } catch {
                status = "plan: \(error.localizedDescription)"
            }
        }
    }
}

/// Shows the server's label and rigidity guess; edits are sent back as user overrides.
struct ItemEditor: View {
    @Binding var item: ScannedItem
    @State private var label = ""

    var body: some View {
        TextField("label", text: $label)
            .textFieldStyle(.roundedBorder)
            .onAppear { label = item.label ?? "" }
            .onChange(of: item.id) { label = item.label ?? "" }
            .onSubmit { Task { item = try await API.update(id: item.id, label: label, rigidity: nil) } }
        Picker("rigidity", selection: Binding(get: { item.rigidity ?? "rigid" }, set: { r in
            Task { item = try await API.update(id: item.id, label: nil, rigidity: r) }
        })) {
            ForEach(["rigid", "soft", "fragile"], id: \.self) { Text($0) }
        }
        .pickerStyle(.segmented)
        if let d = item.description, !d.isEmpty { Text(d).font(.caption) }
        Text("~\(String(format: "%.1f", item.mass ?? 0)) kg · squeezes \(String(format: "%.1f", item.compressibility ?? 1))×\(item.keepUpright == true ? " · keep upright" : "")").font(.caption2)
        Text("\(item.labelSource ?? "") label · \(item.rigiditySource ?? "") rigidity").font(.caption2)
    }
}
