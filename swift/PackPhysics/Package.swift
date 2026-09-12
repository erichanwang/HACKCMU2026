// swift-tools-version:5.9
import PackageDescription

let package = Package(
    name: "PackPhysics",
    platforms: [.iOS(.v17), .macOS(.v13)],
    products: [
        .library(name: "PackPhysics", targets: ["PackPhysics"]),
        .executable(name: "packphysics", targets: ["packphysics"]),
    ],
    targets: [
        .target(name: "PackPhysics"),
        .executableTarget(name: "packphysics", dependencies: ["PackPhysics"], path: "Sources/PackPhysicsCLI"),
        .testTarget(
            name: "PackPhysicsTests",
            dependencies: ["PackPhysics"],
            resources: [.copy("Resources")]
        ),
    ],
    swiftLanguageVersions: [.v5]
)
