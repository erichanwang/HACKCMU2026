import SwiftUI

/// The landing page, as the load sheet for your bags.
///
/// Aircraft have solved this exact problem with paperwork for seventy years: what is
/// aboard, where, how heavy, how close to the limit. The direction contract lives in
/// `.impeccable/surfaces/spike-homescreen-swift.md`; the short version is that rules and
/// tabular figures do the work cards and progress bars were doing, because `PRODUCT.md`
/// asks this product to read like a good tape measure.
struct HomeScreen: View {
    /// Switches tabs — these are shortcuts to the tabs, not separate screens.
    var go: (AppTab) -> Void

    @State private var items: [ScannedItem] = []
    @State private var suitcases: [API.Suitcase] = []
    /// nil while the first check is still in flight.
    @State private var reachable: Bool?
    @State private var showSettings = false
    @State private var revision = Date()
    @AppStorage("serverURL") private var serverURL = API.defaultBase
    @AppStorage("authToken") private var authToken = ""

    var body: some View {
        ZStack {
            Sheet.paper.ignoresSafeArea()
            ScrollView {
                VStack(alignment: .leading, spacing: 0) {
                    masthead
                    if suitcases.isEmpty {
                        noSheet
                    } else {
                        ForEach(Array(suitcases.enumerated()), id: \.element.id) { index, bag in
                            sheet(bag, number: index + 1)
                        }
                    }
                    unmanifested
                    linkField
                }
                .padding(.horizontal, 20)
                .padding(.bottom, 28)
            }
            .safeAreaInset(edge: .bottom, spacing: 0) { scanAction }
        }
        .sheet(isPresented: $showSettings) { SettingsSheet(serverURL: $serverURL, authToken: $authToken) }
        .task { await load() }
    }

    // MARK: - Masthead

    private var masthead: some View {
        VStack(spacing: 0) {
            HStack(alignment: .firstTextBaseline) {
                Text("LOAD SHEET")
                    .font(.caption.weight(.semibold))
                    .tracking(1.6)
                Spacer()
                Text("REV \(revision, format: .dateTime.hour().minute())")
                    .font(.caption.monospaced())
                    .foregroundStyle(Sheet.ink.opacity(0.55))
                Button { showSettings = true } label: {
                    Image(systemName: "gearshape")
                        .font(.footnote.weight(.semibold))
                        .frame(width: 44, height: 44)
                        .contentShape(Rectangle())
                }
                .accessibilityLabel("Server settings")
                .padding(.trailing, -12)
            }
            .foregroundStyle(Sheet.ink)
            Rule(weight: 1.5)
            HStack(spacing: 14) {
                Text("\(suitcases.count) \(suitcases.count == 1 ? "BAG" : "BAGS")")
                Text("\(items.count) \(items.count == 1 ? "ITEM" : "ITEMS")")
                if pendingCount > 0 { Text("\(pendingCount) IDENTIFYING") }
                Spacer()
            }
            .font(.caption2.monospaced())
            .foregroundStyle(Sheet.ink.opacity(0.55))
            .padding(.top, 7)
        }
        .padding(.top, 8)
    }

    private var pendingCount: Int { items.filter { $0.labelStatus == "pending" }.count }

    // MARK: - One bag, one sheet

    private func sheet(_ bag: API.Suitcase, number: Int) -> some View {
        let aboard = contents(of: bag)
        let value = fraction(for: bag)
        return VStack(alignment: .leading, spacing: 0) {
            HStack(alignment: .lastTextBaseline) {
                Text(bag.name.uppercased())
                    .font(.system(.title, design: .default).weight(.heavy))
                    .tracking(-0.4)
                Spacer(minLength: 10)
                Text("SHEET \(number) OF \(suitcases.count)")
                    .font(.caption2.monospaced())
                    .foregroundStyle(Sheet.ink.opacity(0.5))
            }
            .padding(.top, 26)

            Text("\(bag.sizeText ?? "SIZE UNKNOWN") · \(volumeText(bag))")
                .font(.footnote.monospaced())
                .foregroundStyle(Sheet.ink.opacity(0.6))
                .padding(.top, 3)

            LoadBar(value: value)
                .padding(.top, 20)

            manifest(aboard)
        }
        .foregroundStyle(Sheet.ink)
    }

