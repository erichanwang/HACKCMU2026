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
    /// The plan the solver actually produced. Outlives the sheet: closing the diagram is how the
    /// user gets back to the AR overlay, so dismissing it must not throw the plan away.
    @State private var plan: PackingPlan?
    @State private var showingDiagram = false
    /// A POST /plan is in flight; a second one would race the first and last write would win.
    @State private var packing = false
    /// Where the server is and how to authenticate; typed on the phone, kept across launches.
    @AppStorage("serverURL") private var serverURL = API.defaultBase
    @AppStorage("authToken") private var authToken = ""
    @State private var showSettings = false

    var body: some View {
        ZStack(alignment: .bottom) {
            ScanView(item: $item, status: $status, suitcaseId: $suitcaseId, plan: $plan, mode: mode).ignoresSafeArea()
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
                Button("Pack") { pack() }.disabled(suitcaseId == nil || packing)
                Button(serverURL) { showSettings = true }.font(.caption2).lineLimit(1)
            }
                .font(.system(.title2, design: .monospaced))
                .padding()
                .background(.black.opacity(0.6))
                .foregroundStyle(.white)
                .clipShape(RoundedRectangle(cornerRadius: 12))
                .padding(.bottom, 40)
                .sheet(isPresented: $showSettings) {
                    Form {
                        TextField("http://mac-lan-ip:8000", text: $serverURL).keyboardType(.URL)
                        TextField("bearer token (optional)", text: $authToken)
                    }
                    .textInputAutocapitalization(.never)
                    .autocorrectionDisabled()
                }
        }
        .sheet(isPresented: $showingDiagram) {
            if let plan { PlanDiagramView(plan: plan) }
        }
    }

    private func pack() {
        guard let suitcaseId else { return }
        status = "Packing…"
        packing = true
        Task {
            defer { packing = false }
            do {
                let (fetchedPlan, unpacked) = try await API.plan(suitcaseId: suitcaseId)
                plan = fetchedPlan
                showingDiagram = true
                status = unpacked.isEmpty
                    ? "Packed \(fetchedPlan.placements.count) items"
                    : "Packed \(fetchedPlan.placements.count), didn't fit: \(unpacked.map(\.label).joined(separator: ", "))"
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
