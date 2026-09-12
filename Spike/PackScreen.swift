import PackingPlan
import SwiftUI

/// The Pack tab: every suitcase you have, what is in each one, and a play button that
/// runs the solver on it.
///
/// The flow is the one the product actually has — a bag is a container you fill, then
/// solve — so the tab shows the bags first, lets you put items into any of them, and
/// only then offers to pack. The solve and the three views of its result already
/// existed; this screen is the way into them.
struct PackScreen: View {
    /// Raised while the AR overlay owns the camera, so the Scan tab stands its own
    /// ARSession down. iOS runs one at a time.
    @Binding var arActive: Bool

    @State private var suitcases: [API.Suitcase] = []
    @State private var items: [ScannedItem] = []
    @State private var status = ""
    /// The suitcase currently being solved, so only its own button spins.
    @State private var packing: String?
    @State private var plan: PackingPlan?
    @State private var planNotice: String?
    @State private var showingPlan = false
    /// The bag whose contents are being edited.
    @State private var editing: API.Suitcase?

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(spacing: 14) {
                    if !status.isEmpty { statusRow }
                    if suitcases.isEmpty {
                        empty
                    } else {
                        ForEach(suitcases) { bag in bagCard(bag) }
                    }
                }
                .padding(.horizontal, 18)
                .padding(.vertical, 12)
            }
            .background(Sheet.paper)
            .navigationTitle(suitcases.count == 1 ? "1 suitcase" : "\(suitcases.count) suitcases")
            .refreshable { await load() }
            .navigationDestination(isPresented: $showingPlan) {
                if let plan {
                    PlanSheet(plan: plan, notice: planNotice, arActive: $arActive)
                        .navigationTitle("Plan")
                        .navigationBarTitleDisplayMode(.inline)
                }
            }
        }
        .sheet(item: $editing) { bag in
            BagContentsSheet(bag: bag, items: items, move: move)
        }
        .task { await load() }
    }

    // MARK: - Pieces

    private func bagCard(_ bag: API.Suitcase) -> some View {
        let aboard = contents(of: bag)
        let value = fraction(for: bag, aboard: aboard)
        return VStack(alignment: .leading, spacing: 0) {
            HStack(alignment: .firstTextBaseline) {
                Text(bag.name).font(.title3.weight(.semibold))
                Spacer(minLength: 8)
                Text("\(Int((value * 100).rounded()))%")
                    .font(.subheadline.monospacedDigit().weight(.semibold))
                    .foregroundStyle(value > 1 ? Sheet.warn : Sheet.accent)
            }
            Text("\(bag.sizeText ?? "size unknown") · \(aboard.count) \(aboard.count == 1 ? "item" : "items")")
                .font(.footnote.monospacedDigit())
                .foregroundStyle(Sheet.ink.opacity(0.5))
                .padding(.top, 2)

            LoadBar(value: value).padding(.top, 14)

            HStack(spacing: 10) {
                Button { editing = bag } label: {
                    Label("Add items", systemImage: "plus")
                        .font(.subheadline.weight(.medium))
                        .frame(maxWidth: .infinity, minHeight: 44)
                        .background(Sheet.card, in: Capsule())
                }
                .buttonStyle(.plain)
                .foregroundStyle(Sheet.ink)

                Button { pack(bag) } label: {
                    Group {
                        if packing == bag.id {
                            ProgressView().tint(.white)
                        } else {
                            Image(systemName: "play.fill").font(.body)
                        }
                    }
                    .foregroundStyle(.white)
                    .frame(width: 58, height: 44)
                    .background(aboard.isEmpty ? Sheet.ink.opacity(0.18) : Sheet.accent, in: Capsule())
                }
                .buttonStyle(.plain)
                .disabled(aboard.isEmpty || packing != nil)
                .accessibilityLabel("Pack \(bag.name)")
            }
            .padding(.top, 16)

            if aboard.isEmpty {
                Text("Put something in this bag before packing it.")
                    .font(.caption)
                    .foregroundStyle(Sheet.ink.opacity(0.5))
                    .padding(.top, 8)
            }
        }
        .foregroundStyle(Sheet.ink)
        .padding(18)
        .background(Sheet.card.opacity(0.55), in: RoundedRectangle(cornerRadius: 22, style: .continuous))
        .overlay(RoundedRectangle(cornerRadius: 22, style: .continuous).stroke(Sheet.hairline, lineWidth: 0.5))
    }

    private var statusRow: some View {
        HStack(alignment: .top, spacing: 8) {
            Image(systemName: "exclamationmark.triangle.fill").foregroundStyle(Sheet.warn)
            Text(status).font(.footnote).foregroundStyle(Sheet.ink.opacity(0.7))
            Spacer(minLength: 0)
        }
        .padding(.top, 4)
    }

    private var empty: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text("No suitcase yet").font(.title3.weight(.semibold))
            Text("Scan a suitcase on the Scan tab. Every bag you scan shows up here with a play button.")
                .font(.footnote)
                .foregroundStyle(Sheet.ink.opacity(0.6))
                .fixedSize(horizontal: false, vertical: true)
        }
        .foregroundStyle(Sheet.ink)
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(18)
        .background(Sheet.card.opacity(0.55), in: RoundedRectangle(cornerRadius: 22, style: .continuous))
        .padding(.top, 20)
    }

    // MARK: - Numbers

    private func contents(of bag: API.Suitcase) -> [ScannedItem] {
        items.filter { $0.suitcaseId == bag.id }
    }

    private func fraction(for bag: API.Suitcase, aboard: [ScannedItem]) -> Double {
        guard bag.dimensions.count == 3 else { return 0 }
        let capacity = Double(bag.dimensions[0] * bag.dimensions[1] * bag.dimensions[2])
        guard capacity > 0 else { return 0 }
        return aboard.reduce(0.0) { $0 + Double($1.width * $1.height * $1.depth) } / capacity
    }

    // MARK: - Server calls

    private func load() async {
        do {
            suitcases = try await API.suitcases()
            status = ""
        } catch {
            status = "suitcases: \(error.localizedDescription)"
        }
        if let all = try? await API.inventory() { items = all }
    }

    private func move(_ moving: ScannedItem, to bagId: String?) {
        Task {
            do {
                let updated = try await API.move(id: moving.id, toSuitcase: bagId)
                if let i = items.firstIndex(where: { $0.id == updated.id }) { items[i] = updated }
                // The stored plan predates this change; the server drops it and so do we.
                plan = nil
                status = ""
            } catch {
                status = "move: \(error.localizedDescription)"
            }
        }
    }

    private func pack(_ bag: API.Suitcase) {
        packing = bag.id
        Task {
            defer { packing = nil }
            do {
                let (solved, unpacked, pendingLabels) = try await API.plan(suitcaseId: bag.id)
                plan = solved
                planNotice = nil
                status = unpacked.isEmpty ? "" : "Didn't fit: \(unpacked.map(\.label).joined(separator: ", "))"
                if pendingLabels > 0 {
                    status += status.isEmpty ? "\(pendingLabels) still being identified" : ", \(pendingLabels) still being identified"
                }
                showingPlan = true
            } catch {
                // The mock stands in so the views are still usable, but never silently:
                // PlanSheet's own banner says it is a mock and why.
                let reason = PlanFallback.message(for: error)
                status = "plan: \(reason)"
                if let mock = PlanFallback.mockPlan() {
                    planNotice = reason
                    plan = mock
                    showingPlan = true
                }
            }
        }
    }
}

