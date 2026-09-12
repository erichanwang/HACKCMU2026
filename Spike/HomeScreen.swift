import SwiftUI
import UIKit

/// The landing page: the whole screen is the control, and the gesture carries you in.
///
/// No bag list and no readouts — those live on Items and Pack, one tab away. This
/// screen exists to start the two things you open the app to do, and a drag across the
/// surface does it: the lens follows your finger, and on release it expands past the
/// edges of the screen and hands over to the tab underneath, so moving in reads as one
/// continuous motion rather than a tab swap.
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
    /// Drives the idle breathing of the lens.
    @State private var breathing = false
    /// 0 at rest; 1 once the lens has swallowed the screen on the way into a tab.
    @State private var entry: Double = 0
    @State private var entering = false
    @AppStorage("serverURL") private var serverURL = API.defaultBase
    @AppStorage("authToken") private var authToken = ""
    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    /// How far a drag has to travel before it means anything.
    private let threshold: CGFloat = 96

    private var upProgress: Double { min(1, Double(max(0, -drag.height) / threshold)) }
    private var leftProgress: Double { min(1, Double(max(0, -drag.width) / threshold)) }
    private var pull: Double { max(upProgress, leftProgress) }
    private var committed: Bool { pull >= 1 }

    var body: some View {
        ZStack {
            Sheet.paper.ignoresSafeArea()
            ambientWash.ignoresSafeArea().allowsHitTesting(false)

            VStack {
                statusRow
                Spacer()
                lens
                Spacer()
            }
            .padding(.horizontal, 22)
            .padding(.bottom, 18)
            // Everything but the lens recedes as the pull builds, so the lens is
            // plainly the thing you are moving.
            .opacity(1 - entry * 0.95)
            .scaleEffect(1 - entry * 0.06)
            .blur(radius: entry * 3)

            // The lens, grown past the screen edges: what the eye follows from this
            // page into the next one.
            ZStack {
                Circle()
                    .fill(Sheet.accent)
                    .frame(width: 150, height: 150)
                    .scaleEffect(1 + entry * 9)
                // The lens itself grows and thins out as the colour swallows the page,
                // so what you followed on the way in is still the thing you arrive at.
                Image(systemName: "viewfinder")
                    .font(.system(size: 46, weight: .light))
                    .foregroundStyle(.white)
                    .scaleEffect(1 + entry * 5.5)
                    .opacity(entry > 0 ? (1 - entry * entry) : 0)
            }
            .opacity(entry > 0 ? 1 : 0)
            .ignoresSafeArea()
            .allowsHitTesting(false)
        }
        .contentShape(Rectangle())
        .gesture(
            DragGesture(minimumDistance: 8)
                .onChanged { value in
                    guard !entering else { return }
                    drag = value.translation
                }
                .onEnded { value in
                    guard !entering else { return }
                    let up = -value.translation.height, left = -value.translation.width
                    if up >= threshold, up > left { enter(.scan) }
                    else if left >= threshold { enter(.pack) }
                    else {
                        withAnimation(reduceMotion ? nil : .spring(response: 0.34, dampingFraction: 0.7)) {
                            drag = .zero
                        }
                    }
                }
        )
        .sheet(isPresented: $showSettings) { SettingsSheet(serverURL: $serverURL, authToken: $authToken) }
        .accessibilityElement(children: .contain)
        .accessibilityAction(named: "Scan") { go(.scan) }
        .accessibilityAction(named: "Packing plan") { go(.pack) }
        .task {
            await check()
            guard !reduceMotion else { return }
            withAnimation(.easeInOut(duration: 1.9).repeatForever(autoreverses: true)) { breathing = true }
        }
        .onAppear {
            // Coming back from a tab: clear the expansion so the lens is a lens again.
            entry = 0
            entering = false
            drag = .zero
        }
    }

    // MARK: - The gesture

    /// Expand the lens past the edges, hand over to the tab, then reset behind the swap.
    private func enter(_ tab: AppTab) {
        guard !entering else { return }
        entering = true
        guard !reduceMotion else {
            go(tab)
            entering = false
            return
        }
        withAnimation(.easeInOut(duration: 0.62)) {
            entry = 1
            drag = .zero
        }
        Task {
            // Hand over just before the lens finishes filling the screen, so the tab
            // underneath is already there when the colour clears.
            try? await Task.sleep(for: .milliseconds(560))
            go(tab)
            try? await Task.sleep(for: .milliseconds(200))
            entry = 0
            entering = false
        }
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

    /// The lens: breathing at rest, following the finger under a pull, and brightening
    /// as it comes to the point where releasing means something.
    private var lens: some View {
        VStack(spacing: 24) {
            ZStack {
                // Two haloes, offset in phase, so the idle state is never quite still.
                Circle()
                    .fill(Sheet.accent.opacity(0.06))
                    .frame(width: 190, height: 190)
                    .scaleEffect((breathing ? 1.10 : 0.90) + 0.12 * pull)
                Circle()
                    .fill(Sheet.accent.opacity(0.10 + 0.26 * pull))
                    .frame(width: 138, height: 138)
                    .scaleEffect((breathing ? 0.94 : 1.07) + 0.16 * pull)
                Image(systemName: "viewfinder")
                    .font(.system(size: 46, weight: .light))
                    .foregroundStyle(Sheet.accent)
                    .opacity(0.6 + 0.4 * pull)
                    .rotationEffect(.degrees(pull * 90 + (breathing ? 3 : -3)))
                    .scaleEffect(breathing ? 1.03 : 0.98)
            }
            .overlay(alignment: .top) {
                Image(systemName: "chevron.up")
                    .font(.footnote.weight(.semibold))
                    .foregroundStyle(Sheet.accent.opacity(0.3 + 0.7 * upProgress))
                    .offset(y: -34 - 10 * upProgress + (breathing ? -5 : 3))
                    .opacity(1 - leftProgress)
            }

            VStack(spacing: 5) {
                Text(committed ? "Release" : "Swipe up to scan")
                    .font(.title3.weight(.medium))
                    .foregroundStyle(Sheet.ink)
                    .contentTransition(.opacity)
                Text("or tap the lens")
                    .font(.footnote)
                    .foregroundStyle(Sheet.ink.opacity(0.4))
                    .opacity(1 - pull)
            }
        }
        // Follows the finger, and leads it slightly on the way out.
        .offset(x: drag.width * 0.4, y: drag.height * 0.5)
        .scaleEffect(1 + 0.05 * pull)
        // The tap target is the lens, not the whole screen: a stray tap anywhere
        // should not throw you into the camera.
        .contentShape(Circle())
        .onTapGesture { enter(.scan) }
    }

    /// A slow wash of warm light behind everything: two soft fields drifting on long,
    /// mismatched cycles so the page is never quite static and never busy either. No
    /// marks, no geometry — just the ground breathing. Reduce Motion holds it still.
    private var ambientWash: some View {
        TimelineView(.animation(paused: reduceMotion)) { context in
            let t = reduceMotion ? 0 : context.date.timeIntervalSinceReferenceDate
            GeometryReader { geo in
                let w = geo.size.width, h = geo.size.height
                ZStack {
                    wash(tint: Sheet.accent.opacity(0.16), diameter: w * 1.25)
                        .position(x: w * (0.5 + 0.26 * cos(t / 7)),
                                  y: h * (0.40 + 0.16 * sin(t / 5.5)))
                    wash(tint: Sheet.accent.opacity(0.09), diameter: w * 1.05)
                        .position(x: w * (0.5 - 0.30 * sin(t / 9)),
                                  y: h * (0.58 + 0.19 * cos(t / 7.5)))
                }
                .blur(radius: 50)
            }
        }
    }

    private func wash(tint: Color, diameter: CGFloat) -> some View {
        Circle()
            .fill(RadialGradient(colors: [tint, tint.opacity(0)], center: .center,
                                 startRadius: 0, endRadius: diameter / 2))
            .frame(width: diameter, height: diameter)
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
///
/// Paper, card, ink and hairline resolve per trait collection, so one definition covers
/// both appearances and every screen follows the toggle without plumbing. The accent is
/// the user's, read from `UserDefaults`; `ContentView` holds the same key so changing it
/// re-evaluates the tree that reads these.
enum Sheet {
    static var paper: Color { adaptive(light: 1, 1, 1, dark: 0.055, 0.063, 0.075) }
    static var card: Color { adaptive(light: 0.976, 0.976, 0.980, dark: 0.102, 0.114, 0.133) }
    static var ink: Color { adaptive(light: 0.102, 0.122, 0.169, dark: 0.949, 0.957, 0.969) }
    static var hairline: Color { ink.opacity(0.12) }
    static var warn: Color { adaptive(light: 0.702, 0.443, 0.031, dark: 0.886, 0.647, 0.235) }

    /// The undertone. One saturated colour carries the whole app, so it is the only
    /// thing worth letting someone change.
    static let accents: [(name: String, color: Color)] = [
        ("Orange", Color(red: 0.839, green: 0.329, blue: 0.122)),
        ("Blue", Color(red: 0.098, green: 0.435, blue: 0.839)),
        ("Green", Color(red: 0.078, green: 0.529, blue: 0.357)),
        ("Violet", Color(red: 0.424, green: 0.310, blue: 0.812)),
        ("Pink", Color(red: 0.831, green: 0.243, blue: 0.478)),
        ("Graphite", Color(red: 0.231, green: 0.255, blue: 0.294)),
    ]

    static var accentName: String { UserDefaults.standard.string(forKey: "accentName") ?? "Orange" }

    static var accent: Color {
        accents.first { $0.name == accentName }?.color ?? accents[0].color
    }

    private static func adaptive(light lr: CGFloat, _ lg: CGFloat, _ lb: CGFloat,
                                 dark dr: CGFloat, _ dg: CGFloat, _ db: CGFloat) -> Color {
        Color(UIColor { traits in
            traits.userInterfaceStyle == .dark
                ? UIColor(red: dr, green: dg, blue: db, alpha: 1)
                : UIColor(red: lr, green: lg, blue: lb, alpha: 1)
        })
    }
}

/// Light, dark, or whatever the phone is set to.
enum Appearance: String, CaseIterable {
    case system, light, dark

    var scheme: ColorScheme? {
        switch self {
        case .system: return nil
        case .light: return .light
        case .dark: return .dark
        }
    }

    var label: String { rawValue.capitalized }
}
