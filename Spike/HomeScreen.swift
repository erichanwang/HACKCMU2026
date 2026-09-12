import SwiftUI

/// The landing page: your bags, what is in them, and a swipe to start scanning.
///
/// Keeps the load sheet's substance — a bag states its size, its load against the
/// limit, and every item aboard by name and measurement — and drops its hard edges.
/// White ground, soft cards, sentence case, figures still tabular because `PRODUCT.md`
/// asks this product to read like a good tape measure.
/// Direction contract: `.impeccable/surfaces/spike-homescreen-swift.md`.
struct HomeScreen: View {
    /// Switches tabs — these are shortcuts to the tabs, not separate screens.
    var go: (AppTab) -> Void

    @State private var items: [ScannedItem] = []
    @State private var suitcases: [API.Suitcase] = []
    /// nil while the first check is still in flight.
    @State private var reachable: Bool?
    @State private var showSettings = false
    @AppStorage("serverURL") private var serverURL = API.defaultBase
    @AppStorage("authToken") private var authToken = ""

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 14) {
                    summaryLine
                    if suitcases.isEmpty {
                        emptyCard
                    } else {
                        ForEach(suitcases) { bag in bagCard(bag) }
                    }
                    unmanifested
                    linkRow
                }
                .padding(.horizontal, 18)
                .padding(.bottom, 22)
            }
            .background(Sheet.paper)
            .navigationTitle("PackAR")
            .refreshable { await load() }
            .safeAreaInset(edge: .bottom, spacing: 0) {
                SwipeToScan(label: suitcases.isEmpty ? "Swipe to scan your suitcase" : "Swipe to scan an item") {
                    go(.scan)
                }
                .padding(.horizontal, 18)
                .padding(.top, 8)
                .padding(.bottom, 6)
                .background(Sheet.paper)
            }
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    Button { showSettings = true } label: { Image(systemName: "gearshape") }
                        .accessibilityLabel("Server settings")
                }
            }
        }
        .sheet(isPresented: $showSettings) { SettingsSheet(serverURL: $serverURL, authToken: $authToken) }
        .task { await load() }
    }

    // MARK: - Pieces

    private var summaryLine: some View {
        HStack(spacing: 6) {
            Text("\(suitcases.count) \(suitcases.count == 1 ? "bag" : "bags")")
            Text("·")
            Text("\(items.count) \(items.count == 1 ? "item" : "items")")
            if pendingCount > 0 {
                Text("·")
                Text("\(pendingCount) identifying").foregroundStyle(Sheet.accent)
            }
            Spacer()
        }
        .font(.subheadline.monospacedDigit())
        .foregroundStyle(Sheet.ink.opacity(0.55))
        .padding(.top, 2)
    }

    private var pendingCount: Int { items.filter { $0.labelStatus == "pending" }.count }

    private func bagCard(_ bag: API.Suitcase) -> some View {
        let aboard = contents(of: bag)
        let value = fraction(for: bag)
        let over = value > 1
        let shown = aboard.prefix(5)
        return VStack(alignment: .leading, spacing: 0) {
            HStack(alignment: .firstTextBaseline) {
                Text(bag.name)
                    .font(.title3.weight(.semibold))
                Spacer(minLength: 10)
                Text("\(percent(value))%")
                    .font(.subheadline.monospacedDigit().weight(.semibold))
                    .foregroundStyle(over ? Sheet.warn : Sheet.accent)
            }
            Text("\(bag.sizeText ?? "size unknown") · \(volumeText(bag))")
                .font(.footnote.monospacedDigit())
                .foregroundStyle(Sheet.ink.opacity(0.5))
                .padding(.top, 2)

            LoadBar(value: value)
                .padding(.top, 14)

            if aboard.isEmpty {
                Text("Nothing in this bag yet.")
                    .font(.footnote)
                    .foregroundStyle(Sheet.ink.opacity(0.5))
                    .padding(.top, 16)
            } else {
                VStack(spacing: 0) {
                    ForEach(Array(shown), id: \.id) { scanned in
                        ContentsRow(item: scanned)
                    }
                }
                .padding(.top, 10)
                if aboard.count > shown.count {
                    Button { go(.items) } label: {
                        HStack(spacing: 4) {
                            Text("\(aboard.count - shown.count) more")
                            Image(systemName: "chevron.right").font(.caption2.weight(.semibold))
                            Spacer()
                        }
                        .font(.footnote.weight(.medium))
                        .foregroundStyle(Sheet.accent)
                        .frame(minHeight: 40)
                        .contentShape(Rectangle())
                    }
                    .buttonStyle(.plain)
                }
            }
        }
        .foregroundStyle(Sheet.ink)
        .padding(18)
        .background(Sheet.card, in: RoundedRectangle(cornerRadius: 22, style: .continuous))
        .overlay(RoundedRectangle(cornerRadius: 22, style: .continuous).stroke(Sheet.hairline, lineWidth: 0.5))
    }

    @ViewBuilder private var unmanifested: some View {
        let loose = items.filter { $0.suitcaseId == nil }
        if !loose.isEmpty {
            VStack(alignment: .leading, spacing: 0) {
                HStack(alignment: .firstTextBaseline) {
                    Text("Not in a bag").font(.subheadline.weight(.semibold))
                    Spacer()
                    Text("\(loose.count)").font(.footnote.monospacedDigit()).foregroundStyle(Sheet.ink.opacity(0.5))
                }
                VStack(spacing: 0) {
                    ForEach(Array(loose.prefix(4)), id: \.id) { ContentsRow(item: $0) }
                }
                .padding(.top, 8)
                Text("Move them into a bag from the Items tab.")
                    .font(.caption)
                    .foregroundStyle(Sheet.ink.opacity(0.5))
                    .padding(.top, 6)
            }
            .foregroundStyle(Sheet.ink)
            .padding(18)
            .background(Sheet.card, in: RoundedRectangle(cornerRadius: 22, style: .continuous))
            .overlay(RoundedRectangle(cornerRadius: 22, style: .continuous).stroke(Sheet.hairline, lineWidth: 0.5))
        }
    }

    private var emptyCard: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text(reachable == false ? "No connection" : "No bag yet")
                .font(.title3.weight(.semibold))
            Text(reachable == false
                 ? "Tried \(host). The Mac running the server has to be on this Wi-Fi, and the address has to match."
                 : "Swipe below to scan your suitcase. Everything you scan after that is listed against it.")
                .font(.footnote)
                .foregroundStyle(Sheet.ink.opacity(0.6))
                .fixedSize(horizontal: false, vertical: true)
            if reachable == false {
                Button("Set the server address") { showSettings = true }
                    .font(.footnote.weight(.semibold))
                    .foregroundStyle(Sheet.accent)
                    .frame(minHeight: 44)
            }
        }
        .foregroundStyle(Sheet.ink)
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(18)
        .background(Sheet.card, in: RoundedRectangle(cornerRadius: 22, style: .continuous))
        .overlay(RoundedRectangle(cornerRadius: 22, style: .continuous).stroke(Sheet.hairline, lineWidth: 0.5))
    }

    private var linkRow: some View {
        Button { showSettings = true } label: {
            HStack(spacing: 8) {
                Circle()
                    .fill(reachable == nil ? Sheet.ink.opacity(0.3) : (reachable! ? .green : Sheet.warn))
                    .frame(width: 7, height: 7)
                Text(linkState).font(.footnote)
                Text(host).font(.footnote.monospaced()).foregroundStyle(Sheet.ink.opacity(0.4)).lineLimit(1)
                Spacer(minLength: 4)
                Image(systemName: "chevron.right").font(.caption2.weight(.semibold))
            }
            .foregroundStyle(Sheet.ink.opacity(0.6))
            .frame(minHeight: 44)
            .padding(.horizontal, 4)
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
    }

    private var linkState: String {
        switch reachable {
        case nil: return "Checking"
        case true?: return "Connected"
        default: return "Not connected"
        }
    }

    // MARK: - Numbers

    private func contents(of bag: API.Suitcase) -> [ScannedItem] {
        items.filter { $0.suitcaseId == bag.id }
    }

    private func percent(_ value: Double) -> Int { Int((value * 100).rounded()) }

    /// Scanned bounding-box volume against the bag's own; 0 when either is unknown.
    private func fraction(for bag: API.Suitcase) -> Double {
        guard bag.dimensions.count == 3 else { return 0 }
        let capacity = Double(bag.dimensions[0] * bag.dimensions[1] * bag.dimensions[2])
        guard capacity > 0 else { return 0 }
        return contents(of: bag).reduce(0.0) { $0 + Double($1.width * $1.height * $1.depth) } / capacity
    }

    private func volumeText(_ bag: API.Suitcase) -> String {
        guard bag.dimensions.count == 3 else { return "—" }
        return String(format: "%.0f L", Double(bag.dimensions[0] * bag.dimensions[1] * bag.dimensions[2]) * 1000)
    }

    private var host: String {
        let url = API.base
        guard let name = url.host else { return url.absoluteString }
        return url.port.map { "\(name):\($0)" } ?? name
    }

    /// One round trip answers both questions: whether the server is up, and what it holds.
    private func load() async {
        do {
            items = try await API.inventory()
            suitcases = (try? await API.suitcases()) ?? []
            reachable = true
        } catch {
            reachable = false
        }
    }
}

