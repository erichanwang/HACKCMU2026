import Foundation
// PACKAR_TEST_ONLY (set by tests/swift/rings/run.sh) compiles just the pure layout below on Linux.
#if !PACKAR_TEST_ONLY
import SwiftUI
#endif

/// Where the n-th inventory item sits: ring 1 seats 6, ring 2 seats 12, ring k seats 6k, filled
/// inside out. `angle` (radians, before any rotation) spreads a ring's items evenly, so a
/// half-full outer ring never bunches on one side.
struct RingSlot: Equatable {
    let ring: Int
    let angle: Double
}

func ringSlots(count: Int) -> [RingSlot] {
    var slots: [RingSlot] = []
    var ring = 1, left = count
    while left > 0 {
        let n = min(left, 6 * ring)
        for i in 0..<n { slots.append(RingSlot(ring: ring, angle: 2 * .pi * Double(i) / Double(n))) }
        left -= n
        ring += 1
    }
    return slots
}

/// Ring k turns once every `30 + 15k` seconds, neighbours in opposite directions; slow enough to
/// read a label off a moving circle. Radians at time `t`.
func ringSpin(ring: Int, at t: Double) -> Double {
    (ring.isMultiple(of: 2) ? -1 : 1) * t * 2 * .pi / (30 + 15 * Double(ring))
}

#if !PACKAR_TEST_ONLY
/// The inventory as concentric rings of circles, one per item, drifting over the camera view.
/// Tapping a circle makes it the panel's current item (so ItemEditor shows and edits it);
/// holding one opens `menu` (put in a suitcase, delete); tapping the backdrop closes the
/// overlay. Reduce Motion freezes the rings in place.
/// ponytail: past ~36 items on a phone the circles overlap; the ring cap would need a
/// zoom or a page if a real inventory gets that big.
struct InventoryRings<MenuContent: View>: View {
    let items: [ScannedItem]
    var select: (ScannedItem) -> Void
    var dismiss: () -> Void
    /// The long-press menu for one circle; the caller owns the suitcase list and the actions.
    @ViewBuilder var menu: (ScannedItem) -> MenuContent
    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    var body: some View {
        GeometryReader { geo in
            let slots = ringSlots(count: items.count)
            let rings = slots.last?.ring ?? 0
            // Centred in the part of the screen the bottom panel doesn't cover.
            let center = CGPoint(x: geo.size.width / 2, y: geo.size.height * 0.38)
            let reach = min(geo.size.width / 2, geo.size.height * 0.38) - 16
            let step = rings > 0 ? min(reach / CGFloat(rings), 96) : 0
            let diameter = max(44, min(72, step - 8))  // 44pt: the smallest usable tap target
            ZStack {
                Color.black.opacity(0.45).ignoresSafeArea().onTapGesture(perform: dismiss)
                if items.isEmpty {
                    Text("Nothing scanned yet")
                        .font(.body)
                        .foregroundStyle(.white)
                        .position(center)
                }
                TimelineView(.animation(paused: reduceMotion)) { context in
                    let t = reduceMotion ? 0 : context.date.timeIntervalSinceReferenceDate
                    ForEach(Array(zip(items, slots)), id: \.0.id) { item, slot in
                        let a = slot.angle + ringSpin(ring: slot.ring, at: t)
                        let r = step * CGFloat(slot.ring)
                        circle(item, diameter: diameter)
                            .position(x: center.x + r * CGFloat(cos(a)), y: center.y + r * CGFloat(sin(a)))
                    }
                }
            }
        }
    }

    private func circle(_ item: ScannedItem, diameter: CGFloat) -> some View {
        let label = item.labelStatus == "pending" ? "labelling…" : (item.label ?? "unlabelled")
        return Button { select(item) } label: {
            Text(label)
                .font(.caption2.weight(.semibold))
                .multilineTextAlignment(.center)
                .lineLimit(2)
                .minimumScaleFactor(0.7)
                .padding(6)
                .frame(width: diameter, height: diameter)
                .background(.black.opacity(0.6), in: Circle())
                .overlay(Circle().stroke(.white.opacity(0.3), lineWidth: 1))
                .foregroundStyle(.white)
        }
        .buttonStyle(.plain)
        .contextMenu { menu(item) }
        .accessibilityLabel(String(format: "%@, %.1f by %.1f by %.1f centimetres", label,
                                   item.width * 100, item.depth * 100, item.height * 100))
    }
}
#endif
