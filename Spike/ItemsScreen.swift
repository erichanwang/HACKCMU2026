import SwiftUI

/// Everything this user has scanned, across every suitcase — the Items tab.
///
/// Shares no state with the Scan tab on purpose: it re-reads `GET /inventory` when the
/// tab appears and on pull-to-refresh, so the server stays the single answer to what is
/// in which bag. Editing, moving and deleting here go through the same routes the scan
/// panel's own menus use.
///
/// ponytail: a move made here leaves the Scan tab's in-memory `plan` stale (the server
/// drops the stored one, the scan screen doesn't know). Tapping Pack again re-plans.
/// Hoist the state into a shared model if that becomes confusing in the demo.
struct ItemsScreen: View {
    @State private var items: [ScannedItem] = []
    @State private var suitcases: [API.Suitcase] = []
    /// Last failed request, in this file's "<what>: <error>" convention. Empty when fine.
    @State private var status = ""

    var body: some View {
        NavigationStack {
            List {
                if !status.isEmpty {
                    Label(status, systemImage: "exclamationmark.triangle.fill")
                        .font(.footnote)
                        .foregroundStyle(.orange)
                }
                ForEach($items) { $scanned in
                    NavigationLink { detail($scanned) } label: { row(scanned) }
                        .contextMenu { menu(scanned) }
                }
                .onDelete { offsets in offsets.map { items[$0] }.forEach(remove) }
            }
            .overlay {
                if items.isEmpty && status.isEmpty {
                    ContentUnavailableView("Nothing scanned yet", systemImage: "shippingbox",
                                           description: Text("Scan something on the Scan tab and it lands here."))
                }
            }
            .navigationTitle("Items")
            .refreshable { await load() }
        }
        .task { await load() }
    }

    private func row(_ scanned: ScannedItem) -> some View {
        VStack(alignment: .leading, spacing: 2) {
            Text(scanned.labelStatus == "pending" ? "Labelling…" : (scanned.label ?? "Unlabelled"))
                .font(.body.weight(.medium))
            Text("\(scanned.sizeText) · \(bagName(for: scanned.suitcaseId))")
                .font(.subheadline.monospacedDigit())
                .foregroundStyle(.secondary)
        }
    }

    /// Label, rigidity and the rest of the server's guesses, edited through the same
    /// `ItemEditor` the scan panel shows.
    private func detail(_ scanned: Binding<ScannedItem>) -> some View {
        Form {
            Section { ItemEditor(item: scanned) }
            Section("Suitcase") {
                Menu {
                    menu(scanned.wrappedValue)
                } label: {
                    Label(bagName(for: scanned.wrappedValue.suitcaseId), systemImage: "suitcase")
                }
            }
        }
        .navigationTitle(scanned.wrappedValue.label ?? "Item")
        .navigationBarTitleDisplayMode(.inline)
    }

    /// Put in a suitcase, take out of one, or delete — current bag ticked.
    @ViewBuilder private func menu(_ target: ScannedItem) -> some View {
        if suitcases.isEmpty {
            Button("Scan a suitcase first") {}.disabled(true)
        }
        ForEach(suitcases) { bag in
            Button { move(target, to: bag.id) } label: {
                if bag.id == target.suitcaseId { Label(bag.displayName, systemImage: "checkmark") } else { Text(bag.displayName) }
            }
        }
        if target.suitcaseId != nil {
            Button("Take out of its suitcase", systemImage: "arrow.up.bin") { move(target, to: nil) }
        }
        Divider()
        Button("Delete", systemImage: "trash", role: .destructive) { remove(target) }
    }

    private func bagName(for id: String?) -> String {
        guard let id else { return "Not in a suitcase" }
        return suitcases.first { $0.id == id }?.displayName ?? "Another suitcase"
    }

    // MARK: - Server calls

    private func load() async {
        do {
            items = try await API.inventory()
            status = ""
        } catch {
            status = "items: \(error.localizedDescription)"
        }
        if let bags = try? await API.suitcases() { suitcases = bags }
    }

    private func move(_ moving: ScannedItem, to bagId: String?) {
        Task {
            do {
                let updated = try await API.move(id: moving.id, toSuitcase: bagId)
                if let i = items.firstIndex(where: { $0.id == updated.id }) { items[i] = updated }
                status = ""
            } catch {
                status = "move: \(error.localizedDescription)"
            }
        }
    }

    private func remove(_ removing: ScannedItem) {
        items.removeAll { $0.id == removing.id }
        Task {
            do { try await API.delete(itemId: removing.id) } catch {
                status = "delete: \(error.localizedDescription)"
                await load()  // the row is still there on the server; put it back
            }
        }
    }
}

extension API.Suitcase {
    /// Stored in metres (team contract); centimetres only here, at the moment of display.
    /// `dimensions` is [width, height, depth] — shown W × D × H, the way a bag is measured.
    var sizeText: String? {
        guard dimensions.count == 3 else { return nil }
        return String(format: "%.0f × %.0f × %.0f cm", dimensions[0] * 100, dimensions[2] * 100, dimensions[1] * 100)
    }

    /// Every bag is created as "scanned suitcase", so the size is what tells them apart.
    var displayName: String { sizeText.map { "\(name) · \($0)" } ?? name }
}
