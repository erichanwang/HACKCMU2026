// Linux has no `simd` module (real Apple docs: https://developer.apple.com/documentation/simd,
// https://developer.apple.com/documentation/simd/simd_float4x4, https://developer.apple.com/
// documentation/simd/simd_quatf). This is a self-contained shim scoped to this typecheck harness
// — it duplicates a couple of functions already in tests/swift/SimdShim.swift because that file
// is compiled into other, separate test binaries and this harness does not link against them.
//
// None of these are actor-isolated in the real SDK (they're plain math value types usable from
// any thread).
//
// This file is built as its own module named `simd` (see run.sh) so that Spike/Geometry.swift's
// `#if canImport(simd) import simd #endif` takes the real-module branch, not the
// tests/swift/SimdShim.swift fallback branch.
public func simd_dot(_ a: SIMD2<Float>, _ b: SIMD2<Float>) -> Float { (a * b).sum() }
public func simd_dot(_ a: SIMD3<Float>, _ b: SIMD3<Float>) -> Float { (a * b).sum() }
public func simd_length(_ a: SIMD2<Float>) -> Float { simd_dot(a, a).squareRoot() }
public func simd_length(_ a: SIMD3<Float>) -> Float { simd_dot(a, a).squareRoot() }
public func simd_normalize(_ a: SIMD3<Float>) -> SIMD3<Float> { a / simd_length(a) }
public func simd_cross(_ a: SIMD3<Float>, _ b: SIMD3<Float>) -> SIMD3<Float> {
    SIMD3(a.y * b.z - a.z * b.y, a.z * b.x - a.x * b.z, a.x * b.y - a.y * b.x)
}

public struct simd_float2x2 {
    public var columns: (SIMD2<Float>, SIMD2<Float>)
    public init(_ c0: SIMD2<Float>, _ c1: SIMD2<Float>) { columns = (c0, c1) }
    public static func * (m: simd_float2x2, v: SIMD2<Float>) -> SIMD2<Float> { m.columns.0 * v.x + m.columns.1 * v.y }
}

public struct simd_float4x4 {
    public var columns: (SIMD4<Float>, SIMD4<Float>, SIMD4<Float>, SIMD4<Float>)
    public init(columns: (SIMD4<Float>, SIMD4<Float>, SIMD4<Float>, SIMD4<Float>)) { self.columns = columns }
    public static func * (m: simd_float4x4, v: SIMD4<Float>) -> SIMD4<Float> {
        m.columns.0 * v.x + m.columns.1 * v.y + m.columns.2 * v.z + m.columns.3 * v.w
    }
}

/// Real simd_quatf is a float-precision quaternion; only the two initializers ScanView.swift
/// uses (default/identity, and `from:to:` shortest-rotation) are declared.
public struct simd_quatf {
    public init() {}
    public init(from: SIMD3<Float>, to: SIMD3<Float>) {}
}
