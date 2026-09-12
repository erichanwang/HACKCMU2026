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
    /// Everything scanned into this suitcase, as of the last time the Items sheet was opened.
    @State private var items: [ScannedItem] = []
    @State private var showingItems = false
    /// Everything this user has scanned, across suitcases, newest first; kept across Reset.
    /// Shown as InventoryRings over the camera while the backpack is open.
    @State private var inventory: [ScannedItem] = []
    @State private var showingInventory = false
    @State private var confirmingReset = false
    /// Where the server is and how to authenticate; typed on the phone, kept across launches.
    @AppStorage("serverURL") private var serverURL = API.defaultBase
    @AppStorage("authToken") private var authToken = ""
    @State private var showSettings = false

    var body: some View {
        ZStack(alignment: .bottom) {
            ScanView(item: $item, status: $status, suitcaseId: $suitcaseId, plan: $plan, mode: mode).ignoresSafeArea()
            if showingInventory {
                InventoryRings(items: inventory, select: { item = $0 }, dismiss: { showingInventory = false })
                    .transition(.opacity)
            }
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
                HStack(spacing: 16) {
                    Button("Pack") { pack() }.disabled(suitcaseId == nil || packing)
                    Button("Items (\(items.count))") { showingItems = true }.disabled(suitcaseId == nil)
                    Button("Reset") { confirmingReset = true }.disabled(suitcaseId == nil)
                }
                .sheet(isPresented: $showingItems) { itemList }
                .confirmationDialog("Delete this suitcase? Scanned items stay in your inventory.",
                                    isPresented: $confirmingReset, titleVisibility: .visible) {
                    Button("Reset", role: .destructive) { reset() }
                }
                Button(serverURL) { showSettings = true }.font(.caption2).lineLimit(1)
            }
                .font(.system(.title2, design: .monospaced))
                .padding()
                .background(.black.opacity(0.6))
                .foregroundStyle(.white)
                .clipShape(RoundedRectangle(cornerRadius: 12))
                // The segmented picker makes the panel full-width, so the screen's bottom-right
                // corner is the panel's; the backpack floats over it rather than beside it.
                .overlay(alignment: .bottomTrailing) { backpack }
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
        .animation(.easeOut(duration: 0.2), value: showingInventory)
        // Quiet on launch: a server that isn't up yet must not replace the first-tap hint.
        .task { if let saved = try? await API.inventory() { inventory = saved } }
        .onChange(of: item) { _, new in
            // ScanView sets `item` on the upload ack and again when the label arrives, and ItemEditor
            // sets it on a PATCH, so both lists stay current without reopening a sheet. A `createdAt`
            // is what makes it the server's document; the pre-upload local scan has none. An
            // inventory circle tapped from an old suitcase sets `item` too; that one stays out of
            // this bag's Items.
            guard let new, new.createdAt != nil else { return }
            if let i = inventory.firstIndex(where: { $0.id == new.id }) { inventory[i] = new } else { inventory.insert(new, at: 0) }
            guard new.suitcaseId == suitcaseId else { return }
            if let i = items.firstIndex(where: { $0.id == new.id }) { items[i] = new } else { items.append(new) }
        }
    }

    /// Toggles the inventory rings; the count badge is what makes a scan visibly land somewhere.
    private var backpack: some View {
        Button {
            showingInventory.toggle()
        } label: {
            Image(systemName: showingInventory ? "xmark" : "backpack.fill")
                .frame(width: 48, height: 48)
                .background(.white.opacity(showingInventory ? 0.25 : 0.12), in: Circle())
                .overlay(alignment: .topTrailing) {
                    if !inventory.isEmpty {
                        Text("\(inventory.count)")
                            .font(.system(.caption2, design: .monospaced).bold())
                            .padding(.horizontal, 5).padding(.vertical, 1)
                            .background(.white, in: Capsule())
                            .foregroundStyle(.black)
                    }
                }
        }
        .accessibilityLabel(showingInventory ? "Close inventory" : "Inventory, \(inventory.count) items")
        .padding(8)
    }

    /// What is in the suitcase right now, re-read from the server each time the sheet opens.
    private var itemList: some View {
        List {
            ForEach(items) { scanned in
                VStack(alignment: .leading, spacing: 2) {
                    Text(scanned.labelStatus == "pending" ? "labelling…" : (scanned.label ?? "unlabelled"))
                    Text(String(format: "%.1f × %.1f × %.1f cm · %@",
                                scanned.width * 100, scanned.depth * 100, scanned.height * 100,
                                scanned.rigidity ?? "?"))
                        .font(.caption)
                }
            }
            .onDelete { offsets in
                let ids = offsets.map { items[$0].id }
                items.remove(atOffsets: offsets)
                inventory.removeAll { ids.contains($0.id) }  // same item, both counts
                Task {
                    for id in ids {
                        do { try await API.delete(itemId: id) } catch { status = "delete: \(error.localizedDescription)" }
                    }
                }
            }
        }
        .task { await loadItems() }
    }

    private func loadItems() async {
        guard let suitcaseId else { return }
        do { items = try await API.items(suitcaseId: suitcaseId) }
        catch { status = "items: \(error.localizedDescription)" }
    }

    private func reset() {
        guard let suitcaseId else { return }
        Task {
            do { try await API.deleteSuitcase(id: suitcaseId) } catch {
                status = "reset: \(error.localizedDescription)"
                return
            }
            self.suitcaseId = nil
            item = nil
            plan = nil
            items = []
            mode = .suitcase
            status = "Point at your open suitcase on the floor, then tap it"
        }
    }

    private func pack() {
        guard let suitcaseId else { return }
        status = "Packing…"
        packing = true
        Task {
            defer { packing = false }
            do {
                let (fetchedPlan, unpacked, pendingLabels) = try await API.plan(suitcaseId: suitcaseId)
                plan = fetchedPlan
                showingDiagram = true
                status = unpacked.isEmpty
                    ? "Packed \(fetchedPlan.placements.count) items"
                    : "Packed \(fetchedPlan.placements.count), didn't fit: \(unpacked.map(\.label).joined(separator: ", "))"
                if pendingLabels > 0 { status += ", \(pendingLabels) still labelling" }
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
            .onChange(of: item.id) { _, _ in label = item.label ?? "" }
            .onSubmit { Task { item = try await API.update(id: item.id, label: label, rigidity: nil) } }
        Picker("rigidity", selection: Binding(get: { item.rigidity ?? "rigid" }, set: { r in
            Task { item = try await API.update(id: item.id, label: nil, rigidity: r) }
        })) {
            ForEach(["rigid", "soft", "fragile"], id: \.self) { Text($0).tag($0) }
        }
        .pickerStyle(.segmented)
        if let d = item.description, !d.isEmpty { Text(d).font(.caption) }
        Text("~\(String(format: "%.1f", item.mass ?? 0)) kg · squeezes \(String(format: "%.1f", item.compressibility ?? 1))×\(item.keepUpright == true ? " · keep upright" : "")").font(.caption2)
        Text("\(item.labelSource ?? "") label · \(item.rigiditySource ?? "") rigidity").font(.caption2)
    }
}
