import Foundation
import PackingPlan

/// Metres are the only unit in the model layer; centimetres exist solely here,
/// at the moment a string is built for a person to read.
func centimetres(_ metres: Float, decimals: Int = 1) -> String {
    String(format: "%.\(decimals)f cm", metres * 100)
}

#if canImport(SwiftUI)
import SwiftUI

extension Placement {
    /// Stable per-item colour. Driven by `step` so a plan always draws the same
    /// way, and spaced around the wheel so neighbours stay distinguishable.
    var diagramColor: Color {
        Color(hue: (Double(step) * 0.17).truncatingRemainder(dividingBy: 1.0),
              saturation: 0.62,
              brightness: 0.78)
    }
}
#endif
