import SwiftUI

/// The landing page: a viewfinder.
///
/// PackAR is a camera held over an open suitcase, so its first screen is framed like
/// one — corner marks, chrome at the edges, the subject in the middle. The subject
/// here is what the app already knows: how many bags, how full each one is, and
/// whether the server that does the solving can actually be reached.
///
/// The marks are drawn rather than laid over a live feed. A second ARSession here
/// would take the camera from the Scan tab (iOS runs one at a time), ask for camera
/// permission before the app has explained itself, and burn battery to decorate a
/// screen whose job is orientation. `PRODUCT.md` is explicit that nothing is gated
/// behind the camera; that includes the way in.
struct HomeScreen: View {
    /// Switches tabs — these are shortcuts to the tabs, not separate screens.
    var go: (AppTab) -> Void

    @State private var items: [ScannedItem] = []
    @State private var suitcases: [API.Suitcase] = []
    /// nil while the first check is still in flight.
    @State private var reachable: Bool?
    @State private var showSettings = false
    /// The one authored moment: the marks settle inward once, the way a camera
    /// acquires focus. Not a loop, and not on every section.
    @State private var framed = false
    @AppStorage("serverURL") private var serverURL = API.defaultBase
    @AppStorage("authToken") private var authToken = ""
    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    private var ground: Color { Color(red: 0.04, green: 0.04, blue: 0.05) }
    private var rule: Color { .white.opacity(0.14) }

    var body: some View {
        ZStack {
            ground.ignoresSafeArea()
            ScrollView {
                VStack(alignment: .leading, spacing: 0) {
                    chrome
                    viewfinder
                    primaryAction
                    bags
                    elsewhere
                }
                .padding(.bottom, 28)
            }
        }
        .environment(\.colorScheme, .dark)
        .sheet(isPresented: $showSettings) { SettingsSheet(serverURL: $serverURL, authToken: $authToken) }
        .task {
            await load()
            guard !framed else { return }
            if reduceMotion { framed = true } else { withAnimation(.easeOut(duration: 0.55)) { framed = true } }
        }
    }

    // MARK: - Chrome

    /// Camera-app chrome: what the app is connected to, and the way to change it.
    private var chrome: some View {
        HStack(spacing: 9) {
            Circle()
                .fill(reachable == nil ? .white.opacity(0.5) : (reachable! ? .green : .orange))
                .frame(width: 7, height: 7)
            Text(reachable == nil ? "Connecting" : (reachable! ? "Ready" : "No server"))
                .font(.footnote.weight(.semibold))
                .foregroundStyle(.white)
            Text(host)
                .font(.footnote.monospacedDigit())
                .foregroundStyle(.white.opacity(0.6))
                .lineLimit(1)
                .truncationMode(.head)
            Spacer(minLength: 8)
            Button { showSettings = true } label: {
                Image(systemName: "gearshape")
                    .font(.body)
                    .foregroundStyle(.white.opacity(0.8))
                    .frame(width: 44, height: 44)
                    .contentShape(Rectangle())
            }
            .accessibilityLabel("Server settings")
        }
        .padding(.leading, 20)
        .padding(.trailing, 8)
        .padding(.top, 4)
        .accessibilityElement(children: .combine)
    }

    private var host: String {
        let url = API.base
        guard let name = url.host else { return url.absoluteString }
        return url.port.map { "\(name):\($0)" } ?? name
    }

    // MARK: - The frame

    private var viewfinder: some View {
        ZStack {
            ViewfinderFrame(inset: framed ? 0 : 22)
                .stroke(.white.opacity(0.5), style: StrokeStyle(lineWidth: 2, lineCap: .round))
            VStack(spacing: 8) {
                Text("PackAR")
                    .font(.system(.largeTitle, design: .rounded).weight(.semibold))
                    .foregroundStyle(.white)
                Text("Scan the bag, scan what goes in it,\nfollow the plan into the real suitcase.")
                    .font(.subheadline)
                    .multilineTextAlignment(.center)
                    .foregroundStyle(.white.opacity(0.65))
                Text(summary)
                    .font(.footnote.monospacedDigit())
                    .foregroundStyle(.white.opacity(0.5))
                    .padding(.top, 6)
            }
            .padding(.horizontal, 30)
        }
        .frame(height: 232)
        .padding(.horizontal, 24)
        .padding(.top, 18)
    }

    /// Counts, not adjectives. Pending labels are named because they are the one
    /// thing that changes under you while you look at the screen.
    private var summary: String {
        guard !items.isEmpty || !suitcases.isEmpty else { return "Nothing scanned yet" }
        var parts = ["\(suitcases.count) \(suitcases.count == 1 ? "bag" : "bags")",
                     "\(items.count) \(items.count == 1 ? "item" : "items")"]
        let pending = items.filter { $0.labelStatus == "pending" }.count
        if pending > 0 { parts.append("\(pending) labelling") }
        return parts.joined(separator: "  ·  ")
    }

    // MARK: - Actions

    private var primaryAction: some View {
        Button { go(.scan) } label: {
            Label(suitcases.isEmpty ? "Scan your suitcase" : "Scan an item", systemImage: "viewfinder")
                .font(.callout.weight(.semibold))
                .foregroundStyle(ground)
                .frame(maxWidth: .infinity, minHeight: 52)
        }
        .background(.white, in: Capsule())
        .padding(.horizontal, 24)
        .padding(.top, 22)
    }

