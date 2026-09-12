import Foundation
import XCTest
@testable import PackPhysics

final class IOTests: XCTestCase {

    // MARK: - Scene JSON round-trip

    func testRoundTripEveryParityScene() throws {
        for c in Parity.cases {
            let data = try sceneToJSON(c.scene)
            let back = try sceneFromJSON(data)
            XCTAssertEqual(back, c.scene, c.name)
        }
    }

    /// The exact JSON `examples/scene_carry_on.json` (== fixture:valid_packed_scene)
    /// as written by the Python layer -- decoded directly, not round-tripped
    /// through our own encoder first.
    static let pythonValidPackedSceneJSON = """
    {
      "container": {
        "id": "carry_on",
        "dimensions": [0.56, 0.23, 0.36],
        "position": [0.0, 0.115, 0.0],
        "rotation": [0.0, 0.0, 0.0, 1.0]
      },
      "objects": [
        {
          "id": "laptop",
          "dimensions": [0.3, 0.02, 0.21],
          "position": [-0.125, 0.01, -0.07],
          "rotation": [0.0, 0.0, 0.0, 1.0],
          "mass_kg": 1.3,
          "constraints": {
            "fragile": true, "keep_upright": false,
            "cannot_support_weight": true, "heavy": false,
            "orientation_lock": "flat_only"
          },
          "rigidity": "rigid",
          "compressibility_k": 1.0
        },
        {
          "id": "headphones_case",
          "dimensions": [0.18, 0.07, 0.18],
          "position": [0.14, 0.035, -0.07],
          "rotation": [0.0, 0.0, 0.0, 1.0],
          "mass_kg": 0.3,
          "constraints": {
            "fragile": false, "keep_upright": false,
            "cannot_support_weight": false, "heavy": false,
            "orientation_lock": null
          },
          "rigidity": "rigid",
          "compressibility_k": 1.0
        },
        {
          "id": "charger",
          "dimensions": [0.09, 0.03, 0.06],
          "position": [0.1, 0.085, -0.1],
          "rotation": [0.0, 0.0, 0.0, 1.0],
          "mass_kg": 0.1,
          "constraints": {
            "fragile": false, "keep_upright": false,
            "cannot_support_weight": false, "heavy": false,
            "orientation_lock": null
          },
          "rigidity": "rigid",
          "compressibility_k": 1.0
        },
        {
          "id": "toiletry_bottle",
          "dimensions": [0.06, 0.15, 0.06],
          "position": [0.18, 0.145, -0.02],
          "rotation": [0.0, 0.0, 0.0, 1.0],
          "mass_kg": 0.2,
          "constraints": {
            "fragile": false, "keep_upright": true,
            "cannot_support_weight": false, "heavy": false,
            "orientation_lock": null
          },
          "rigidity": "rigid",
          "compressibility_k": 1.0
        },
        {
          "id": "shoe",
          "dimensions": [0.29, 0.11, 0.12],
          "position": [-0.13, 0.055, 0.1],
          "rotation": [0.0, 0.0, 0.0, 1.0],
          "mass_kg": 0.3,
          "constraints": {
            "fragile": false, "keep_upright": false,
            "cannot_support_weight": false, "heavy": false,
            "orientation_lock": null
          },
          "rigidity": "rigid",
          "compressibility_k": 1.0
        },
        {
          "id": "toiletry_bag",
          "dimensions": [0.2, 0.08, 0.135],
          "position": [0.125, 0.04, 0.1075],
          "rotation": [0.0, 0.0, 0.0, 1.0],
          "mass_kg": 0.5,
          "constraints": {
            "fragile": false, "keep_upright": false,
            "cannot_support_weight": false, "heavy": false,
            "orientation_lock": null
          },
          "rigidity": "rigid",
          "compressibility_k": 1.0
        },
        {
          "id": "camera",
          "dimensions": [0.13, 0.09, 0.1],
          "position": [0.125, 0.125, 0.1075],
          "rotation": [0.0, 0.0, 0.0, 1.0],
          "mass_kg": 0.4,
          "constraints": {
            "fragile": false, "keep_upright": false,
            "cannot_support_weight": false, "heavy": false,
            "orientation_lock": null
          },
          "rigidity": "rigid",
          "compressibility_k": 1.0
        }
      ]
    }
    """

