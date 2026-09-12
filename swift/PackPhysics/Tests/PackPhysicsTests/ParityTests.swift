// Cross-language parity: `validateLayout` vs the Python v2 validator, on every
// scene in Resources/parity_v2.json (fixtures, benchmark grids, 30 seeded random
// scenes with fully random orientations, plus the malformed case).
//
// Structural JSON comparison with numeric tolerance 1e-9 (`assertJSONClose`, from
// MetricsTests.swift). Failures name the case and the JSON path.

import Foundation
import XCTest
@testable import PackPhysics

/// Seconds, monotonic enough for millisecond-scale loops.
private func now() -> Double { Date().timeIntervalSince1970 }

final class ParityTests: XCTestCase {
    func testValidateLayoutMatchesPythonOnEveryParityCase() {
        var checked = 0
        for c in Parity.cases where c.name != "malformed:dup_ids" {
            assertJSONClose(validateLayout(c.scene).asJSON(), c.expected,
                            tol: 1e-9, path: "$[\(c.name)]")
            checked += 1
        }
        XCTAssertEqual(checked, Parity.cases.count - 1)
    }

    /// The malformed case: everything must match exactly except the `detail`
    /// string (a Swift error message need not read like Python's), which only
    /// has to be non-empty.
    func testMalformedCaseMatchesExceptDetailText() {
        let c = Parity.cases.first { $0.name == "malformed:dup_ids" }!
        let got = validateLayout(c.scene).asJSON()
        XCTAssertEqual(got["valid"]?.boolValue, false)
        XCTAssertEqual(got["score"]?.doubleValue, 0.0)
        XCTAssertEqual(got["warnings"], c.expected["warnings"])
        XCTAssertEqual(got["metrics"], c.expected["metrics"])  // {}
        let violations = got["violations"]!.arrayValue!
        let expected = c.expected["violations"]!.arrayValue!
        XCTAssertEqual(violations.count, expected.count)
        for (v, e) in zip(violations, expected) {
            XCTAssertEqual(v["type"], e["type"])
            XCTAssertEqual(v["object"], e["object"])
            XCTAssertFalse((v["detail"]?.stringValue ?? "").isEmpty)
        }
    }

    func testEveryParityCaseIsDeterministic() {
        for c in Parity.cases {
            XCTAssertEqual(validateLayout(c.scene).asJSON(), validateLayout(c.scene).asJSON(), c.name)
        }
    }

    /// Not a pass/fail assertion beyond "it finishes": prints ms/call so the
    /// Swift port can be compared with the Python v2 reference (~2.1 ms for
    /// grid:n20:cell0.19, ~3.0 ms for grid:n40:cell0.19).
    func testValidateLayoutTiming() {
        for name in ["grid:n20:cell0.19", "grid:n40:cell0.19"] {
            let scene = Parity.scene(named: name)
            _ = validateLayout(scene)  // warm up
            let iterations = 50
            let t0 = now()
            for _ in 0..<iterations { _ = validateLayout(scene) }
            let msPerCall = (now() - t0) * 1000.0 / Double(iterations)
            print("[timing] validateLayout \(name): \(String(format: "%.3f", msPerCall)) ms/call")
            XCTAssertLessThan(msPerCall, 500.0)
        }
    }
}
