import SwiftUI

@main
struct SpikeApp: App {
    var body: some Scene { WindowGroup { ContentView() } }
}

struct ContentView: View {
    @State private var item: ScannedItem?
    @State private var status = "Point at a box on a table, then tap it"

    var body: some View {
        ZStack(alignment: .bottom) {
            ScanView(item: $item, status: $status).ignoresSafeArea()
            VStack {
                if let item { Text(String(format: "%.1f × %.1f × %.1f cm", item.width, item.depth, item.height)) }
                Text(status).font(.footnote)
            }
                .font(.system(.title2, design: .monospaced))
                .padding()
                .background(.black.opacity(0.6))
                .foregroundStyle(.white)
                .clipShape(RoundedRectangle(cornerRadius: 12))
                .padding(.bottom, 40)
        }
    }
}