// MARK: - Vocabulary

/// The one place the app's ink, paper and signal are named.
enum Sheet {
    static let paper = Color.white
    static let card = Color(red: 0.976, green: 0.976, blue: 0.980)
    static let hairline = Color(red: 0.102, green: 0.122, blue: 0.169).opacity(0.10)
    static let ink = Color(red: 0.102, green: 0.122, blue: 0.169)
    static let accent = Color(red: 0.839, green: 0.329, blue: 0.122)
    static let warn = Color(red: 0.702, green: 0.443, blue: 0.031)
}

/// One thing in a bag: what it is, and what it measures.
struct ContentsRow: View {
    let item: ScannedItem

    var body: some View {
        HStack(spacing: 10) {
            Image(systemName: item.symbol)
                .font(.system(size: 13))
                .foregroundStyle(Sheet.ink.opacity(0.45))
                .frame(width: 20)
            Text(item.labelStatus == "pending" ? "Identifying…" : (item.label ?? "Unlabelled"))
                .font(.subheadline)
                .lineLimit(1)
            Spacer(minLength: 8)
            Text(item.manifestSize)
                .font(.caption.monospaced())
                .foregroundStyle(Sheet.ink.opacity(0.45))
        }
        .foregroundStyle(Sheet.ink.opacity(0.9))
        .frame(minHeight: 32)
    }
}