    private func manifest(_ aboard: [ScannedItem]) -> some View {
        let shown = aboard.prefix(6)
        return VStack(alignment: .leading, spacing: 0) {
            FieldLabel("MANIFEST", trailing: "\(aboard.count) \(aboard.count == 1 ? "ITEM" : "ITEMS")")
                .padding(.top, 24)
            Rule()
            if aboard.isEmpty {
                Text("Nothing aboard yet.")
                    .font(.footnote)
                    .foregroundStyle(Sheet.ink.opacity(0.6))
                    .padding(.vertical, 12)
                Rule()
            } else {
                ForEach(Array(shown.enumerated()), id: \.element.id) { index, scanned in
                    ManifestRow(position: index + 1, item: scanned)
                    Rule()
                }
                if aboard.count > shown.count {
                    Button { go(.items) } label: {
                        HStack {
                            Text("+ \(aboard.count - shown.count) MORE")
                                .font(.caption.monospaced().weight(.semibold))
                            Spacer()
                            Image(systemName: "arrow.right").font(.caption.weight(.semibold))
                        }
                        .foregroundStyle(Sheet.accent)
                        .frame(minHeight: 44)
                        .contentShape(Rectangle())
                    }
                    Rule()
                }
            }
        }
    }

    // MARK: - Loose items and empty state

    @ViewBuilder private var unmanifested: some View {
        let loose = items.filter { $0.suitcaseId == nil }
        if !loose.isEmpty {
            VStack(alignment: .leading, spacing: 0) {
                FieldLabel("UNMANIFESTED", trailing: "\(loose.count)")
                    .padding(.top, 30)
                Rule()
                ForEach(Array(loose.prefix(4).enumerated()), id: \.element.id) { index, scanned in
                    ManifestRow(position: index + 1, item: scanned)
                    Rule()
                }
                Text("Not assigned to a bag. Move them from the Items tab.")
                    .font(.caption)
                    .foregroundStyle(Sheet.ink.opacity(0.55))
                    .padding(.top, 8)
            }
            .foregroundStyle(Sheet.ink)
        }
    }

    private var noSheet: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text(reachable == false ? "NO LINK" : "NO BAG ON FILE")
                .font(.system(.title2, design: .default).weight(.heavy))
                .tracking(-0.3)
            Text(reachable == false
                 ? "Tried \(host). The Mac running the server has to be on this Wi-Fi, and the address has to match."
                 : "Scan your suitcase and it opens a sheet here. Everything you scan after that is manifested against it.")
                .font(.footnote)
                .foregroundStyle(Sheet.ink.opacity(0.65))
                .fixedSize(horizontal: false, vertical: true)
            if reachable == false {
                Button("SET SERVER ADDRESS") { showSettings = true }
                    .font(.caption.weight(.semibold))
                    .tracking(0.8)
                    .foregroundStyle(Sheet.accent)
                    .frame(minHeight: 44)
            }
        }
        .foregroundStyle(Sheet.ink)
        .padding(.top, 30)
    }

    // MARK: - Action and link

    private var scanAction: some View {
        Button { go(.scan) } label: {
            Text(suitcases.isEmpty ? "SCAN SUITCASE" : "SCAN ITEM")
                .font(.subheadline.weight(.bold))
                .tracking(1.4)
                .foregroundStyle(Sheet.paper)
                .frame(maxWidth: .infinity, minHeight: 52)
                .background(Sheet.accent)
        }
        // Plain: the system's own button chrome composites over the fill otherwise and
        // washes the bar out to a pale tint with unreadable text.
        .buttonStyle(.plain)
        .padding(.horizontal, 20)
        .padding(.top, 10)
        .padding(.bottom, 6)
        .background(alignment: .top) {
            ZStack(alignment: .top) {
                Sheet.paper
                Rule()
            }
        }
    }

    private var linkField: some View {
        Button { showSettings = true } label: {
            VStack(spacing: 0) {
                Rule().padding(.bottom, 8)
                HStack(spacing: 8) {
                    Text("LINK")
                        .font(.caption2.weight(.semibold))
                        .tracking(1.2)
                    Text(linkState)
                        .font(.caption2.monospaced().weight(.semibold))
                        .foregroundStyle(reachable == false ? Sheet.warn : Sheet.ink)
                    Text(host)
                        .font(.caption2.monospaced())
                        .foregroundStyle(Sheet.ink.opacity(0.5))
                        .lineLimit(1)
                        .truncationMode(.head)
                    Spacer(minLength: 4)
                    Image(systemName: "chevron.right").font(.caption2.weight(.semibold))
                }
                .foregroundStyle(Sheet.ink.opacity(0.75))
                .frame(minHeight: 44)
            }
        }
        .padding(.top, 22)
    }

    private var linkState: String {
        switch reachable {
        case nil: return "···"
        case true?: return "OK"
        default: return "DOWN"
        }
    }

    // MARK: - Numbers

    private func contents(of bag: API.Suitcase) -> [ScannedItem] {
        items.filter { $0.suitcaseId == bag.id }
    }

    /// Scanned bounding-box volume against the bag's own; 0 when either is unknown.
    private func fraction(for bag: API.Suitcase) -> Double {
        guard bag.dimensions.count == 3 else { return 0 }
        let capacity = Double(bag.dimensions[0] * bag.dimensions[1] * bag.dimensions[2])
        guard capacity > 0 else { return 0 }
        return contents(of: bag).reduce(0.0) { $0 + Double($1.width * $1.height * $1.depth) } / capacity
    }

    private func volumeText(_ bag: API.Suitcase) -> String {
        guard bag.dimensions.count == 3 else { return "—" }
        let litres = Double(bag.dimensions[0] * bag.dimensions[1] * bag.dimensions[2]) * 1000
        return String(format: "%.0f L", litres)
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
            revision = Date()
        } catch {
            reachable = false
        }
    }
}

