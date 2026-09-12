import SwiftUI

@main
struct SpikeApp: App {
    var body: some Scene { WindowGroup { ContentView() } }
}

struct ContentView: View {
    var body: some View {
        NavigationStack {
            List {
                NavigationLink("Scan a box") { ScanScreen() }
                NavigationLink("Plan AR frame") { PlanARView() }
                NavigationLink("Plan 3D scene") { PlanSceneScreen() }
                NavigationLink("Scanned item") { ScannedItemScreen() }
            }
            .navigationTitle("Spike")
        }
    }
}

/// The original scan screen, unchanged apart from moving off the app's root so
/// only one ARSession is ever live.
struct ScanScreen: View {
    @State private var item: ScannedItem?
    @State private var status = "Point at a box on a table, then tap it"

    var body: some View {
        ZStack(alignment: .bottom) {
            ScanView(item: $item, status: $status).ignoresSafeArea()
            VStack(spacing: 8) {
                if let item {
                    Text(String(format: "%.1f × %.1f × %.1f cm", item.width * 100, item.depth * 100, item.height * 100))
                    if item.label != nil { ItemEditor(item: Binding($item)!) }
                }
                Text(status).font(.footnote)
            }
                .font(.system(.title2, design: .monospaced))
                .padding()
                .background(.black.opacity(0.6))
                .foregroundStyle(.white)
                .clipShape(RoundedRectangle(cornerRadius: 12))
                .padding(.bottom, 40)
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
        Text("\(item.labelSource ?? "") label · \(item.rigiditySource ?? "") rigidity").font(.caption2)
    }
}
