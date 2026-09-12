import SwiftUI

/// The app's landing page: what PackAR is, whether the server is actually reachable,
/// what has been scanned so far, and one tap into each of the other tabs.
///
/// The reachability dot is the point of this screen. The server is a Mac on the same
/// Wi-Fi whose address changes with its DHCP lease, and until now the first sign of a
/// stale address was a scan failing halfway through a demo.
struct HomeScreen: View {
    /// Switches tabs — these buttons are shortcuts to the tabs, not separate screens.
    var go: (AppTab) -> Void

    @State private var items: [ScannedItem] = []
    @State private var suitcases: [API.Suitcase] = []
    /// nil while the first check is still in flight.
    @State private var reachable: Bool?
    @State private var showSettings = false
    @AppStorage("serverURL") private var serverURL = API.defaultBase
    @AppStorage("authToken") private var authToken = ""

    var body: some View {
        ZStack {
            LinearGradient(colors: [Color(red: 0.16, green: 0.14, blue: 0.42), .black],
                           startPoint: .top, endPoint: .bottom)
                .ignoresSafeArea()
            ScrollView {
                VStack(alignment: .leading, spacing: 28) {
                    serverPill
                    masthead
                    stats
                    actions
                }
                .padding(.horizontal, 24)
                .padding(.top, 8)
                .padding(.bottom, 32)
            }
        }
        .environment(\.colorScheme, .dark)
        .tint(.indigo)
        .sheet(isPresented: $showSettings) { SettingsSheet(serverURL: $serverURL, authToken: $authToken) }
        .task { await load() }
    }

    // MARK: - Pieces

    private var serverPill: some View {
        HStack(spacing: 8) {
            Circle()
                .fill(reachable == nil ? .gray : (reachable! ? .green : .orange))
                .frame(width: 8, height: 8)
            Text(reachable == nil ? "Checking server…" : (reachable! ? "Server connected" : "Server unreachable"))
                .font(.caption.weight(.medium))
            Text(API.base.host ?? API.base.absoluteString)
                .font(.caption.monospaced())
                .foregroundStyle(.secondary)
            Spacer()
            Button { showSettings = true } label: {
                Image(systemName: "gearshape.fill").font(.callout.weight(.semibold))
            }
            .accessibilityLabel("Server settings")
        }
        .padding(.horizontal, 14)
        .padding(.vertical, 10)
        .background(.ultraThinMaterial, in: Capsule())
    }

    private var masthead: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text("PackAR")
                .font(.system(size: 48, weight: .bold, design: .rounded))
                .foregroundStyle(.white)
            Text("Scan your stuff, get a packing plan, then see exactly where everything goes — in AR.")
                .font(.callout)
                .foregroundStyle(.white.opacity(0.7))
                .fixedSize(horizontal: false, vertical: true)
        }
        .padding(.top, 12)
    }

    private var stats: some View {
        HStack(spacing: 12) {
            stat("\(items.count)", items.count == 1 ? "item scanned" : "items scanned", "shippingbox.fill")
            stat("\(suitcases.count)", suitcases.count == 1 ? "suitcase" : "suitcases", "suitcase.fill")
        }
    }

    private func stat(_ value: String, _ caption: String, _ symbol: String) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            Image(systemName: symbol).font(.title3).foregroundStyle(.indigo)
            Text(value).font(.system(.largeTitle, design: .rounded).weight(.bold).monospacedDigit())
            Text(caption).font(.caption).foregroundStyle(.secondary)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(16)
        .background(.ultraThinMaterial, in: RoundedRectangle(cornerRadius: 20, style: .continuous))
    }

    private var actions: some View {
        VStack(spacing: 12) {
            Button { go(.scan) } label: {
                Label("Scan an item", systemImage: "viewfinder")
                    .font(.headline)
                    .frame(maxWidth: .infinity)
                    .padding(.vertical, 6)
            }
            .buttonStyle(.borderedProminent)
            .controlSize(.large)

            HStack(spacing: 12) {
                secondary("Items", "list.bullet") { go(.items) }
                secondary("Pack", "cube.transparent") { go(.pack) }
            }
        }
    }

    private func secondary(_ title: String, _ symbol: String, action: @escaping () -> Void) -> some View {
        Button(action: action) {
            Label(title, systemImage: symbol)
                .font(.subheadline.weight(.semibold))
                .frame(maxWidth: .infinity)
                .padding(.vertical, 12)
        }
        .buttonStyle(.bordered)
        .tint(.white)
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