// MARK: - The sheet's vocabulary

/// The one place the sheet's ink, paper and signal are named.
enum Sheet {
    static let paper = Color(red: 0.957, green: 0.949, blue: 0.925)
    static let ink = Color(red: 0.102, green: 0.122, blue: 0.169)
    static let accent = Color(red: 0.839, green: 0.329, blue: 0.122)
    static let warn = Color(red: 0.702, green: 0.443, blue: 0.031)
}

/// A ruled line. The sheet separates with rules, never with cards.
struct Rule: View {
    var weight: CGFloat = 0.75

    var body: some View {
        Rectangle()
            .fill(Sheet.ink.opacity(weight > 1 ? 0.85 : 0.22))
            .frame(height: weight)
    }
}

/// A form field's name, with its count on the right where a form puts it.
struct FieldLabel: View {
    let title: String
    var trailing: String?

    init(_ title: String, trailing: String? = nil) {
        self.title = title
        self.trailing = trailing
    }

    var body: some View {
        HStack(alignment: .firstTextBaseline) {
            Text(title).font(.caption2.weight(.semibold)).tracking(1.4)
            Spacer()
            if let trailing {
                Text(trailing).font(.caption2.monospaced()).foregroundStyle(Sheet.ink.opacity(0.55))
            }
        }
        .padding(.bottom, 7)
    }
}

/// Position number, what it is, what it measures — the three columns a manifest has.
struct ManifestRow: View {
    let position: Int
    let item: ScannedItem

    var body: some View {
        HStack(spacing: 12) {
            Text(String(format: "%02d", position))
                .font(.caption.monospaced())
                .foregroundStyle(Sheet.ink.opacity(0.45))
            Text(item.labelStatus == "pending" ? "IDENTIFYING…" : (item.label ?? "UNLABELLED").uppercased())
                .font(.footnote.weight(.medium))
                .lineLimit(1)
            Spacer(minLength: 8)
            Text(item.manifestSize)
                .font(.caption.monospaced())
                .foregroundStyle(Sheet.ink.opacity(0.6))
        }
        .foregroundStyle(Sheet.ink)
        .frame(minHeight: 38)
    }
}

/// The load against the limit: ticks at 0/50/100, a limit rule, and the reading in
/// figures. Past the limit the bar runs into the amber overrun past the rule rather
/// than stopping at it, because a bag that will not close should look like one.
struct LoadBar: View {
    /// Where the 100% rule sits across the bar's width; the rest is the overrun strip.
    static let limitShare: CGFloat = 0.82

    let value: Double

    private var percent: Int { Int((value * 100).rounded()) }
    private var over: Bool { value > 1 }

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            FieldLabel("LOAD", trailing: over ? "OVER LIMIT" : nil)
            GeometryReader { geo in
                // The limit rule sits at LoadBar.limitShare of the width; the strip past it
                // is overrun, so a bag that will not close runs into it instead of stopping.
                let limit = geo.size.width * LoadBar.limitShare
                ZStack(alignment: .leading) {
                    Rectangle().fill(Sheet.ink.opacity(0.08)).frame(height: 14)
                    Rectangle()
                        .fill(over ? Sheet.warn : Sheet.accent)
                        .frame(width: max(2, limit * min(value, 1.2)), height: 14)
                    Rectangle().fill(Sheet.ink).frame(width: 1.5, height: 22).offset(x: limit - 0.75)
                }
                .frame(height: 22, alignment: .center)
                .overlay(alignment: .topLeading) {
                    // Ticks under the marks they name: 0 at the origin, 100 under the rule.
                    ZStack(alignment: .topLeading) {
                        Text("0").offset(x: 0, y: 24)
                        Text("100").offset(x: limit - 14, y: 24)
                    }
                    .font(.caption2.monospaced())
                    .foregroundStyle(Sheet.ink.opacity(0.5))
                }
            }
            .frame(height: 40)
            Text("\(percent)% BY VOLUME")
                .font(.caption2.monospaced().weight(.semibold))
                .foregroundStyle(over ? Sheet.warn : Sheet.ink)
                .padding(.top, 2)
        }
        .accessibilityElement(children: .ignore)
        .accessibilityLabel("Load \(percent) percent by volume\(over ? ", over the limit" : "")")
    }
}

extension ScannedItem {
    /// Centimetres, no decimals, fixed width — a manifest column, not a sentence.
    var manifestSize: String {
        String(format: "%.0f×%.0f×%.0f", width * 100, depth * 100, height * 100)
    }
}
