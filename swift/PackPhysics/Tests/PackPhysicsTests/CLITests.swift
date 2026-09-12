// Exercises the built `packphysics` executable end-to-end via `Process`.
// Requires the executable to already be built: `swift build && swift test
// --filter CLITests` (plain `swift test` builds it too, but a bare
// `swift build` first makes failures easier to diagnose). If the binary
// isn't found, every test here skips via `XCTSkip` rather than failing.

import Foundation
import XCTest
@testable import PackPhysics

/// Directory holding the just-built products (the executable lives next to
/// the xctest bundle). Standard XCTest pattern; on Linux `Bundle.main` is the
/// test runner itself, which sits in the same products directory.
private func productsDirectory() -> URL {
    #if os(macOS)
    for bundle in Bundle.allBundles where bundle.bundlePath.hasSuffix(".xctest") {
        return bundle.bundleURL.deletingLastPathComponent()
    }
    fatalError("couldn't find the products directory")
    #else
    // On Linux `Bundle.main.bundleURL` for the xctest binary already IS the
    // products directory (verified empirically -- unlike macOS's `.xctest`
    // bundle, there's no extra path component to strip here).
    return Bundle.main.bundleURL
    #endif
}

private func packphysicsBinary() throws -> URL {
    let url = productsDirectory().appendingPathComponent("packphysics")
    guard FileManager.default.isExecutableFile(atPath: url.path) else {
        throw XCTSkip("packphysics binary not found at \(url.path) -- run `swift build` before `swift test --filter CLITests`")
    }
    return url
}

private struct CLIResult {
    let exitCode: Int32
    let stdout: String
    let stderr: String
}

private func runCLI(_ args: [String]) throws -> CLIResult {
    let process = Process()
    process.executableURL = try packphysicsBinary()
    process.arguments = args
    let outPipe = Pipe()
    let errPipe = Pipe()
    process.standardOutput = outPipe
    process.standardError = errPipe
    try process.run()
    process.waitUntilExit()
    let outData = outPipe.fileHandleForReading.readDataToEndOfFile()
    let errData = errPipe.fileHandleForReading.readDataToEndOfFile()
    return CLIResult(
        exitCode: process.terminationStatus,
        stdout: String(data: outData, encoding: .utf8) ?? "",
        stderr: String(data: errData, encoding: .utf8) ?? ""
    )
}

/// Writes `text` to a uniquely-named file under the system temp directory and
/// returns its URL; the file is removed when the test case's run ends.
private func writeTempFile(_ text: String, testCase: XCTestCase, name: String) -> URL {
    let url = FileManager.default.temporaryDirectory.appendingPathComponent("\(UUID().uuidString)-\(name)")
    try! text.write(to: url, atomically: true, encoding: .utf8)
    testCase.addTeardownBlock { try? FileManager.default.removeItem(at: url) }
    return url
}

final class CLITests: XCTestCase {
    func testExamplePrintsSevenObjectScene() throws {
        let result = try runCLI(["example"])
        XCTAssertEqual(result.exitCode, 0)
        let scene = try sceneFromJSON(Data(result.stdout.utf8))
        XCTAssertEqual(scene.objects.count, 7)
        XCTAssertEqual(scene.container.id, "carry_on")
    }

    func testValidateOnExampleSceneIsValid() throws {
        let example = try runCLI(["example"])
        XCTAssertEqual(example.exitCode, 0)
        let sceneFile = writeTempFile(example.stdout, testCase: self, name: "scene.json")

        let result = try runCLI(["validate", sceneFile.path])
        XCTAssertEqual(result.exitCode, 0)
        let validation = try resultFromJSON(Data(result.stdout.utf8))
        XCTAssertTrue(validation.valid)
        XCTAssertTrue(result.stdout.contains("\"valid\":true"))
    }

    func testValidateWithCollisionPlacementsIsInvalid() throws {
        let example = try runCLI(["example"])
        XCTAssertEqual(example.exitCode, 0)
        let sceneFile = writeTempFile(example.stdout, testCase: self, name: "scene.json")
        // Moves the shoe into the laptop by exactly 0.02m along Z (see
        // tests/fixtures.py::scene_with_collision in the Python prototype).
        let placementsJSON = #"{"placements": [{"id": "shoe", "position": [-0.13, 0.055, 0.075]}]}"#
        let placementsFile = writeTempFile(placementsJSON, testCase: self, name: "placements.json")

        let result = try runCLI(["validate", sceneFile.path, "--placements", placementsFile.path])
        XCTAssertEqual(result.exitCode, 1)
        let validation = try resultFromJSON(Data(result.stdout.utf8))
        XCTAssertFalse(validation.valid)

        let collision = validation.violations.first { $0["type"]?.stringValue == "OBJECT_COLLISION" }
        XCTAssertNotNil(collision, "expected an OBJECT_COLLISION violation, got: \(validation.violations)")
        let objects = Set(collision?["objects"]?.arrayValue?.compactMap(\.stringValue) ?? [])
        XCTAssertEqual(objects, ["laptop", "shoe"])
        XCTAssertEqual(collision?["penetration_depth_m"]?.doubleValue ?? -1, 0.02, accuracy: 1e-6)
    }

    func testValidateMissingFileExitsTwoWithEmptyStdout() throws {
        let result = try runCLI(["validate", "/tmp/packphysics-cli-tests-does-not-exist.json"])
        XCTAssertEqual(result.exitCode, 2)
        XCTAssertTrue(result.stdout.isEmpty)
        XCTAssertFalse(result.stderr.isEmpty)
    }

    func testScanToObjectConvertsCentimetresToMetres() throws {
        let itemFile = writeTempFile(#"{"id":"shoe","width":29,"depth":12,"height":11}"#, testCase: self, name: "item.json")
        let result = try runCLI(["scan-to-object", itemFile.path])
        XCTAssertEqual(result.exitCode, 0)
        let object = try JSONDecoder().decode(SceneObject.self, from: Data(result.stdout.utf8))
        XCTAssertEqual(object.dimensions.x, 0.29, accuracy: 1e-9)
        XCTAssertEqual(object.dimensions.y, 0.11, accuracy: 1e-9)
        XCTAssertEqual(object.dimensions.z, 0.12, accuracy: 1e-9)
    }

    func testBenchRunsAndPrintsTimings() throws {
        let result = try runCLI(["bench", "--objects", "5", "--repeat", "3"])
        XCTAssertEqual(result.exitCode, 0)
        let lines = result.stdout.split(separator: "\n").map(String.init)
        XCTAssertTrue(lines.contains { $0.contains("ms/call") }, "missing header line: \(result.stdout)")
        // At least one data row: name, n, a "X.XX"-shaped ms/call figure, collisions.
        let msRow = try! NSRegularExpression(pattern: #"\d+\.\d{2}\s+\d+\s*$"#)
        let hasTimingRow = lines.contains { line in
            msRow.firstMatch(in: line, range: NSRange(line.startIndex..., in: line)) != nil
        }
        XCTAssertTrue(hasTimingRow, "expected at least one ms/call timing row, got: \(result.stdout)")
    }

    func testHelpExitsZero() throws {
        let result = try runCLI(["--help"])
        XCTAssertEqual(result.exitCode, 0)
        XCTAssertTrue(result.stdout.contains("Usage: packphysics"))
    }
}