    private var elsewhere: some View {
        HStack(spacing: 0) {
            link("Every item", count: items.count) { go(.items) }
            Rectangle().fill(rule).frame(width: 1, height: 24)
            link("Packing plan", count: nil) { go(.pack) }
        }
        .padding(.horizontal, 24)
        .padding(.top, 22)
    }

    private func link(_ title: String, count: Int?, action: @escaping () -> Void) -> some View {
        Button(action: action) {
            HStack(spacing: 6) {
                Text(title).font(.subheadline.weight(.medium))
                if let count { Text("\(count)").font(.subheadline.monospacedDigit()).foregroundStyle(.white.opacity(0.5)) }
            }
            .frame(maxWidth: .infinity, minHeight: 44)
            .contentShape(Rectangle())
        }
        .foregroundStyle(.white.opacity(0.85))
    }

    // MARK: - Bags

    private var bags: some View {
        VStack(alignment: .leading, spacing: 0) {
            Text("YOUR BAGS")
                .font(.caption2.weight(.semibold))
                .tracking(0.8)
                .foregroundStyle(.white.opacity(0.45))
                .padding(.bottom, 12)

            if suitcases.isEmpty {
                Text(reachable == false
                     ? "Can't reach \(host). The Mac running the server has to be on this Wi-Fi, and its address has to match."
                     : "No suitcase scanned yet. Scan one and it appears here with how much of it is spoken for.")
                    .font(.subheadline)
                    .foregroundStyle(.white.opacity(0.6))
                    .fixedSize(horizontal: false, vertical: true)
                // The recovery belongs next to the problem, not only behind the gear.
                if reachable == false {
                    Button("Set the server address") { showSettings = true }
                        .font(.subheadline.weight(.semibold))
                        .frame(minHeight: 44)
                        .padding(.top, 4)
                }
            } else {
                ForEach(suitcases) { bag in
                    Rectangle().fill(rule).frame(height: 1)
                    bagRow(bag)
                }
                Rectangle().fill(rule).frame(height: 1)
                Text("Volume counts each item's scanned bounding box — a bag reads past 100% before the solver rules on what fits.")
                    .font(.caption)
                    .foregroundStyle(.white.opacity(0.45))
                    .fixedSize(horizontal: false, vertical: true)
                    .padding(.top, 12)
            }
        }
        .padding(.horizontal, 24)
        .padding(.top, 34)
    }

    private func bagRow(_ bag: API.Suitcase) -> some View {
        let packed = items.filter { $0.suitcaseId == bag.id }
        let fraction = fill(bag, packed: packed)
        let over = (fraction ?? 0) > 1
        return VStack(alignment: .leading, spacing: 9) {
            HStack(alignment: .firstTextBaseline) {
                Text(bag.name)
                    .font(.body.weight(.medium))
                    .foregroundStyle(.white)
                Spacer(minLength: 12)
                Text(bag.sizeText ?? "size unknown")
                    .font(.footnote.monospacedDigit())
                    .foregroundStyle(.white.opacity(0.55))
            }
            GeometryReader { geo in
                ZStack(alignment: .leading) {
                    Capsule().fill(.white.opacity(0.12))
                    Capsule()
                        .fill(over ? Color.orange : .white.opacity(0.85))
                        .frame(width: geo.size.width * min(fraction ?? 0, 1))
                }
            }
            .frame(height: 5)
            HStack(spacing: 6) {
                if let fraction {
                    Text("\(Int((fraction * 100).rounded()))% by volume")
                        .font(.footnote.monospacedDigit())
                        .foregroundStyle(over ? .orange : .white.opacity(0.7))
                    if over {
                        Text("· more than the bag holds")
                            .font(.footnote)
                            .foregroundStyle(.orange)
                    }
                }
                Spacer(minLength: 6)
                Text("\(packed.count) \(packed.count == 1 ? "item" : "items")")
                    .font(.footnote.monospacedDigit())
                    .foregroundStyle(.white.opacity(0.55))
            }
        }
        .padding(.vertical, 14)
        .accessibilityElement(children: .combine)
    }

    /// Scanned bounding-box volume against the bag's own, or nil when either is unknown.
    private func fill(_ bag: API.Suitcase, packed: [ScannedItem]) -> Double? {
        guard bag.dimensions.count == 3 else { return nil }
        let capacity = Double(bag.dimensions[0] * bag.dimensions[1] * bag.dimensions[2])
        guard capacity > 0 else { return nil }
        let used = packed.reduce(0.0) { $0 + Double($1.width * $1.height * $1.depth) }
        return used / capacity
    }

    // MARK: - Server

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

/// Four corner marks, the way a camera app frames its subject. `inset` animates so
/// they can settle inward once on first appearance.
private struct ViewfinderFrame: Shape {
    var inset: CGFloat
    var arm: CGFloat = 40

    var animatableData: CGFloat {
        get { inset }
        set { inset = newValue }
    }

    func path(in rect: CGRect) -> Path {
        let r = rect.insetBy(dx: inset, dy: inset)
        let corners: [(CGPoint, CGFloat, CGFloat)] = [
            (CGPoint(x: r.minX, y: r.minY), 1, 1),
            (CGPoint(x: r.maxX, y: r.minY), -1, 1),
            (CGPoint(x: r.minX, y: r.maxY), 1, -1),
            (CGPoint(x: r.maxX, y: r.maxY), -1, -1),
        ]
        var path = Path()
        for (corner, dx, dy) in corners {
            path.move(to: CGPoint(x: corner.x, y: corner.y + arm * dy))
            path.addLine(to: corner)
            path.addLine(to: CGPoint(x: corner.x + arm * dx, y: corner.y))
        }
        return path
    }
}
