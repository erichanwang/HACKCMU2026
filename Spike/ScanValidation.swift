import Foundation

/// Table plane: the horizontal plane nearest below the seed (not the largest), within
/// `maxDrop`. Nearest avoids picking the room's floor when the object sits on a table
/// above it — the floor is also "a horizontal plane below the seed", just further down.
/// `maxDrop` stops a stray floor plane from being accepted as "the table" when no real
/// table plane has been found yet.
func pickTablePlane(planeYs: [Float], seedY: Float, maxDrop: Float) -> Float? {
    planeYs.filter { $0 < seedY && seedY - $0 <= maxDrop }.max()
}

/// A fitted box is only usable if every dimension is finite, positive, and no bigger than
/// a single packed item or suitcase plausibly is. Degenerate clusters (collinear/coincident
/// points, or NaN mesh data) can fit a zero or NaN dimension; a cluster that swallowed a
/// wall or the floor fits an oversized one. Both should be rejected before they reach the
/// server.
func isUsableBox(width: Float, height: Float, depth: Float, maxDimension: Float) -> Bool {
    [width, height, depth].allSatisfy { $0.isFinite && $0 > 0 && $0 <= maxDimension }
}

/// A heightmap is only usable if it's non-empty, rectangular (every row matches the first
/// row's length), and free of NaN/infinite cells — the server and solver both assume this
/// shape.
func isUsableHeightMap(_ heights: [[Float]]) -> Bool {
    guard let first = heights.first, !first.isEmpty else { return false }
    return heights.allSatisfy { row in row.count == first.count && row.allSatisfy(\.isFinite) }
}

/// What the user is told about an item's label, given the server's terminal state.
/// Pure so `tests/swift/robust` can pin it: every `labelStatus` the server can write must map to
/// its own actionable line. A value falling through to a generic string is the bug this prevents —
/// `"failed"` did exactly that before, showing "labelled unknown" as if the guess were real.
func labelStatusMessage(labelStatus: String?, label: String?, identifyHint: String?) -> String {
    switch labelStatus {
    case "pending":
        return "labelling…"
    case "unidentified":
        // The server computes the hint from the scan geometry (flat/tiny -> rescan, otherwise
        // rotate) and from whether any model was configured at all. Trust it over our own copy.
        return identifyHint ?? "couldn't identify it — type it in"
    case "failed":
        return "couldn't reach the labeller — type it in"
    default:
        return "labelled \(label ?? "?")"
    }
}
