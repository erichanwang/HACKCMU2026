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
    /// The item a Delete was tapped on, until the dialog confirms or cancels. Swipe and
    /// Edit-mode deletes are their own confirmation; a tapped button is not.
    @State private var deleting: ScannedItem?

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
            .toolbar {
                // Swipe already deletes, but nothing on screen says so. Edit mode does.
                if !items.isEmpty { ToolbarItem(placement: .topBarTrailing) { EditButton() } }
            }
        }
        .task { await load() }
        .confirmationDialog("Delete this item?",
                            isPresented: Binding(get: { deleting != nil }, set: { if !$0 { deleting = nil } }),
                            titleVisibility: .visible, presenting: deleting) { doomed in
            Button("Delete \(doomed.label ?? "item")", role: .destructive) { remove(doomed) }
        } message: { _ in
            Text("It leaves your inventory and any suitcase it was in.")
        }
    }

    private func row(_ scanned: ScannedItem) -> some View {
        HStack(spacing: 13) {
            StampIcon(symbol: scanned.symbol)
            VStack(alignment: .leading, spacing: 2) {
                Text(scanned.labelStatus == "pending" ? "Identifying…" : (scanned.label ?? "Unlabelled"))
                    .font(.body.weight(.medium))
                Text("\(scanned.manifestSize) cm · \(shortBagName(for: scanned.suitcaseId))")
                    .font(.caption.monospaced())
                    .foregroundStyle(.secondary)
            }
        }
        .padding(.vertical, 3)
    }

    /// Label, rigidity and the rest of the server's guesses, edited through the same
    /// `ItemEditor` the scan panel shows.
    private func detail(_ scanned: Binding<ScannedItem>) -> some View {
        Form {
            Section { ItemEditor(item: scanned) }
            Section("Suitcase") {
                Menu {
                    moveMenu(scanned.wrappedValue)
                } label: {
                    Label(bagName(for: scanned.wrappedValue.suitcaseId), systemImage: "suitcase")
                }
            }
            Section {
                Button(role: .destructive) { deleting = scanned.wrappedValue } label: {
                    Label("Delete item", systemImage: "trash")
                }
            }
        }
        .navigationTitle(scanned.wrappedValue.label ?? "Item")
        .navigationBarTitleDisplayMode(.inline)
    }

    /// Put in a suitcase, take out of one, or delete — current bag ticked.
    @ViewBuilder private func menu(_ target: ScannedItem) -> some View {
        moveMenu(target)
        Divider()
        Button("Delete", systemImage: "trash", role: .destructive) { deleting = target }
    }

    @ViewBuilder private func moveMenu(_ target: ScannedItem) -> some View {
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
    }

    private func bagName(for id: String?) -> String {
        guard let id else { return "Not in a suitcase" }
        return suitcases.first { $0.id == id }?.displayName ?? "Another suitcase"
    }

    /// The bag's name alone. A row already states the item's own size; repeating the
    /// bag's next to it wrapped every row onto a second line for no information.
    private func shortBagName(for id: String?) -> String {
        guard let id else { return "Not in a suitcase" }
        return suitcases.first { $0.id == id }?.name ?? "Another suitcase"
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
        deleting = nil
        Task {
            do { try await API.delete(itemId: removing.id) } catch {
                status = "delete: \(error.localizedDescription)"
                await load()  // the row is still there on the server; put it back
            }
        }
    }
}

/// An item's glyph in the load sheet's vocabulary: ink on paper inside a hairline
/// field box, the way a form stamps a category. Not a tinted chip.
struct StampIcon: View {
    let symbol: String

    var body: some View {
        Image(systemName: symbol)
            .font(.system(size: 14, weight: .regular))
            .foregroundStyle(Sheet.ink.opacity(0.75))
            .frame(width: 30, height: 30)
            .overlay(Rectangle().stroke(Sheet.ink.opacity(0.25), lineWidth: 0.75))
            .accessibilityHidden(true)
    }
}

extension ScannedItem {
    /// A glyph for whatever the server called this. Keyword match, first hit wins; an
    /// item nobody has labelled yet gets the generic box rather than a wrong picture.
    var symbol: String {
        let text = (label ?? "").lowercased()
        for (needle, symbol) in ScannedItem.symbols where text.contains(needle) { return symbol }
        return "shippingbox.fill"
    }

    /// Ordered: "toiletry bag" has to match toiletries before it matches bags.
    private static let symbols: [(String, String)] = [
        ("toiletr", "drop.fill"), ("shampoo", "drop.fill"), ("soap", "drop.fill"), ("wash", "drop.fill"),
        ("shoe", "shoe.fill"), ("boot", "shoe.fill"), ("sneaker", "shoe.fill"), ("trainer", "shoe.fill"),
        ("sandal", "shoe.fill"),
        ("jean", "tshirt.fill"), ("trouser", "tshirt.fill"), ("pant", "tshirt.fill"), ("shirt", "tshirt.fill"),
        ("sweater", "tshirt.fill"), ("jumper", "tshirt.fill"), ("jacket", "tshirt.fill"), ("coat", "tshirt.fill"),
        ("dress", "tshirt.fill"), ("sock", "tshirt.fill"), ("cloth", "tshirt.fill"), ("hoodie", "tshirt.fill"),
        ("towel", "square.stack.fill"), ("blanket", "square.stack.fill"),
        ("book", "book.closed.fill"), ("paperback", "book.closed.fill"), ("novel", "book.closed.fill"),
        ("notebook", "book.closed.fill"),
        ("camera", "camera.fill"), ("lens", "camera.fill"),
        ("laptop", "laptopcomputer"), ("macbook", "laptopcomputer"), ("computer", "laptopcomputer"),
        ("ipad", "ipad"), ("tablet", "ipad"), ("phone", "iphone"),
        ("charger", "powerplug.fill"), ("cable", "powerplug.fill"), ("adapter", "powerplug.fill"),
        ("battery", "powerplug.fill"), ("power", "powerplug.fill"),
        ("headphone", "headphones"), ("earbud", "headphones"), ("earphone", "headphones"),
        ("bottle", "waterbottle.fill"), ("flask", "waterbottle.fill"),
        ("glasses", "eyeglasses"), ("sunglass", "eyeglasses"),
        ("umbrella", "umbrella.fill"),
        ("medicine", "cross.case.fill"), ("first aid", "cross.case.fill"), ("pill", "cross.case.fill"),
        ("hat", "hat.widebrim.fill"), ("cap", "hat.widebrim.fill"),
        ("bag", "bag.fill"), ("pouch", "bag.fill"), ("case", "bag.fill"), ("kit", "bag.fill"),
    ]
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
