import Foundation
import XCTest
@testable import PackPhysics

private func makeObj(_ id: String, dims: Vec3 = Vec3(0.2, 0.2, 0.2), rigidity: Rigidity = .rigid, k: Double = 1.0) -> SceneObject {
    SceneObject(id: id, dimensions: dims, position: Vec3(0, 0, 0), rigidity: rigidity, compressibilityK: k)
}

final class CompressibilityTests: XCTestCase {
    func testRigidAlwaysZeroRegardlessOfK() {
        for k in [1.0, 2.0, 100.0] {
            let o = makeObj("r", rigidity: .rigid, k: k)
            XCTAssertEqual(compressionAllowanceM(o, extentM: 1.0), 0.0)
        }
    }

    func testSoftK1IsZeroNothingToCompress() {
        let o = makeObj("s", rigidity: .soft, k: 1.0)
        XCTAssertEqual(compressionAllowanceM(o, extentM: 1.0), 0.0, accuracy: 1e-9)
    }

    func testSoftK2PositiveAndBelowCap() {
        let o = makeObj("s", rigidity: .soft, k: 2.0)
        let extent = 1.0
        let allowance = compressionAllowanceM(o, extentM: extent)
        XCTAssertGreaterThan(allowance, 0.0)
        XCTAssertLessThan(allowance, 0.95 * extent)
        // extent * (1 - 1/2) * 1.0 = 0.5 * extent
        XCTAssertEqual(allowance, 0.5 * extent, accuracy: 1e-9)
    }

    func testSemiIsHalfOfSoftForSameK() {
        let extent = 1.0
        let soft = makeObj("soft", rigidity: .soft, k: 2.0)
        let semi = makeObj("semi", rigidity: .semi, k: 2.0)
        XCTAssertEqual(compressionAllowanceM(semi, extentM: extent), 0.5 * compressionAllowanceM(soft, extentM: extent), accuracy: 1e-9)
    }

    func testExtremeKClampedTo95PercentCeiling() {
        let extent = 1.0
        let o = makeObj("s", rigidity: .soft, k: 1e9)
        let allowance = compressionAllowanceM(o, extentM: extent)
        XCTAssertLessThan(allowance, extent)
        XCTAssertEqual(allowance, 0.95 * extent, accuracy: 1e-6)
    }

    func testTwoSoftObjectsSumAllowances() throws {
        let a = makeObj("a", dims: Vec3(1, 1, 1), rigidity: .soft, k: 2.0)
        let b = makeObj("b", dims: Vec3(1, 1, 1), rigidity: .soft, k: 2.0)
        let obbA = try obbFrom(a), obbB = try obbFrom(b)
        let axis = Vec3(1, 0, 0)
        let total = combinedCollisionAllowanceM(a, b, mtvAxis: axis, obbA: obbA, obbB: obbB)
        let each = compressionAllowanceM(a, extentM: 1.0)
        XCTAssertEqual(total, 2 * each, accuracy: 1e-9)
    }

    func testRigidPlusSoftOnlySoftContributes() throws {
        let a = makeObj("a", dims: Vec3(1, 1, 1), rigidity: .rigid, k: 5.0)
        let b = makeObj("b", dims: Vec3(1, 1, 1), rigidity: .soft, k: 2.0)
        let obbA = try obbFrom(a), obbB = try obbFrom(b)
        let axis = Vec3(1, 0, 0)
        let total = combinedCollisionAllowanceM(a, b, mtvAxis: axis, obbA: obbA, obbB: obbB)
        XCTAssertEqual(total, compressionAllowanceM(b, extentM: 1.0), accuracy: 1e-9)
    }

    func testContainerWallAllowanceMatchesCompressionAllowanceM() {
        let o = makeObj("s", rigidity: .soft, k: 3.0)
        XCTAssertEqual(containerWallAllowanceM(o, wallAxisExtentM: 0.4), compressionAllowanceM(o, extentM: 0.4), accuracy: 1e-9)
    }
}
