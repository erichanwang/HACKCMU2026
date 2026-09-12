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
    /// The user's suitcases, for "put this item in…"; re-read whenever the current one changes.
    @State private var suitcases: [API.Suitcase] = []
    /// The item a Delete was tapped on, until the dialog confirms or cancels.
    @State private var deleting: ScannedItem?
    @State private var confirmingReset = false
    /// Where the server is and how to authenticate; typed on the phone, kept across launches.
    @AppStorage("serverURL") private var serverURL = API.defaultBase
    @AppStorage("authToken") private var authToken = ""
    @State private var showSettings = false

    var body: some View {
        ZStack {
            ScanView(item: $item, status: $status, suitcaseId: $suitcaseId, plan: $plan, mode: mode).ignoresSafeArea()
            if showingInventory {
                InventoryRings(items: inventory, select: { item = $0 }, dismiss: { showingInventory = false },
                               menu: { itemMenu($0) })
                    .transition(.opacity)
            }
            VStack(spacing: 0) {
                topBar
                Spacer()
                // Above the panel, not over it: the panel's own bottom-right corner is the Reset button.
                HStack { Spacer(); backpack }
                    .padding(.horizontal, 12)
                    .padding(.bottom, 10)
                panel
            }
            // Camera-app chrome: dark over the live feed whatever the system theme. Sheets follow the system.
            .environment(\.colorScheme, .dark)
        }
        .tint(.indigo)
        .animation(.easeOut(duration: 0.2), value: showingInventory)
        .sheet(isPresented: $showingItems) { itemList }
        .sheet(isPresented: $showSettings) { SettingsSheet(serverURL: $serverURL, authToken: $authToken) }
        .sheet(isPresented: $showingDiagram) {
            if let plan { PlanViewer(plan: plan).presentationDragIndicator(.visible) }
        }
        .confirmationDialog("Delete this suitcase? Scanned items stay in your inventory.",
                            isPresented: $confirmingReset, titleVisibility: .visible) {
            Button("Reset", role: .destructive) { reset() }
        }
        .confirmationDialog("Delete this item?", isPresented: Binding(get: { deleting != nil }, set: { if !$0 { deleting = nil } }),
                            titleVisibility: .visible, presenting: deleting) { doomed in
            Button("Delete \(doomed.label ?? "item")", role: .destructive) { remove(doomed) }
        } message: { _ in
            Text("It leaves your inventory and any suitcase it was in.")
        }
        // Quiet on launch: a server that isn't up yet must not replace the first-tap hint.
        .task {
            if let saved = try? await API.inventory() { inventory = saved }
            await loadSuitcases()
        }
        .onChange(of: suitcaseId) { _, _ in Task { await loadSuitcases() } }
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

    // MARK: - Chrome

    private var topBar: some View {
        HStack {
            Picker("Scan mode", selection: $mode) {
                Text("Suitcase").tag(ScanMode.suitcase)
                Text("Item").tag(ScanMode.item)
            }
            .pickerStyle(.segmented)
            .frame(maxWidth: 240)
            .padding(6)
            .background(.regularMaterial, in: RoundedRectangle(cornerRadius: 14, style: .continuous))
            Spacer()
            Button { showSettings = true } label: {
                Image(systemName: "gearshape.fill")
                    .font(.body.weight(.semibold))
                    .frame(width: 44, height: 44)
                    .background(.regularMaterial, in: Circle())
            }
            .accessibilityLabel("Server settings")
        }
        .padding(.horizontal, 16)
        .padding(.top, 8)
    }

    /// Toggles the inventory rings; the count badge is what makes a scan visibly land somewhere.
    private var backpack: some View {
        Button {
            showingInventory.toggle()
        } label: {
            Image(systemName: showingInventory ? "xmark" : "backpack.fill")
                .font(.body.weight(.semibold))
                .frame(width: 48, height: 48)
                .background(.regularMaterial, in: Circle())
                .overlay(alignment: .topTrailing) {
                    if !inventory.isEmpty {
                        Text("\(inventory.count)")
                            .font(.caption2.bold().monospacedDigit())
                            .padding(.horizontal, 5).padding(.vertical, 1)
                            .background(.white, in: Capsule())
                            .foregroundStyle(.black)
                    }
                }
        }
        .accessibilityLabel(showingInventory ? "Close inventory" : "Inventory, \(inventory.count) items")
    }

    private var panel: some View {
        VStack(alignment: .leading, spacing: 14) {
            if mode == .item, suitcaseId == nil {
                Label("Scan the suitcase first", systemImage: "suitcase")
                    .font(.footnote)
                    .foregroundStyle(.secondary)
            }
            statusRow
            if let item {
                Divider()
                if item.label != nil {
                    ItemEditor(item: Binding($item)!)
                    HStack {
                        Menu { itemMenu(item) } label: {
                            Label(bagName(for: item.suitcaseId), systemImage: "suitcase")
                        }
                        Spacer()
                        Button(role: .destructive) { deleting = item } label: { Label("Delete", systemImage: "trash") }
                    }
                    .font(.subheadline.weight(.medium))
                } else {
                    HStack(alignment: .firstTextBaseline) {
                        Text("New item").font(.headline)
                        Spacer()
                        Text(item.sizeText).font(.subheadline.monospacedDigit()).foregroundStyle(.secondary)
                    }
                }
            }
            actions
        }
        .padding(16)
        .background(.regularMaterial, in: RoundedRectangle(cornerRadius: 22, style: .continuous))
        .padding(.horizontal, 12)
        .padding(.bottom, 8)
    }

    /// Every in-flight status (ScanView's and this file's) ends in "…"; a failed request is
    /// "<what>: <error>". Both are conventions of the strings, not fields on a model.
    private var statusRow: some View {
        HStack(spacing: 10) {
            if status.contains("…") {
                ProgressView().controlSize(.small)
            } else if ["server:", "plan:", "items:", "reset:", "delete:", "move:"].contains(where: { status.hasPrefix($0) }) {
                Image(systemName: "exclamationmark.triangle.fill").foregroundStyle(.orange)
            }
            Text(status).font(.subheadline)
        }
    }

    private var actions: some View {
        HStack(spacing: 10) {
            Button { pack() } label: {
                Label("Pack", systemImage: "shippingbox.fill").frame(maxWidth: .infinity)
            }
            .buttonStyle(.borderedProminent)
            .disabled(suitcaseId == nil || packing)
            Button { showingItems = true } label: { Label("\(items.count)", systemImage: "list.bullet") }
                .buttonStyle(.bordered)
                .disabled(suitcaseId == nil)
                .accessibilityLabel("Items in this suitcase: \(items.count)")
            Button(role: .destructive) { confirmingReset = true } label: { Image(systemName: "trash") }
                .buttonStyle(.bordered)
                .disabled(suitcaseId == nil)
                .accessibilityLabel("Reset suitcase")
        }
        .controlSize(.large)
        .fontWeight(.semibold)
        .monospacedDigit()
    }

    /// What is in the suitcase right now, re-read from the server each time the sheet opens.
    private var itemList: some View {
        NavigationStack {
            List {
                ForEach(items) { scanned in
                    VStack(alignment: .leading, spacing: 2) {
                        Text(scanned.labelStatus == "pending" ? "Labelling…" : (scanned.label ?? "Unlabelled"))
                            .font(.body.weight(.medium))
                        Text("\(scanned.sizeText) · \(scanned.rigidity ?? "rigidity unknown")")
                            .font(.subheadline.monospacedDigit())
                            .foregroundStyle(.secondary)
                    }
                    // Move only: swipe already deletes here, and the Delete dialog lives under this sheet.
                    .contextMenu { moveMenu(scanned) }
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
            .overlay {
                if items.isEmpty {
                    ContentUnavailableView("Nothing scanned yet", systemImage: "shippingbox",
                                           description: Text("Switch to Item and tap something next to the bag."))
                }
            }
            .navigationTitle("In this suitcase")
            .navigationBarTitleDisplayMode(.inline)
        }
        .presentationDetents([.medium, .large])
        .task { await loadItems() }
    }

    // MARK: - Put in a suitcase / delete

    /// The suitcase rows of an item's menu, current bag ticked, then "take out" when it is in one.
    @ViewBuilder private func moveMenu(_ target: ScannedItem) -> some View {
        if suitcases.isEmpty {
            Button("Scan a suitcase first") {}.disabled(true)
        }
        ForEach(suitcases) { bag in
            Button { move(target, to: bag.id) } label: {
                if bag.id == target.suitcaseId { Label(bagName(bag), systemImage: "checkmark") } else { Text(bagName(bag)) }
            }
        }
        if target.suitcaseId != nil {
            Button("Take out of its suitcase", systemImage: "arrow.up.bin") { move(target, to: nil) }
        }
    }

    @ViewBuilder private func itemMenu(_ target: ScannedItem) -> some View {
        moveMenu(target)
        Divider()
        Button("Delete", systemImage: "trash", role: .destructive) { deleting = target }
    }

    /// Every bag is created as "scanned suitcase", so the size is what tells them apart.
    private func bagName(_ bag: API.Suitcase) -> String {
        guard bag.dimensions.count == 3 else { return bag.name }
        let size = String(format: "%.0f × %.0f × %.0f cm", bag.dimensions[0] * 100, bag.dimensions[2] * 100, bag.dimensions[1] * 100)
        return "\(bag.id == suitcaseId ? "This suitcase" : bag.name) · \(size)"
    }

    private func bagName(for id: String?) -> String {
        guard let id else { return "Not in a suitcase" }
        if id == suitcaseId { return "This suitcase" }
        return suitcases.first { $0.id == id }.map(bagName) ?? "Another suitcase"
    }

    private func loadSuitcases() async {
        if let bags = try? await API.suitcases() { suitcases = bags }
    }

    private func move(_ moving: ScannedItem, to bagId: String?) {
        let name = moving.label ?? "item"
        Task {
            do {
                let updated = try await API.move(id: moving.id, toSuitcase: bagId)
                if let i = inventory.firstIndex(where: { $0.id == updated.id }) { inventory[i] = updated }
                items.removeAll { $0.id == updated.id }
                if let suitcaseId, updated.suitcaseId == suitcaseId { items.append(updated) }
                if item?.id == updated.id { item = updated }
                // The bag's plan predates this change; the overlay must not keep showing it.
                if let suitcaseId, moving.suitcaseId == suitcaseId || updated.suitcaseId == suitcaseId {
                    plan = nil
                    status = updated.suitcaseId == suitcaseId ? "\(name) added — tap Pack to re-plan" : "\(name) taken out — tap Pack to re-plan"
                } else {
                    status = updated.suitcaseId == nil ? "\(name) taken out of its suitcase" : "\(name) moved"
                }
            } catch {
                status = "move: \(error.localizedDescription)"
            }
        }
    }

    private func remove(_ removing: ScannedItem) {
        Task {
            do { try await API.delete(itemId: removing.id) } catch {
                status = "delete: \(error.localizedDescription)"
                return
            }
            inventory.removeAll { $0.id == removing.id }
            items.removeAll { $0.id == removing.id }
            if item?.id == removing.id { item = nil }
            if let suitcaseId, removing.suitcaseId == suitcaseId { plan = nil }
            status = "Deleted \(removing.label ?? "item")"
        }
    }

    // MARK: - Server calls

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

extension ScannedItem {
    /// Stored in metres (team contract); centimetres only here, at the moment of display.
    var sizeText: String { String(format: "%.1f × %.1f × %.1f cm", width * 100, depth * 100, height * 100) }
}

/// Shows the server's label and rigidity guess; edits are sent back as user overrides.
struct ItemEditor: View {
    @Binding var item: ScannedItem
    @State private var label = ""

    var body: some View {
        HStack(alignment: .firstTextBaseline) {
            TextField("Label", text: $label)
                .font(.headline)
                .submitLabel(.done)
                .onAppear { label = item.label ?? "" }
                .onChange(of: item.id) { _, _ in label = item.label ?? "" }
                .onSubmit { Task { item = try await API.update(id: item.id, label: label, rigidity: nil) } }
            Text(item.sizeText).font(.subheadline.monospacedDigit()).foregroundStyle(.secondary)
        }
        Picker("Rigidity", selection: Binding(get: { item.rigidity ?? "rigid" }, set: { r in
            Task { item = try await API.update(id: item.id, label: nil, rigidity: r) }
        })) {
            ForEach(["rigid", "soft", "fragile"], id: \.self) { Text($0.capitalized).tag($0) }
        }
        .pickerStyle(.segmented)
        if let d = item.description, !d.isEmpty {
            Text(d).font(.caption).foregroundStyle(.secondary).lineLimit(2)
        }
        Text("~\(String(format: "%.1f", item.mass ?? 0)) kg · squeezes \(String(format: "%.1f", item.compressibility ?? 1))×\(item.keepUpright == true ? " · keep upright" : "")")
            .font(.caption.monospacedDigit())
            .foregroundStyle(.secondary)
        Text("\(item.labelSource ?? "") label · \(item.rigiditySource ?? "") rigidity")
            .font(.caption2)
            .foregroundStyle(.tertiary)
    }
}

/// Server address and bearer token, kept in UserDefaults; API.base re-reads them on every request.
struct SettingsSheet: View {
    @Binding var serverURL: String
    @Binding var authToken: String
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        NavigationStack {
            Form {
                Section {
                    TextField("http://192.168.1.20:8000", text: $serverURL).keyboardType(.URL)
                } header: {
                    Text("Server address")
                } footer: {
                    Text("The Mac running the packing server, on the same Wi-Fi as this phone. `ipconfig getifaddr en0` on the Mac prints its address.")
                }
                Section("Bearer token") {
                    TextField("Optional", text: $authToken)
                }
            }
            .textInputAutocapitalization(.never)
            .autocorrectionDisabled()
            .navigationTitle("Settings")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar { Button("Done") { dismiss() } }
        }
        .presentationDetents([.medium])
    }
}
