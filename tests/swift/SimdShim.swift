// Linux has no `simd` module. The app's pure geometry (Spike/Geometry.swift) uses only
// these few functions, so this shim lets it compile and run under `swiftc` on Linux for
// tests and simulations. On Apple platforms `canImport(simd)` is true and this is empty.
#if !canImport(simd)
func simd_dot(_ a: SIMD2<Float>, _ b: SIMD2<Float>) -> Float { (a * b).sum() }
func simd_dot(_ a: SIMD3<Float>, _ b: SIMD3<Float>) -> Float { (a * b).sum() }
func simd_length(_ a: SIMD2<Float>) -> Float { simd_dot(a, a).squareRoot() }
func simd_length(_ a: SIMD3<Float>) -> Float { simd_dot(a, a).squareRoot() }
func simd_normalize(_ a: SIMD3<Float>) -> SIMD3<Float> { a / simd_length(a) }
func simd_cross(_ a: SIMD3<Float>, _ b: SIMD3<Float>) -> SIMD3<Float> {
    SIMD3(a.y * b.z - a.z * b.y, a.z * b.x - a.x * b.z, a.x * b.y - a.y * b.x)
}

struct simd_float2x2 {
    var columns: (SIMD2<Float>, SIMD2<Float>)
    init(_ c0: SIMD2<Float>, _ c1: SIMD2<Float>) { columns = (c0, c1) }
    static func * (m: simd_float2x2, v: SIMD2<Float>) -> SIMD2<Float> { m.columns.0 * v.x + m.columns.1 * v.y }
}
#endif