    func testDecodesPythonWrittenValidPackedSceneJSON() throws {
        let scene = try sceneFromJSON(Data(Self.pythonValidPackedSceneJSON.utf8))
        XCTAssertEqual(scene.container.id, "carry_on")
        XCTAssertEqual(scene.objects.count, 7)
        let laptop = scene.object(id: "laptop")!
        XCTAssertTrue(laptop.constraints.fragile)
        XCTAssertTrue(laptop.constraints.cannotSupportWeight)
        XCTAssertFalse(laptop.constraints.keepUpright)
        XCTAssertFalse(laptop.constraints.heavy)
        XCTAssertEqual(laptop.constraints.orientationLock, "flat_only")
        XCTAssertEqual(laptop.massKg, 1.3)
    }

    // MARK: - ScannedItem

    func testScannedItemCmToMMapping() throws {
        let json = """
        {"id": "shoe-1", "width": 29.0, "depth": 12.0, "height": 11.0}
        """
        let item = try JSONDecoder().decode(ScannedItem.self, from: Data(json.utf8))
        let obj = sceneObject(from: item)
        XCTAssertEqual(obj.id, "shoe-1")
        XCTAssertEqual(obj.dimensions.x, 0.29, accuracy: 1e-9)
        XCTAssertEqual(obj.dimensions.y, 0.11, accuracy: 1e-9)
        XCTAssertEqual(obj.dimensions.z, 0.12, accuracy: 1e-9)
        XCTAssertEqual(obj.position, .zero)
        XCTAssertEqual(obj.rotation, .identity)
    }

    func testScannedItemFromUUIDStringId() throws {
        let uuidString = UUID().uuidString
        let json = """
        {"id": "\(uuidString)", "width": 29.0, "depth": 12.0, "height": 11.0}
        """
        let item = try JSONDecoder().decode(ScannedItem.self, from: Data(json.utf8))
        let obj = sceneObject(from: item)
        XCTAssertEqual(obj.id, uuidString)
        XCTAssertEqual(obj.dimensions.x, 0.29, accuracy: 1e-9)
        XCTAssertEqual(obj.dimensions.y, 0.11, accuracy: 1e-9)
        XCTAssertEqual(obj.dimensions.z, 0.12, accuracy: 1e-9)
    }

    // MARK: - BoxFit orientation (numerically verified, matching tests/test_io.py)

    private func checkBoxFitOrientation(degrees: Double) throws {
        let theta = degrees * .pi / 180
        let widthM = 0.29, depthM = 0.12, heightM = 0.11
        let center = Vec3(0.4, 0.3, -0.2)
        let axis = Vec3(cos(theta), 0, -sin(theta))

        let obj = sceneObject(id: "shoe", widthM: widthM, depthM: depthM, heightM: heightM, center: center, axis: axis)
        let verts = obbVertices(try obbFrom(obj))
        let edge = verts[4] - verts[0]  // SIGNS rows 0/4 differ only in local-x sign
        let len = length(edge)
        let cosAngle = dot(edge, axis) / (len * length(axis))
        XCTAssertGreaterThan(abs(cosAngle), 1 - 1e-9, "deg=\(degrees)")
        XCTAssertEqual(len, widthM, accuracy: 1e-9, "deg=\(degrees)")
        let topY = verts.map(\.y).max()!
        XCTAssertEqual(topY, center.y + heightM / 2, accuracy: 1e-9, "deg=\(degrees)")

        // Float convenience entry point must match up to Float precision.
        let objF = sceneObject(
            id: "shoe", widthM: widthM, depthM: depthM, heightM: heightM,
            center: SIMD3<Float>(Float(center.x), Float(center.y), Float(center.z)),
            axis: SIMD3<Float>(Float(axis.x), Float(axis.y), Float(axis.z))
        )
        XCTAssertEqual(objF.rotation.x, obj.rotation.x, accuracy: 1e-6)
        XCTAssertEqual(objF.rotation.y, obj.rotation.y, accuracy: 1e-6)
        XCTAssertEqual(objF.rotation.z, obj.rotation.z, accuracy: 1e-6)
        XCTAssertEqual(objF.rotation.w, obj.rotation.w, accuracy: 1e-6)
        XCTAssertEqual(objF.position.x, obj.position.x, accuracy: 1e-6)
        XCTAssertEqual(objF.position.y, obj.position.y, accuracy: 1e-6)
        XCTAssertEqual(objF.position.z, obj.position.z, accuracy: 1e-6)
    }