/// Tick items in or out of one bag. Everything you have scanned is listed; tapping a row
/// moves it into this bag, tapping a ticked row takes it out again.
struct BagContentsSheet: View {
    let bag: API.Suitcase
    let items: [ScannedItem]
    let move: (ScannedItem, String?) -> Void

    @Environment(\.dismiss) private var dismiss

    var body: some View {
        NavigationStack {
            List {
                if items.isEmpty {
                    Text("Nothing scanned yet.").font(.footnote).foregroundStyle(.secondary)
                }
                ForEach(items) { item in
                    let inBag = item.suitcaseId == bag.id
                    Button {
                        move(item, inBag ? nil : bag.id)
                    } label: {
                        HStack(spacing: 12) {
                            Image(systemName: inBag ? "checkmark.circle.fill" : "circle")
                                .font(.title3)
                                .foregroundStyle(inBag ? Sheet.accent : Sheet.ink.opacity(0.25))
                            VStack(alignment: .leading, spacing: 2) {
                                Text(item.labelStatus == "pending" ? "Identifying…" : (item.label ?? "Unlabelled"))
                                    .font(.body)
                                    .foregroundStyle(Sheet.ink)
                                Text(whereItIs(item))
                                    .font(.caption.monospaced())
                                    .foregroundStyle(Sheet.ink.opacity(0.5))
                            }
                            Spacer()
                        }
                        .contentShape(Rectangle())
                    }
                    .buttonStyle(.plain)
                }
            }
            .scrollContentBackground(.hidden)
            .background(Sheet.paper)
            .navigationTitle(bag.name)
            .navigationBarTitleDisplayMode(.inline)
            .toolbar { Button("Done") { dismiss() } }
        }
        .presentationDetents([.medium, .large])
    }

    /// An item already in another bag has to say so: moving it takes it out of that one.
    private func whereItIs(_ item: ScannedItem) -> String {
        guard let id = item.suitcaseId else { return "\(item.manifestSize) cm · not in a bag" }
        return id == bag.id ? "\(item.manifestSize) cm · in this bag" : "\(item.manifestSize) cm · in another bag"
    }
}

/// The load against the limit. The 100 mark sits inboard of the end, so a bag holding
/// more than it can close on runs into the overrun strip rather than stopping at full
/// and looking fine.
struct LoadBar: View {
    static let limitShare: CGFloat = 0.84

    let value: Double

    private var over: Bool { value > 1 }

    var body: some View {
        GeometryReader { geo in
            let limit = geo.size.width * LoadBar.limitShare
            ZStack(alignment: .leading) {
                Capsule().fill(Sheet.ink.opacity(0.07)).frame(height: 8)
                Capsule()
                    .fill(over ? Sheet.warn : Sheet.accent)
                    .frame(width: max(6, limit * min(value, 1.18)), height: 8)
                Rectangle().fill(Sheet.ink.opacity(0.3)).frame(width: 1, height: 14).offset(x: limit)
            }
            .frame(height: 14, alignment: .center)
        }
        .frame(height: 14)
        .accessibilityElement(children: .ignore)
        .accessibilityLabel("\(Int((value * 100).rounded())) percent by volume\(over ? ", over what the bag holds" : "")")
    }
}
