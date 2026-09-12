import Foundation
import XCTest
@testable import PackPhysics

/// One entry of Resources/parity_v2.json: a scene plus the Python v2 validator's output.
struct ParityCase: Decodable {
    let name: String
    let scene: Scene
    let expected: JSONValue
}

enum Parity {
    static let cases: [ParityCase] = {
        let url = Bundle.module.url(forResource: "parity_v2", withExtension: "json", subdirectory: "Resources")!
        let data = try! Data(contentsOf: url)
        return try! JSONDecoder().decode([ParityCase].self, from: data)
    }()

    static func scene(named name: String) -> Scene {
        cases.first { $0.name == name }!.scene
    }
}

final class ContractTests: XCTestCase {
    func testParityDatasetLoadsAndRoundTrips() throws {
        XCTAssertGreaterThanOrEqual(Parity.cases.count, 50)
        let scene = Parity.scene(named: "fixture:valid_packed_scene")
        XCTAssertEqual(scene.container.id, "carry_on")
        XCTAssertEqual(scene.objects.count, 7)
        let laptop = scene.object(id: "laptop")!
        XCTAssertTrue(laptop.constraints.fragile)
        XCTAssertTrue(laptop.constraints.cannotSupportWeight)
        XCTAssertEqual(laptop.constraints.orientationLock, "flat_only")
        XCTAssertEqual(laptop.massKg, 1.3)
        let data = try JSONEncoder().encode(scene)
        let back = try JSONDecoder().decode(Scene.self, from: data)
        XCTAssertEqual(back, scene)
    }

    func testQuaternionMatchesPythonConvention() throws {
        // 90 degrees about +Y sends local +x to world -z (right-hand rule).
        let m = try quatToMatrix(.yaw(radians: .pi / 2))
        let x = m * Vec3(1, 0, 0)
        XCTAssertEqual(x.x, 0, accuracy: 1e-12)
        XCTAssertEqual(x.z, -1, accuracy: 1e-12)
        // Non-unit quaternion is re-normalized like Python (norm off by > 1e-3).
        let scaled = Quat(x: 0, y: 2 * sin(.pi / 4), z: 0, w: 2 * cos(.pi / 4))
        let m2 = try quatToMatrix(scaled)
        XCTAssertEqual(m2, m)
        XCTAssertThrowsError(try quatToMatrix(Quat(x: 0, y: 0, z: 0, w: 0), id: "bad")) { err in
            XCTAssertEqual((err as? MalformedSceneError)?.objectId, "bad")
        }
    }

    func testPrecomputeMatchesFixtureNumbers() throws {
        let g = try precompute(Parity.scene(named: "fixture:valid_packed_scene"))
        XCTAssertEqual(g.n, 7)
        XCTAssertEqual(g.containerFloorY, 0, accuracy: 1e-12)
        // laptop: dims (0.30, 0.02, 0.21) at (-0.125, 0.01, -0.07) -> x in [-0.275, 0.025]
        let i = g.index["laptop"]!
        XCTAssertEqual(g.aabbMin[i].x, -0.275, accuracy: 1e-12)
        XCTAssertEqual(g.aabbMax[i].x, 0.025, accuracy: 1e-12)
        XCTAssertEqual(g.aabbMin[i].y, 0, accuracy: 1e-12)
        // Every stacked item in the fixture rests on something: floor or another object.
        let floor = onFloor(g, contactEps: 1e-3)
        let rests = Set(restingPairs(g, contactEps: 1e-3).map(\.top))
        for k in 0..<g.n { XCTAssertTrue(floor[k] || rests.contains(k), g.ids[k]) }
        // No AABB overlaps in a valid packing except the deliberately flush stacks.
        let pairs = aabbCandidatePairs(g)
        XCTAssertEqual(pairs.count, 3)
    }

    func testMalformedScenesNameTheOffender() {
        let good = SceneObject(id: "good", dimensions: Vec3(0.1, 0.1, 0.1), position: Vec3(0, 0.05, 0))
        let box = Container(id: "c", dimensions: Vec3(1, 1, 1), position: Vec3(0, 0.5, 0))
        let bad = [
            SceneObject(id: "bad", dimensions: Vec3(.nan, 0.1, 0.1), position: Vec3(0.3, 0.05, 0)),
            SceneObject(id: "bad", dimensions: Vec3(0, 0.1, 0.1), position: Vec3(0.3, 0.05, 0)),
            SceneObject(id: "bad", dimensions: Vec3(0.1, 0.1, 0.1), position: Vec3(.infinity, 0.05, 0)),
            SceneObject(id: "bad", dimensions: Vec3(0.1, 0.1, 0.1), position: Vec3(0.3, 0.05, 0), rotation: Quat(x: 0, y: 0, z: 0, w: 0)),
        ]
        for b in bad {
            XCTAssertThrowsError(try precompute(Scene(container: box, objects: [good, b]))) { err in
                XCTAssertEqual((err as? MalformedSceneError)?.objectId, "bad")
            }
        }
        let dup = Scene(container: box, objects: [good, SceneObject(id: "good", dimensions: Vec3(0.1, 0.1, 0.1), position: Vec3(0.5, 0.05, 0))])
        XCTAssertThrowsError(try precompute(dup)) { err in
            XCTAssertEqual((err as? MalformedSceneError)?.objectId, "good")
        }
        // The parity dataset's malformed case decodes and is rejected with the same id.
        let dupCase = Parity.scene(named: "malformed:dup_ids")
        XCTAssertThrowsError(try precompute(dupCase)) { err in
            XCTAssertEqual((err as? MalformedSceneError)?.objectId, "a")
        }
    }
}
