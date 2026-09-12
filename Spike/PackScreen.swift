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
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    /// Raised while the AR overlay owns the camera, so the Scan tab stands its own
    /// ARSession down. iOS runs one at a time.
    @Binding var arActive: Bool

    @State private var suitcases: [API.Suitcase] = []
    @State private var items: [ScannedItem] = []
    @State private var status = ""
    /// The suitcase currently being solved, so only its own button spins.
    @State private var packing: String?
    /// 0 open, 1 shut. The plan does not open until the bag does, so pressing play
    /// always reads as one motion: zip it up, then show what is inside.
    @State private var zip: Double = 0
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
            .navigationBarTitleDisplayMode(.inline)
            .refreshable { await load() }
            .navigationDestination(isPresented: $showingPlan) {
                if let plan {
                    PlanSheet(plan: plan, notice: planNotice, scans: scansByID, arActive: $arActive)
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

            HStack(spacing: 14) {
                // The zip only runs while this bag is actually being solved; the rest of
                // the time the same line is the load against the limit.
                Group {
                    if packing == bag.id {
                        ZipperBar(progress: zip)
                    } else {
                        LoadBar(value: value)
                    }
                }
                Button { pack(bag) } label: {
                    Image(systemName: "play.fill")
                        .font(.subheadline)
                        .foregroundStyle(.white)
                        .frame(width: 44, height: 44)
                        .background(aboard.isEmpty ? Sheet.ink.opacity(0.18) : Sheet.accent, in: Circle())
                }
                .buttonStyle(.plain)
                .disabled(aboard.isEmpty || packing != nil)
                .accessibilityLabel("Pack \(bag.name)")
            }
            .padding(.top, 18)

            Button { editing = bag } label: {
                Label("Add items", systemImage: "plus")
                    .font(.subheadline.weight(.medium))
                    .frame(maxWidth: .infinity, minHeight: 44)
                    .background(Sheet.card, in: Capsule())
                    .overlay(Capsule().stroke(Sheet.hairline, lineWidth: 0.5))
            }
            .buttonStyle(.plain)
            .foregroundStyle(Sheet.ink)
            .padding(.top, 14)

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

    /// The scans behind the placements, by item id. With these the plan draws each
    /// item's real recorded surface fitted into the box the solver gave it — so a soft
    /// item the solver squeezed or folded is visibly squeezed, not a tidy rectangle.
    private var scansByID: [String: ScannedItem] {
        Dictionary(items.map { ($0.id, $0) }, uniquingKeysWith: { a, _ in a })
    }

    /// Placements the solver fitted into less room than the item was scanned at. The
    /// server already allows for this (physics/prepack.py computes a compression
    /// allowance and hands soft items fold options); this is where it becomes visible.
    private func squeezedCount(_ plan: PackingPlan) -> Int {
        let scans = scansByID
        return plan.placements.filter { placement in
            guard let scan = scans[placement.itemID] else { return false }
            let scanned = Double(scan.width * scan.height * scan.depth)
            let placed = Double(placement.size.x * placement.size.y * placement.size.z)
            return scanned > 0 && placed < scanned * 0.95
        }.count
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
        zip = 0
        Task {
            defer { packing = nil; zip = 0 }
            // The solve runs while the zip closes; whichever finishes second decides
            // when the plan opens, and the bag is never shown open mid-zip.
            async let request = API.plan(suitcaseId: bag.id)
            if reduceMotion {
                zip = 1
            } else {
                // One frame open before it starts closing. A view inserted and animated
                // in the same pass has no previous value to animate from, so the bar
                // would mount already shut and the zip would never be seen.
                try? await Task.sleep(for: .milliseconds(60))
                withAnimation(.easeInOut(duration: 0.9)) { zip = 1 }
                try? await Task.sleep(for: .milliseconds(920))
            }
            do {
                let (solved, unpacked, pendingLabels) = try await request
                plan = solved
                planNotice = nil
                status = unpacked.isEmpty ? "" : "Didn't fit: \(unpacked.map(\.label).joined(separator: ", "))"
                let squeezed = squeezedCount(solved)
                if squeezed > 0 {
                    let note = "\(squeezed) \(squeezed == 1 ? "item is" : "items are") folded or squeezed to fit"
                    status += status.isEmpty ? note : " · " + note
                }
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


/// The load bar while the solver is running: a zip closing along the bag.
///
/// Teeth ahead of the pull sit splayed and grey, teeth behind it mesh and take the
/// accent, and the pull runs the length on a loop until the plan comes back. It is the
/// same 14pt line the LoadBar occupies, so the card does not jump when it swaps in.
/// Reduce Motion parks the pull halfway rather than running it.
struct ZipperBar: View, Animatable {
    /// 0 fully open, 1 fully shut. Driven by the caller so the zip is one deliberate
    /// motion that finishes, not a spinner that loops while something else happens.
    var progress: Double

    /// Without this the Canvas would jump straight from open to shut: SwiftUI
    /// interpolates animatable data, not arbitrary state a body happens to read.
    var animatableData: Double {
        get { progress }
        set { progress = newValue }
    }

    var body: some View {
        Canvas { ctx, size in
            let phase = max(0, min(1, progress))
            let midY = size.height / 2
            let pullX = size.width * phase
            let pitch: CGFloat = 7

            var x: CGFloat = 1
            while x < size.width - 1 {
                let closed = x < pullX - 3
                let spread: CGFloat = closed ? 1.6 : 4.6
                let colour = closed ? Sheet.accent : Sheet.ink.opacity(0.2)
                for side in [CGFloat(-1), CGFloat(1)] {
                    let rect = CGRect(x: x, y: midY + side * spread - 1.5, width: 4.2, height: 3)
                    ctx.fill(Path(roundedRect: rect, cornerRadius: 1.2), with: .color(colour))
                }
                x += pitch
            }

            // The pull itself, with its tab hanging below the line.
            let body = CGRect(x: pullX - 4.5, y: midY - 6.5, width: 9, height: 13)
            ctx.fill(Path(roundedRect: body, cornerRadius: 3), with: .color(Sheet.accent))
            let tab = CGRect(x: pullX - 1.8, y: midY + 5, width: 3.6, height: 7)
            ctx.fill(Path(roundedRect: tab, cornerRadius: 1.8), with: .color(Sheet.accent.opacity(0.7)))
        }
        .frame(height: 14)
        .accessibilityElement(children: .ignore)
        .accessibilityLabel("Packing this bag")
    }
}
