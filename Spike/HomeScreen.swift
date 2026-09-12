import SwiftUI

/// The landing page: the whole screen is the control.
///
/// No bag list and no readouts here — those live on Items and Pack, one tab away.
/// This screen exists to start the two things you actually came to do, and it starts
/// them with a gesture across the whole surface rather than a widget in the corner of
/// it. Swipe up to scan, swipe left for the plan.
///
/// A swipe is never the only route: a tap scans, and VoiceOver gets both as named
/// actions, because a drag-only interface locks out anyone who cannot drag.
struct HomeScreen: View {
    /// Switches tabs — this screen starts the work, the tabs hold it.
    var go: (AppTab) -> Void

    /// nil while the first check is still in flight.
    @State private var reachable: Bool?
    @State private var showSettings = false
    @State private var drag: CGSize = .zero
    @AppStorage("serverURL") private var serverURL = API.defaultBase
    @AppStorage("authToken") private var authToken = ""
    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    /// How far a drag has to travel before it means anything.
    private let threshold: CGFloat = 96

    /// 0 to 1 towards the scan gesture (up) and the plan gesture (left).
    private var upProgress: Double { min(1, Double(max(0, -drag.height) / threshold)) }
    private var leftProgress: Double { min(1, Double(max(0, -drag.width) / threshold)) }
    private var committed: Bool { upProgress >= 1 || leftProgress >= 1 }

    var body: some View {
        ZStack {
            Sheet.paper.ignoresSafeArea()

            VStack {
                statusRow
                Spacer()
                gestureTarget
                Spacer()
                planHint
            }
            .padding(.horizontal, 22)
            .padding(.bottom, 18)
        }
        .contentShape(Rectangle())
        .gesture(
            DragGesture(minimumDistance: 8)
                .onChanged { drag = $0.translation }
                .onEnded { value in
                    let up = -value.translation.height, left = -value.translation.width
                    withAnimation(reduceMotion ? nil : .spring(response: 0.32, dampingFraction: 0.82)) {
                        drag = .zero
                    }
                    if up >= threshold, up > left { go(.scan) }
                    else if left >= threshold { go(.pack) }
                }
        )
        .onTapGesture { go(.scan) }
        .sheet(isPresented: $showSettings) { SettingsSheet(serverURL: $serverURL, authToken: $authToken) }
        .accessibilityElement(children: .contain)
        .accessibilityAction(named: "Scan") { go(.scan) }
        .accessibilityAction(named: "Packing plan") { go(.pack) }
        .task { await check() }
    }

    // MARK: - Pieces

    private var statusRow: some View {
        HStack(spacing: 8) {
            Circle()
                .fill(reachable == nil ? Sheet.ink.opacity(0.25) : (reachable! ? .green : Sheet.warn))
                .frame(width: 7, height: 7)
            Text(linkState)
                .font(.footnote)
                .foregroundStyle(Sheet.ink.opacity(0.55))
            Spacer()
            Button { showSettings = true } label: {
                Image(systemName: "gearshape")
                    .font(.body)
                    .foregroundStyle(Sheet.ink.opacity(0.55))
                    .frame(width: 44, height: 44)
                    .contentShape(Rectangle())
            }
            .accessibilityLabel("Server settings")
            .padding(.trailing, -10)
        }
        .padding(.top, 4)
    }

    /// The middle of the screen follows the finger: the surface itself is the control,
    /// so it has to move like one.
    private var gestureTarget: some View {
        VStack(spacing: 22) {
            ZStack {
                Circle()
                    .fill(Sheet.accent.opacity(0.10 + 0.25 * upProgress))
                    .frame(width: 132 + 26 * upProgress, height: 132 + 26 * upProgress)
                Image(systemName: "viewfinder")
                    .font(.system(size: 44, weight: .light))
                    .foregroundStyle(Sheet.accent)
                    .opacity(0.55 + 0.45 * upProgress)
            }
            .overlay(alignment: .top) {
                Image(systemName: "chevron.up")
                    .font(.footnote.weight(.semibold))
                    .foregroundStyle(Sheet.accent.opacity(0.35 + 0.65 * upProgress))
                    .offset(y: -26 - 8 * upProgress)
            }

            VStack(spacing: 5) {
                Text(committed ? "Release to scan" : "Swipe up to scan")
                    .font(.title3.weight(.medium))
                    .foregroundStyle(Sheet.ink)
                    .contentTransition(.opacity)
                Text("or tap anywhere")
                    .font(.footnote)
                    .foregroundStyle(Sheet.ink.opacity(0.4))
                    .opacity(1 - upProgress)
            }
        }
        .offset(x: drag.width * 0.35, y: drag.height * 0.45)
        .scaleEffect(1 + 0.03 * upProgress)
    }

    private var planHint: some View {
        HStack(spacing: 7) {
            Image(systemName: "chevron.left")
                .font(.caption2.weight(.semibold))
            Text("Swipe left for your packing plan")
                .font(.footnote)
        }
        .foregroundStyle(Sheet.ink.opacity(0.3 + 0.5 * leftProgress))
        .frame(maxWidth: .infinity)
        .offset(x: drag.width * 0.2)
    }

    private var linkState: String {
        switch reachable {
        case nil: return "Checking the server"
        case true?: return "Connected"
        default: return "Not connected — tap the gear"
        }
    }

    private func check() async {
        reachable = (try? await API.suitcases()) != nil
    }
}

/// The one place the app's ink, paper and signal are named.
enum Sheet {
    static let paper = Color.white
    static let card = Color(red: 0.976, green: 0.976, blue: 0.980)
    static let hairline = Color(red: 0.102, green: 0.122, blue: 0.169).opacity(0.10)
    static let ink = Color(red: 0.102, green: 0.122, blue: 0.169)
    static let accent = Color(red: 0.839, green: 0.329, blue: 0.122)
    static let warn = Color(red: 0.702, green: 0.443, blue: 0.031)
}
