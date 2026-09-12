import PackingPlan
import PackingPlanUI
import SwiftUI

@main
struct SpikeApp: App {
    var body: some Scene { WindowGroup { ContentView() } }
}

struct ContentView: View {
    @State private var suitcase: Suitcase?
    @State private var phase = ScanPhase.idle("Tap an object to scan it")
    @State private var status = ""
    @State private var scanningSuitcase = false
    @State private var suitcaseDims: [Float]?
    @State private var showItems = false
    @State private var itemCount = 0
    @State private var rescan: ScannedItem?
    /// The plan the solver actually produced, shown in the sheet. Nil = no sheet.
    @State private var plan: PackingPlan?
    @State private var packing = false

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
            ScanView(mode: .suitcase, status: $status, suitcaseDims: $suitcaseDims).ignoresSafeArea()
            VStack(spacing: 8) {
                Text(status)
                HStack {
                    Button("Cancel") { scanningSuitcase = false; suitcaseDims = nil }
                    if let dims = suitcaseDims {
                        Button("Use this suitcase") {
                            Task {
                                do { suitcase = try await API.createSuitcase(name: "Scanned suitcase", dimensions: dims); scanningSuitcase = false; suitcaseDims = nil }
                                catch { status = "server: \(error.localizedDescription)" }
                            }
                        }.buttonStyle(.borderedProminent)
                    }
                }
            }
            .panel()
        }
    }

    func scanner(_ suitcase: Suitcase) -> some View {
        ZStack(alignment: .bottom) {
            ScanView(suitcaseId: suitcase.id, rescanId: rescan?.id, phase: $phase, status: $status).ignoresSafeArea()
            VStack {
                HStack {
                    Text(suitcase.name).bold()
                    Text(String(format: "%.0f × %.0f × %.0f cm", suitcase.dimensions[0] * 100, suitcase.dimensions[1] * 100, suitcase.dimensions[2] * 100))
                    Spacer()
                    Button("\(itemCount) items") { showItems = true }
                    Button(packing ? "Packing…" : "Pack") { pack(suitcase) }.disabled(packing || itemCount == 0)
                    Button("Change") { self.suitcase = nil; phase = .idle("Tap an object to scan it") }
                }
                .padding(10).background(.black.opacity(0.6)).foregroundStyle(.white).clipShape(RoundedRectangle(cornerRadius: 12)).padding()
                Spacer()
            }
            .sheet(isPresented: Binding(get: { plan != nil }, set: { if !$0 { plan = nil } })) {
                if let plan { PlanDiagramView(plan: plan) }
            }
            .sheet(isPresented: $showItems) {
                ItemsList(suitcase: suitcase, rescan: { it in rescan = it; phase = .idle("Tap \(it.label ?? "the item") again to re-measure it") })
            }
            .task(id: "\(suitcase.id)-\(phase.key)-\(showItems)") {
                itemCount = (try? await API.items(in: suitcase.id).count) ?? itemCount
            }
            VStack(spacing: 8) { scanPanel }.panel()
        }
    }

    @ViewBuilder var scanPanel: some View {
        switch phase {
        case .idle(let hint):
            if rescan != nil { Label("Rescan mode", systemImage: "arrow.clockwise").font(.footnote) }
            Text(hint)
            if rescan != nil { Button("Cancel rescan") { rescan = nil; phase = .idle("Tap an object to scan it") } }
        case .measured(let it):
            dims(it)
            HStack { ProgressView().tint(.white); Text("Identifying…") }
        case .review(let it, let rescanned):
            dims(it)
            ReviewEditor(item: Binding(get: { it }, set: { phase = .review($0, rescanned: rescanned) }))
            HStack {
                Button("Discard") { phase = .idle("Tap an object to scan it") }
                Button(rescanned ? "Save changes" : "Add to suitcase") {
                    var toSave = it
                    if toSave.label?.isEmpty ?? true { toSave.label = "unknown" }
                    phase = .saving(toSave)
                    Task {
                        do { phase = .saved(try await API.upload(toSave, image: nil), rescanned: rescanned) }
                        catch { phase = .failed(toSave, error.localizedDescription) }
                    }
                }.buttonStyle(.borderedProminent)
            }
        case .saving(let it):
            dims(it)
            HStack { ProgressView().tint(.white); Text("Saving…") }
        case .saved(let it, let rescanned):
            Label(rescanned ? "Re-measured \(it.label ?? "item")" : "Added: \(it.label ?? "unknown")", systemImage: "checkmark.circle.fill").foregroundStyle(.green)
            dims(it)
            ItemEditor(item: Binding(get: { it }, set: { phase = .saved($0, rescanned: rescanned) }))
            Button("Next item") { rescan = nil; phase = .idle("Tap the next object") }.buttonStyle(.borderedProminent)
        case .failed(let it, let why):
            dims(it)
            Label("Not saved: \(why)", systemImage: "exclamationmark.triangle.fill").foregroundStyle(.orange)
            Button("Retry") { phase = .review(it, rescanned: false) }
            Button("Discard") { phase = .idle("Tap an object to scan it") }
        }
    }

    func pack(_ suitcase: Suitcase) {
        packing = true
        Task {
            defer { packing = false }
            do { plan = try await API.plan(suitcaseId: suitcase.id) }
            catch { phase = .idle("Pack failed: \(error.localizedDescription)") }
        }
    }

    func dims(_ it: ScannedItem) -> some View {
        Text(String(format: "%.1f × %.1f × %.1f cm", it.width * 100, it.depth * 100, it.height * 100)).font(.system(.title2, design: .monospaced))
    }
}

