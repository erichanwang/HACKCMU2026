// swift-tools-version: 5.9
import PackageDescription

// plan3d renders a PackingPlan to SVG on the command line. It exists because the
// interactive 3D view in packing-core is SwiftUI and cannot be built, let alone
// looked at, on Linux — this is how the projection gets checked here. See
// docs/PLAN_3D.md.
//
// It links PackingPlanUI as well as PackingPlan: the layer grouping, the
// protrusion query and the footprint projection behind `--layers` live there, and
// they compile on Linux because that target's SwiftUI and CoreGraphics imports
// are both guarded. The views themselves are inside those guards and are not
// visible here — only the layout logic they call, which is the point.
let package = Package(
    name: "plan3d",
    platforms: [.macOS(.v14)],
    dependencies: [
        // Package identity comes from the directory name, not Package(name:).
        .package(path: "../../packing-core")
    ],
    targets: [
        .executableTarget(
            name: "plan3d",
            dependencies: [
                .product(name: "PackingPlan", package: "packing-core"),
                .product(name: "PackingPlanUI", package: "packing-core"),
            ]
        )
    ]
)
