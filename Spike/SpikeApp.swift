import ARKit
import PackingPlan
import PackingPlanUI
import SwiftUI

@main
struct SpikeApp: App {
    var body: some Scene { WindowGroup { ContentView() } }
}

/// The app's four screens. `ContentView` owns the selection because two of them care
/// about it: iOS runs one ARSession at a time, so the Scan tab must not hold the camera
/// while another tab is showing, and the Pack tab's AR overlay needs it released outright.
enum AppTab: Hashable { case home, scan, items, pack }

struct ContentView: View {
    @State private var tab = AppTab.home
    /// Held here so changing either re-evaluates the tree that reads `Sheet`.
    @AppStorage("accentName") private var accentName = "Orange"
    @AppStorage("appearance") private var appearance = Appearance.light.rawValue
    /// True while the Pack tab's AR overlay owns the camera.
    @State private var arActive = false

    var body: some View {
        TabView(selection: $tab) {
            HomeScreen(go: { tab = $0 })
                .tabItem { Label("Home", systemImage: "house.fill") }
                .tag(AppTab.home)
            ScanScreen(cameraLive: tab == .scan && !arActive)
                .tabItem { Label("Scan", systemImage: "viewfinder") }
                .tag(AppTab.scan)
            ItemsScreen()
                .tabItem { Label("Items", systemImage: "list.bullet") }
                .tag(AppTab.items)
            PackScreen(arActive: $arActive)
                .tabItem { Label("Pack", systemImage: "cube.transparent") }
                .tag(AppTab.pack)
        }
        .tint(Sheet.accent)
        .preferredColorScheme(Appearance(rawValue: appearance)?.scheme ?? .light)
    }
}

/// The original scan screen, unchanged apart from moving off the app's root so
/// only one ARSession is ever live.
struct ScanScreen: View {
    /// False while another tab is showing: a ScanView left alive off-screen keeps the
    /// ARSession, and the Pack tab's AR overlay would then never get the camera.
    var cameraLive = true
    @State private var item: ScannedItem?
    @State private var status = ""
    @State private var suitcaseId: String?
    @State private var mode = ScanMode.suitcase
    /// The plan the solver actually produced. Outlives the sheet: closing the diagram is how the
    /// user gets back to the AR overlay, so dismissing it must not throw the plan away.
    @State private var plan: PackingPlan?
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
    /// Set when `plan` is the bundled mock rather than the server's, and why.
    @State private var planNotice: String?
    /// The one plan viewer the app presents — 2D/3D flip and the AR overlay.
    /// Both routes to it, packing and the cube button, set this same flag.
    /// `PackingPlanUI.PlanViewer` still exists but is no longer presented here.
    @State private var showingPlanSheet = false
    /// True while that sheet's AR overlay owns the camera; ScanView stands down
    /// so two ARSessions never compete for it.
    @State private var planARActive = false