extension View {
    func panel() -> some View {
        self.padding().background(.black.opacity(0.65)).foregroundStyle(.white).clipShape(RoundedRectangle(cornerRadius: 12)).padding(.horizontal).padding(.bottom, 40)
    }
}

/// Edits the guess in place before anything is stored. A changed field becomes user-sourced.
struct ReviewEditor: View {
    @Binding var item: ScannedItem

    var body: some View {
        TextField("What is it?", text: Binding(get: { item.label ?? "" }, set: { item.label = $0; item.labelSource = "user" }))
            .textFieldStyle(.roundedBorder).foregroundStyle(.black)
        Picker("rigidity", selection: Binding(get: { item.rigidity ?? "rigid" }, set: { item.rigidity = $0; item.rigiditySource = "user" })) {
            ForEach(["rigid", "soft", "fragile"], id: \.self) { Text($0) }
        }
        .pickerStyle(.segmented)
        if let d = item.description, !d.isEmpty { Text(d).font(.caption) }
        Text("~\(String(format: "%.1f", item.mass ?? 0)) kg · squeezes \(String(format: "%.1f", item.compressibility ?? 1))×\(item.keepUpright == true ? " · keep upright" : "")").font(.caption2)
    }
}

/// Label and rigidity, editable; edits go to the server as user overrides.
struct ItemEditor: View {
    @Binding var item: ScannedItem
    @State private var label = ""
    @State private var saving = false

    var body: some View {
        HStack {
            TextField("label", text: $label)
                .textFieldStyle(.roundedBorder).foregroundStyle(.black)
                .onAppear { label = item.label ?? "" }
                .onChange(of: item.id) { label = item.label ?? "" }
                .onSubmit { save(label: label, rigidity: nil) }
            if saving { ProgressView().tint(.white) }
        }
        Picker("rigidity", selection: Binding(get: { item.rigidity ?? "rigid" }, set: { save(label: nil, rigidity: $0) })) {
            ForEach(["rigid", "soft", "fragile"], id: \.self) { Text($0) }
        }
        .pickerStyle(.segmented)
        if let d = item.description, !d.isEmpty { Text(d).font(.caption) }
        Text("~\(String(format: "%.1f", item.mass ?? 0)) kg · squeezes \(String(format: "%.1f", item.compressibility ?? 1))×\(item.keepUpright == true ? " · keep upright" : "")").font(.caption2)
    }

    func save(label: String?, rigidity: String?) {
        saving = true
        Task {
            defer { saving = false }
            if let updated = try? await API.update(id: item.id, label: label, rigidity: rigidity) { item = updated }
        }
    }
}

