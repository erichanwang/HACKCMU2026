// swift-tools-version: 5.9
import PackageDescription

// Pure logic extracted from Spike/ScanView.swift's tap handler: table-plane picking and
// scan-validity checks. No ARKit/RealityKit/UIKit, so it runs here on Linux even though
// ScanView.swift itself only builds on a Mac/device. Sources/RobustCore/ScanValidation.swift
// is a symlink to Spike/ScanValidation.swift -- one source of truth, no copy to drift.
let package = Package(
    name: "RobustCore",
    targets: [
        .target(name: "RobustCore"),
        .testTarget(name: "RobustCoreTests", dependencies: ["RobustCore"]),
    ]
)