    var body: some View {
        ZStack {
            if planARActive || !cameraLive {
                // Someone else has the camera — the plan's AR overlay, or another tab.
                // Anything here would be hidden behind it anyway, and a live ScanView
                // would fight it.
                Color.black.ignoresSafeArea()
            } else if !ARWorldTrackingConfiguration.isSupported {
                // A simulator, or a device with no LiDAR. Saying so beats a black screen
                // that reads as a crash.
                ZStack {
                    Sheet.paper.ignoresSafeArea()
                    VStack(spacing: 10) {
                        Image(systemName: "camera.metering.unknown")
                            .font(.largeTitle)
                            .foregroundStyle(Sheet.accent)
                        Text("No AR camera")
                            .font(.title3.weight(.semibold))
                        Text("This device has no ARKit world tracking, so scanning is unavailable here. Everything else works.")
                            .font(.footnote)
                            .multilineTextAlignment(.center)
                            .foregroundStyle(Sheet.ink.opacity(0.55))
                            .padding(.horizontal, 44)
                    }
                    .foregroundStyle(Sheet.ink)
                }
            } else {
                ScanView(item: $item, status: $status, suitcaseId: $suitcaseId, plan: $plan, mode: mode).ignoresSafeArea()
            }
            VStack(spacing: 0) {
                topBar
                Spacer()
                // Above the panel, not over it: the panel's own bottom-right corner is the Reset button.
                HStack { Spacer(); backpack }
                    .padding(.horizontal, 12)
                    .padding(.bottom, 10)
                if showingInventory {
                    InventoryStrip(items: inventory, select: { item = $0 },
                                   dismiss: { showingInventory = false }, menu: { itemMenu($0) })
                        .padding(.horizontal, 12)
                        .padding(.bottom, 8)
                        .transition(.move(edge: .bottom).combined(with: .opacity))
                }
                panel
            }
            // Chrome over the live feed follows the app's own appearance setting.
        }
        .tint(Sheet.accent)
        .animation(.easeOut(duration: 0.2), value: showingInventory)
        .sheet(isPresented: $showingItems) { itemList }
        .sheet(isPresented: $showSettings) { SettingsSheet(serverURL: $serverURL, authToken: $authToken) }
        .sheet(isPresented: $showingPlanSheet) {
            if let plan {
                PlanSheet(plan: plan, notice: planNotice,
                          scans: Dictionary(inventory.map { ($0.id, $0) }, uniquingKeysWith: { a, _ in a }),
                          arActive: $planARActive)
            }
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
            .background(Sheet.paper.opacity(0.92), in: RoundedRectangle(cornerRadius: 16, style: .continuous))
            Spacer()
            Button { showSettings = true } label: {
                Image(systemName: "gearshape")
                    .font(.body.weight(.semibold))
                    .foregroundStyle(Sheet.ink)
                    .frame(width: 44, height: 44)
                    .background(Sheet.paper.opacity(0.92), in: Circle())
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
                            .background(Sheet.accent, in: Capsule())
                            .foregroundStyle(.white)
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
                    .foregroundStyle(Sheet.ink.opacity(0.5))
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
        .padding(18)
        .background(Sheet.paper.opacity(0.94), in: RoundedRectangle(cornerRadius: 24, style: .continuous))
        .overlay(RoundedRectangle(cornerRadius: 24, style: .continuous).stroke(Sheet.hairline, lineWidth: 0.5))
        .padding(.horizontal, 14)
        .padding(.bottom, 10)
    }

    /// Every in-flight status (ScanView's and this file's) ends in "…"; a failed request is
    /// "<what>: <error>". Both are conventions of the strings, not fields on a model.
    private var statusRow: some View {
        HStack(alignment: .firstTextBaseline, spacing: 9) {
            if status.contains("…") {
                ProgressView().controlSize(.small)
            } else if ["server:", "plan:", "items:", "reset:", "delete:", "move:"].contains(where: { status.hasPrefix($0) }) {
                Image(systemName: "exclamationmark.triangle.fill")
                    .font(.footnote)
                    .foregroundStyle(Sheet.warn)
            }
            // Wraps onto as many lines as it needs. This line carries the whole
            // instruction for the next tap, so truncating it loses the instruction.
            Text(status)
                .font(.subheadline)
                .foregroundStyle(Sheet.ink)
                .fixedSize(horizontal: false, vertical: true)
                .frame(maxWidth: .infinity, alignment: .leading)
        }
    }

    private var actions: some View {
        let canPack = suitcaseId != nil && !packing
        return HStack(spacing: 9) {
            Button { pack() } label: {
                Group {
                    if packing {
                        ProgressView().tint(.white)
                    } else {
                        Label("Pack", systemImage: "shippingbox.fill")
                            .font(.subheadline.weight(.semibold))
                    }
                }
                .foregroundStyle(.white)
                .frame(maxWidth: .infinity, minHeight: 46)
                .background(canPack ? Sheet.accent : Sheet.ink.opacity(0.15), in: Capsule())
            }
            .buttonStyle(.plain)
            .disabled(!canPack)

            quietAction("list.bullet", count: items.count, enabled: suitcaseId != nil,
                        label: "Items in this suitcase: \(items.count)") { showingItems = true }
            quietAction("cube.transparent", enabled: plan != nil,
                        label: "2D, 3D and AR plan views") { showingPlanSheet = true }
            quietAction("trash", enabled: suitcaseId != nil, tint: Sheet.warn,
                        label: "Reset suitcase") { confirmingReset = true }
        }
    }

    /// The quiet half of the action row: one size, one shape, one fill.
    private func quietAction(_ symbol: String, count: Int? = nil, enabled: Bool,
                             tint: Color = Sheet.ink, label: String,
                             action: @escaping () -> Void) -> some View {
        Button(action: action) {
            HStack(spacing: 5) {
                Image(systemName: symbol).font(.footnote.weight(.semibold))
                if let count { Text("\(count)").font(.footnote.monospacedDigit().weight(.semibold)) }
            }
            .foregroundStyle(enabled ? tint : Sheet.ink.opacity(0.28))
            .frame(minWidth: 46, minHeight: 46)
            .padding(.horizontal, count == nil ? 0 : 6)
            .background(Sheet.card, in: Capsule())
            .overlay(Capsule().stroke(Sheet.hairline, lineWidth: 0.5))
        }
        .buttonStyle(.plain)
        .disabled(!enabled)
        .accessibilityLabel(label)
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
        guard let size = bag.sizeText else { return bag.name }
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
            status = ""
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
                planNotice = nil
                showingPlanSheet = true
                status = unpacked.isEmpty
                    ? "Packed \(fetchedPlan.placements.count) items"
                    : "Packed \(fetchedPlan.placements.count), didn't fit: \(unpacked.map(\.label).joined(separator: ", "))"
                if pendingLabels > 0 { status += ", \(pendingLabels) still labelling" }
            } catch {
                // The mock stands in so the views are still usable, but never
                // silently: the sheet says it is a mock and why.
                let reason = PlanFallback.message(for: error)
                status = "plan: \(reason)"
                if let mock = PlanFallback.mockPlan() {
                    planNotice = reason
                    plan = mock
                }
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
    @AppStorage("accentName") private var accentName = "Orange"
    @AppStorage("appearance") private var appearance = Appearance.light.rawValue
    /// Which model the server asks to identify a scan. Sent with every upload; the
    /// server re-asks the same one on its background retries.
    @AppStorage("labelModel") private var labelModel = "both"
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
                Section("Appearance") {
                    Picker("Theme", selection: $appearance) {
                        ForEach(Appearance.allCases, id: \.rawValue) { Text($0.label).tag($0.rawValue) }
                    }
                    .pickerStyle(.segmented)
                    Picker("Undertone", selection: $accentName) {
                        ForEach(Sheet.accents, id: \.name) { entry in
                            HStack {
                                Circle().fill(entry.color).frame(width: 14, height: 14)
                                Text(entry.name)
                            }
                            .tag(entry.name)
                        }
                    }
                }
                Section {
                    Picker("Model", selection: $labelModel) {
                        Text("MoE").tag("both")
                        Text("Grok").tag("grok")
                        Text("Claude").tag("claude")
                    }
                    .pickerStyle(.segmented)
                } header: {
                    Text("Identification")
                } footer: {
                    Text(modelFooter)
                }
                Section("Bearer token") {
                    TextField("Optional", text: $authToken)
                }
                Section("Developer") {
                    NavigationLink("Plan views (mock)") { MockPlanScreen() }
                    NavigationLink("Scanned item") { ScannedItemScreen() }
                }
            }
            .textInputAutocapitalization(.never)
            .autocorrectionDisabled()
            .navigationTitle("Settings")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar { Button("Done") { dismiss() } }
        }
        .presentationDetents([.medium, .large])
    }

    /// What each choice actually does on the server, in its own words.
    private var modelFooter: String {
        switch labelModel {
        case "grok": return "Only Grok is asked. Faster, and one opinion."
        case "claude": return "Only Claude is asked."
        default:
            return "Both are asked and the answers arbitrated: if one declines, the other's answer stands; if they name it differently, Claude's wins. A model with no API key set on the server is skipped."
        }
    }
}