/// The load against the limit. The 100 mark sits inboard of the end, so a bag holding
/// more than it can close on runs into the overrun strip rather than stopping at full
/// and looking fine.
struct LoadBar: View {
    static let limitShare: CGFloat = 0.84

    let value: Double

    private var percent: Int { Int((value * 100).rounded()) }
    private var over: Bool { value > 1 }

    var body: some View {
        GeometryReader { geo in
            let limit = geo.size.width * LoadBar.limitShare
            ZStack(alignment: .leading) {
                Capsule().fill(Sheet.ink.opacity(0.07)).frame(height: 8)
                Capsule()
                    .fill(over ? Sheet.warn : Sheet.accent)
                    .frame(width: max(6, limit * min(value, 1.18)), height: 8)
                Rectangle().fill(Sheet.ink.opacity(0.35)).frame(width: 1, height: 14).offset(x: limit)
            }
            .frame(height: 14, alignment: .center)
        }
        .frame(height: 14)
        .accessibilityElement(children: .ignore)
        .accessibilityLabel("\(percent) percent by volume\(over ? ", over what the bag holds" : "")")
    }
}

/// Swipe the knob across to open the scanner. A tap anywhere on the track does the same
/// thing, and VoiceOver gets it as a plain button: a swipe must never be the only way.
struct SwipeToScan: View {
    let label: String
    let action: () -> Void

    @State private var offset: CGFloat = 0
    @State private var armed = false
    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    private let knob: CGFloat = 52
    private let height: CGFloat = 60

    var body: some View {
        GeometryReader { geo in
            let travel = max(1, geo.size.width - knob - 8)
            ZStack(alignment: .leading) {
                Capsule().fill(Sheet.ink.opacity(0.05))
                Capsule().stroke(Sheet.hairline, lineWidth: 0.5)

                Text(label)
                    .font(.subheadline.weight(.medium))
                    .foregroundStyle(Sheet.ink.opacity(0.45 * (1 - offset / travel)))
                    .frame(maxWidth: .infinity)
                    .padding(.leading, knob * 0.5)

                Circle()
                    .fill(Sheet.accent)
                    .overlay(
                        Image(systemName: "viewfinder")
                            .font(.system(size: 19, weight: .semibold))
                            .foregroundStyle(.white)
                    )
                    .frame(width: knob, height: knob)
                    .offset(x: 4 + offset)
                    .gesture(
                        DragGesture(minimumDistance: 1)
                            .onChanged { drag in
                                offset = min(max(0, drag.translation.width), travel)
                                armed = offset > travel * 0.6
                            }
                            .onEnded { _ in
                                if armed {
                                    action()
                                }
                                withAnimation(reduceMotion ? nil : .spring(response: 0.3, dampingFraction: 0.8)) {
                                    offset = 0
                                }
                                armed = false
                            }
                    )
            }
            .contentShape(Capsule())
            .onTapGesture { action() }
        }
        .frame(height: height)
        .accessibilityElement(children: .ignore)
        .accessibilityLabel(label)
        .accessibilityAddTraits(.isButton)
        .accessibilityAction { action() }
    }
}
