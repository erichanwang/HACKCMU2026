import SwiftUI

/// Everything scanned so far, as one horizontal row you scroll along the bottom of the
/// camera view.
///
/// This replaces the drifting concentric rings. The rings looked better standing still
/// than they worked in the hand: items moved while you reached for them, they overlapped
/// past about three dozen, and nothing about a packing inventory is radial. A row is
/// scannable, scrolls as far as the inventory goes, and holds still.
struct InventoryStrip<MenuContent: View>: View {
    let items: [ScannedItem]
    var select: (ScannedItem) -> Void
    var dismiss: () -> Void
    /// The long-press menu for one card; the caller owns the suitcase list and the actions.
    @ViewBuilder var menu: (ScannedItem) -> MenuContent

    var body: some View {
        VStack(alignment: .leading, spacing: 9) {
            HStack(spacing: 6) {
                Text("Inventory").font(.footnote.weight(.semibold))
                Text("\(items.count)")
                    .font(.footnote.monospacedDigit())
                    .foregroundStyle(Sheet.ink.opacity(0.5))
                Spacer()
                Button(action: dismiss) {
                    Image(systemName: "xmark")
                        .font(.caption.weight(.semibold))
                        .foregroundStyle(Sheet.ink.opacity(0.6))
                        .frame(width: 30, height: 30)
                        .contentShape(Rectangle())
                }
                .accessibilityLabel("Close inventory")
                .padding(.trailing, -6)
                .padding(.top, -4)
            }
            .foregroundStyle(Sheet.ink)

            if items.isEmpty {
                Text("Nothing scanned yet.")
                    .font(.footnote)
                    .foregroundStyle(Sheet.ink.opacity(0.5))
                    .frame(height: 92, alignment: .leading)
            } else {
                ScrollView(.horizontal, showsIndicators: false) {
                    HStack(spacing: 10) {
                        ForEach(items) { card($0) }
                    }
                    .padding(.vertical, 2)
                }
                .frame(height: 96)
            }
        }
        .padding(13)
        .background(Sheet.paper.opacity(0.94), in: RoundedRectangle(cornerRadius: 22, style: .continuous))
    }

    private func card(_ item: ScannedItem) -> some View {
        let label = item.displayName
        return Button { select(item) } label: {
            VStack(alignment: .leading, spacing: 5) {
                Image(systemName: item.symbol)
                    .font(.system(size: 15, weight: .regular))
                    .foregroundStyle(Sheet.accent)
                Spacer(minLength: 0)
                Text(label)
                    .font(.caption.weight(.medium))
                    .lineLimit(2)
                    .multilineTextAlignment(.leading)
                Text(item.manifestSize)
                    .font(.caption2.monospaced())
                    .foregroundStyle(Sheet.ink.opacity(0.5))
            }
            .foregroundStyle(Sheet.ink)
            .frame(width: 104, height: 88, alignment: .leading)
            .padding(.horizontal, 11)
            .padding(.vertical, 9)
            .background(Sheet.card, in: RoundedRectangle(cornerRadius: 15, style: .continuous))
            .overlay(RoundedRectangle(cornerRadius: 15, style: .continuous).stroke(Sheet.hairline, lineWidth: 0.5))
        }
        .buttonStyle(.plain)
        .contextMenu { menu(item) }
        .accessibilityLabel(String(format: "%@, %.0f by %.0f by %.0f centimetres", label,
                                   item.width * 100, item.depth * 100, item.height * 100))
    }
}