/// Create a suitcase (scan it, or type dimensions in cm) or pick an existing one before scanning.
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
                Section("Existing (swipe left to delete)") {
                    ForEach(existing) { s in
                        Button { selected = s } label: {
                            HStack {
                                Text(s.name)
                                Spacer()
                                Text(String(format: "%.0f × %.0f × %.0f cm", s.dimensions[0] * 100, s.dimensions[1] * 100, s.dimensions[2] * 100)).foregroundStyle(.secondary)
                            }
                        }
                    }
                    .onDelete { offsets in
                        Task {
                            for s in offsets.map({ existing[$0] }) { try? await API.delete("suitcases/\(s.id)") }
                            existing.remove(atOffsets: offsets)
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

/// Everything scanned into this suitcase. Tap an item to edit, rescan or delete it.
struct ItemsList: View {
    let suitcase: Suitcase
    var rescan: (ScannedItem) -> Void
    @Environment(\.dismiss) private var dismiss
    @State private var items: [ScannedItem] = []
    @State private var error: String?

    var body: some View {
        NavigationStack {
            List {
                ForEach($items) { $it in
                    NavigationLink {
                        ItemDetail(item: $it, rescan: { rescan(it); dismiss() }, delete: { delete(it) })
                    } label: {
                        HStack {
                            VStack(alignment: .leading) {
                                Text(it.label ?? "unknown")
                                Text(String(format: "%.1f × %.1f × %.1f cm", it.width * 100, it.depth * 100, it.height * 100)).font(.caption).foregroundStyle(.secondary)
                            }
                            Spacer()
                            Text(it.rigidity ?? "").foregroundStyle(.secondary)
                        }
                    }
                }
                .onDelete { offsets in for it in offsets.map({ items[$0] }) { delete(it) } }
                if let error { Text(error).foregroundStyle(.red) }
            }
            .overlay { if items.isEmpty && error == nil { Text("Nothing scanned yet").foregroundStyle(.secondary) } }
            .navigationTitle("\(items.count) items · \(suitcase.name)")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) { Button("Done") { dismiss() } }
                ToolbarItem(placement: .primaryAction) {
                    Menu("Add") {
                        Button("Scan another") { dismiss() }
                        Button("Add by hand (thin or shiny items)") { adding = true }
                    }
                }
            }
            .sheet(isPresented: $adding) { ManualItemForm(suitcaseId: suitcase.id) { items.append($0) } }
            .task {
                do { items = try await API.items(in: suitcase.id) } catch { self.error = "server: \(error.localizedDescription)" }
            }
        }
    }
    @State private var adding = false

    func delete(_ it: ScannedItem) {
        Task {
            try? await API.delete("items/\(it.id)")
            items.removeAll { $0.id == it.id }
        }
    }
}

struct ItemDetail: View {
    @Binding var item: ScannedItem
    var rescan: () -> Void
    var delete: () -> Void
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        Form {
            Section("Label and rigidity") { ItemEditor(item: $item) }
            Section("Measured") {
                Text(String(format: "%.1f × %.1f × %.1f cm  (w × d × h)", item.width * 100, item.depth * 100, item.height * 100))
                Text("\(item.heights.count) × \(item.heights.first?.count ?? 0) shape cells").foregroundStyle(.secondary)
            }
            Section {
                Button("Rescan to re-measure", action: rescan)
                Button("Delete item", role: .destructive) { delete(); dismiss() }
            }
        }
        .navigationTitle(item.label ?? "Item")
    }
}

/// For things LiDAR can't measure (an iPad, a book, a folded shirt): type it in.
struct ManualItemForm: View {
    let suitcaseId: String
    var added: (ScannedItem) -> Void
    @Environment(\.dismiss) private var dismiss
    @State private var label = ""
    @State private var w = ""
    @State private var d = ""
    @State private var h = ""
    @State private var rigidity = "rigid"
    @State private var error: String?

    var body: some View {
        NavigationStack {
            Form {
                TextField("Label (e.g. iPad)", text: $label)
                Section("Size in cm") {
                    HStack {
                        TextField("Width", text: $w).keyboardType(.decimalPad)
                        TextField("Depth", text: $d).keyboardType(.decimalPad)
                        TextField("Height", text: $h).keyboardType(.decimalPad)
                    }
                }
                Picker("Rigidity", selection: $rigidity) { ForEach(["rigid", "soft", "fragile"], id: \.self) { Text($0) } }.pickerStyle(.segmented)
                if let error { Text(error).foregroundStyle(.red) }
            }
            .navigationTitle("Add by hand")
            .toolbar {
                ToolbarItem(placement: .cancellationAction) { Button("Cancel") { dismiss() } }
                ToolbarItem(placement: .confirmationAction) {
                    Button("Add") {
                        guard let wv = Float(w), let dv = Float(d), let hv = Float(h), wv > 0, dv > 0, hv > 0 else { error = "all three sizes must be positive numbers"; return }
                        let item = ScannedItem(width: wv / 100, height: hv / 100, depth: dv / 100, suitcaseId: suitcaseId,
                                               label: label.isEmpty ? "unknown" : label, rigidity: rigidity)
                        Task {
                            do { added(try await API.upload(item, image: nil)); dismiss() }
                            catch { self.error = "server: \(error.localizedDescription)" }
                        }
                    }
                }
            }
        }
    }
}
