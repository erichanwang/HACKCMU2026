import SwiftUI

@main
struct SpikeApp: App {
    var body: some Scene { WindowGroup { ContentView() } }
}

struct ContentView: View {
    @State private var suitcase: Suitcase?
    @State private var item: ScannedItem?
    @State private var status = "Point at a box on a table, then tap it"
    @State private var scanningSuitcase = false
    @State private var suitcaseDims: [Float]?

    var body: some View {
        if let suitcase {
            scanner(suitcase)
        } else if scanningSuitcase {
            suitcaseScanner
        } else {
            SuitcasePicker(selected: $suitcase, scan: { status = "Close the suitcase, put it on the floor, tap it"; scanningSuitcase = true })
        }
    }

    var suitcaseScanner: some View {
        ZStack(alignment: .bottom) {
            ScanView(mode: .suitcase, item: .constant(nil), status: $status, suitcaseDims: $suitcaseDims).ignoresSafeArea()
            VStack(spacing: 8) {
                Text(status)
                HStack {
                    Button("Cancel") { scanningSuitcase = false; suitcaseDims = nil }
                    if let dims = suitcaseDims {
                        Button("Use this suitcase") {
                            Task {
                                do { suitcase = try await API.createSuitcase(name: "Scanned suitcase", dimensions: dims); scanningSuitcase = false; suitcaseDims = nil; status = "Point at a box on a table, then tap it" }
                                catch { status = "server: \(error.localizedDescription)" }
                            }
                        }.buttonStyle(.borderedProminent)
                    }
                }
            }
            .padding().background(.black.opacity(0.6)).foregroundStyle(.white).clipShape(RoundedRectangle(cornerRadius: 12)).padding(.bottom, 40)
        }
    }

    func scanner(_ suitcase: Suitcase) -> some View {
        ZStack(alignment: .bottom) {
            ScanView(suitcaseId: suitcase.id, item: $item, status: $status).ignoresSafeArea()
            VStack {
                HStack {
                    Text(suitcase.name).bold()
                    Text(String(format: "%.0f × %.0f × %.0f cm", suitcase.dimensions[0] * 100, suitcase.dimensions[1] * 100, suitcase.dimensions[2] * 100))
                    Spacer()
                    Button("Change") { self.suitcase = nil; item = nil }
                }
                .padding(10).background(.black.opacity(0.6)).foregroundStyle(.white).clipShape(RoundedRectangle(cornerRadius: 12)).padding()
                Spacer()
            }
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

/// Create a suitcase (dimensions typed in cm) or pick an existing one before scanning.
struct SuitcasePicker: View {
    @Binding var selected: Suitcase?
    var scan: () -> Void
    @State private var existing: [Suitcase] = []
    @State private var name = "Carry-on"
    @State private var w = "55"
    @State private var h = "22"
    @State private var d = "35"
    @State private var error: String?

    var body: some View {
        NavigationStack {
            Form {
                Section {
                    Button("Scan a suitcase", action: scan)
                }
                Section("Or type interior dimensions (cm)") {
                    TextField("Name", text: $name)
                    HStack {
                        TextField("Width", text: $w).keyboardType(.decimalPad)
                        TextField("Height", text: $h).keyboardType(.decimalPad)
                        TextField("Depth", text: $d).keyboardType(.decimalPad)
                    }
                    Button("Create and scan") {
                        Task {
                            do {
                                let dims = try [w, h, d].map { s -> Float in
                                    guard let v = Float(s), v > 0 else { throw URLError(.badURL, userInfo: [NSLocalizedDescriptionKey: "dimensions must be positive numbers"]) }
                                    return v / 100
                                }
                                selected = try await API.createSuitcase(name: name, dimensions: dims)
                            } catch { self.error = error.localizedDescription }
                        }
                    }
                }
                Section("Existing") {
                    ForEach(existing) { s in
                        Button { selected = s } label: {
                            HStack {
                                Text(s.name)
                                Spacer()
                                Text(String(format: "%.0f × %.0f × %.0f cm", s.dimensions[0] * 100, s.dimensions[1] * 100, s.dimensions[2] * 100)).foregroundStyle(.secondary)
                            }
                        }
                    }
                }
                if let error { Text(error).foregroundStyle(.red) }
            }
            .navigationTitle("Suitcase")
            .task {
                do { existing = try await API.listSuitcases() } catch { self.error = "server: \(error.localizedDescription)" }
            }
        }
    }
}