    func testBoxFitOrientation0Deg() throws { try checkBoxFitOrientation(degrees: 0) }
    func testBoxFitOrientation35Deg() throws { try checkBoxFitOrientation(degrees: 35) }
    func testBoxFitOrientation90Deg() throws { try checkBoxFitOrientation(degrees: 90) }
    func testBoxFitOrientationNeg120Deg() throws { try checkBoxFitOrientation(degrees: -120) }

    // MARK: - applyPlacements

    func testApplyPlacementsPositionRotationSpelling() throws {
        let scene = Parity.scene(named: "fixture:valid_packed_scene")
        let json = """
        [{"id": "shoe", "position": [1.0, 2.0, 3.0], "rotation": [0.0, 1.0, 0.0, 0.0]}]
        """
        let placements = try placementsFromJSON(Data(json.utf8))
        let newScene = try applyPlacements(scene, placements)
        let shoe = newScene.object(id: "shoe")!
        XCTAssertEqual(shoe.position, Vec3(1.0, 2.0, 3.0))
        XCTAssertEqual(shoe.rotation, Quat(x: 0, y: 1, z: 0, w: 0))
    }

    func testApplyPlacementsTargetPositionRotationSpelling() throws {
        let scene = Parity.scene(named: "fixture:valid_packed_scene")
        let json = """
        [{"id": "shoe", "target_position": [4.0, 5.0, 6.0], "target_rotation": [1.0, 0.0, 0.0, 0.0]}]
        """
        let placements = try placementsFromJSON(Data(json.utf8))
        let newScene = try applyPlacements(scene, placements)
        let shoe = newScene.object(id: "shoe")!
        XCTAssertEqual(shoe.position, Vec3(4.0, 5.0, 6.0))
        XCTAssertEqual(shoe.rotation, Quat(x: 1, y: 0, z: 0, w: 0))
    }

    func testApplyPlacementsMissingRotationKeepsOldOne() throws {
        let scene = Parity.scene(named: "fixture:valid_packed_scene")
        let original = scene.object(id: "shoe")!
        let newScene = try applyPlacements(scene, [Placement(id: "shoe", position: Vec3(0, 0, 0))])
        let shoe = newScene.object(id: "shoe")!
        XCTAssertEqual(shoe.position, Vec3(0, 0, 0))
        XCTAssertEqual(shoe.rotation, original.rotation)
    }

    func testApplyPlacementsUnmentionedObjectsKeepPose() throws {
        let scene = Parity.scene(named: "fixture:valid_packed_scene")
        let originalLaptop = scene.object(id: "laptop")!
        let newScene = try applyPlacements(scene, [Placement(id: "shoe", position: Vec3(0, 0, 0))])
        XCTAssertEqual(newScene.object(id: "laptop"), originalLaptop)
    }

    func testApplyPlacementsUnknownIdThrows() throws {
        let scene = Parity.scene(named: "fixture:valid_packed_scene")
        XCTAssertThrowsError(try applyPlacements(scene, [Placement(id: "nonexistent", position: .zero)])) { err in
            XCTAssertEqual((err as? MalformedSceneError)?.objectId, "nonexistent")
        }
    }

