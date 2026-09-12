// swift-tools-version: 5.9
import PackageDescription

// PackingPlan is the pure-geometry core of the HackCMU packing app: plan models,
// loader, validation. Deliberately free of ARKit/RealityKit *and* SwiftUI so the
// invariants in CLAUDE.md are testable on the host with `swift test` — no
// simulator, no camera.
//
// PackingPlanUI holds the 2D fallback rendering. It imports SwiftUI but still no
// ARKit/RealityKit, so it runs in the simulator with no camera and previews on
// the host. AR code belongs in the app target, not here.
//
// macOS 14 is a host-side floor only (it is what `#Preview` needs); iOS 17 is the
// real deployment target.
let package = Package(
    name: "PackingPlan",
    platforms: [.iOS(.v17), .macOS(.v14)],
    products: [
        .library(name: "PackingPlan", targets: ["PackingPlan"]),
        .library(name: "PackingPlanUI", targets: ["PackingPlanUI"]),
    ],
    targets: [
        .target(
            name: "PackingPlan",
            resources: [.process("Resources")]
        ),
        .target(
            name: "PackingPlanUI",
            dependencies: ["PackingPlan"]
        ),
        .testTarget(
            name: "PackingPlanTests",
            dependencies: ["PackingPlan"],
            resources: [.copy("Fixtures")]
        ),
        .testTarget(
            name: "PackingPlanUITests",
            dependencies: ["PackingPlanUI", "PackingPlan"]
        ),
    ]
)