    func testApplyPlacementsDoesNotMutateInput() throws {
        let scene = Parity.scene(named: "fixture:valid_packed_scene")
        let originalPositions = scene.objects.map(\.position)
        _ = try applyPlacements(scene, [Placement(id: "shoe", position: Vec3(9, 9, 9))])
        XCTAssertEqual(scene.objects.map(\.position), originalPositions)
    }

    // MARK: - placementsFromJSON

    func testPlacementsFromJSONAcceptsWrapperShape() throws {
        let json = """
        {"placements": [{"id": "shoe", "position": [1.0, 2.0, 3.0]}]}
        """
        let placements = try placementsFromJSON(Data(json.utf8))
        XCTAssertEqual(placements.count, 1)
        XCTAssertEqual(placements[0].id, "shoe")
    }

    func testPlacementsFromJSONAcceptsBareArrayShape() throws {
        let json = """
        [{"id": "shoe", "position": [1.0, 2.0, 3.0]}]
        """
        let placements = try placementsFromJSON(Data(json.utf8))
        XCTAssertEqual(placements.count, 1)
        XCTAssertEqual(placements[0].id, "shoe")
    }

    /// The exact content of `examples/placements_collision.json`.
    static let pythonPlacementsCollisionJSON = """
    {
      "placements": [
        {
          "id": "shoe",
          "target_position": [-0.13, 0.055, 0.075],
          "target_rotation": [0.0, 0.0, 0.0, 1.0]
        }
      ]
    }
    """

    func testPlacementsCollisionExampleMovesShoe() throws {
        let placements = try placementsFromJSON(Data(Self.pythonPlacementsCollisionJSON.utf8))
        let scene = Parity.scene(named: "fixture:valid_packed_scene")
        let newScene = try applyPlacements(scene, placements)
        let shoe = newScene.object(id: "shoe")!
        XCTAssertEqual(shoe.position.x, -0.13, accuracy: 1e-9)
        XCTAssertEqual(shoe.position.y, 0.055, accuracy: 1e-9)
        XCTAssertEqual(shoe.position.z, 0.075, accuracy: 1e-9)
    }

    // MARK: - PANCandidatesFile

    func testPANCandidatesFileDecodesGeneratedSample() throws {
        let url = Bundle.module.url(forResource: "pan_candidates_sample", withExtension: "json", subdirectory: "Resources")!
        let data = try Data(contentsOf: url)
        let file = try panCandidates(from: data)
        XCTAssertEqual(file.backend, "cache(mock)")
        XCTAssertTrue(file.panAvailable)
        XCTAssertEqual(file.candidates.count, 3)

        let a = file.candidates.first { $0.candidateId == "A" }!
        XCTAssertEqual(a.physicsStatus, "valid")
        XCTAssertEqual(a.simulationStatus, "complete")
        XCTAssertEqual(a.executionRisk, "low")

        let c = file.candidates.first { $0.candidateId == "C" }!
        XCTAssertEqual(c.physicsStatus, "invalid")
        XCTAssertEqual(c.simulationStatus, "unavailable")
        XCTAssertNil(c.executionRisk)
        XCTAssertNil(c.panPreviewVideo)
        XCTAssertNil(c.panFinalFrame)

        XCTAssertFalse(file.steps.isEmpty)
    }

    // MARK: - ValidationResult round-trip

    func testResultRoundTripHandBuilt() throws {
        let result = ValidationResult(
            valid: false, score: 0.5,
            violations: [.object(["type": "OBJECT_COLLISION", "object": "shoe"])],
            warnings: [],
            metrics: .object(["fill_ratio": 0.42])
        )
        let data = try resultToJSON(result)
        let back = try resultFromJSON(data)
        XCTAssertEqual(back, result)
    }

    func testResultRoundTripFromParityExpected() throws {
        let expected = Parity.cases[0].expected
        let data = try JSONEncoder().encode(expected)
        let result = try JSONDecoder().decode(ValidationResult.self, from: data)
        let roundTripped = try resultFromJSON(try resultToJSON(result))
        XCTAssertEqual(roundTripped, result)
    }
}
