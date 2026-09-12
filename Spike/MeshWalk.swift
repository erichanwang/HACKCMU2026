// Pure re-implementation of the ARGeometrySource/ARGeometryElement vertex+index walk that
// ARMeshAnchor.geometry exposes, so it can be unit-tested on Linux without ARKit. No ARKit
// import here — see tests/swift/mesh/run.sh.
//
// Facts this relies on, and where they come from (Apple's docs, cross-checked against
// developer.apple.com/forums/thread/130724 "ARMeshGeometry to Model I/O"):
// - ARGeometryElement.count is the number of PRIMITIVES (triangles), not the number of indices.
// - ARGeometryElement.bytesPerIndex is 2 or 4 — not guaranteed to be 4 (UInt32).
// - ARGeometrySource vertices are tightly packed: stride == componentsPerVector * 4 (12 bytes
//   for a float3 vertex), NOT padded to SIMD3<Float>'s 16-byte in-memory size. Binding a raw
//   pointer straight to SIMD3<Float> and reading `.pointee` therefore over-reads 4 bytes past
//   every vertex (and past the end of the buffer for the last one) — read three Float32s
//   instead and assemble the vector by hand.

/// Reads one triangle-corner index from a tightly packed index buffer, honoring `bytesPerIndex`
/// (2 for UInt16, 4 for UInt32 — ARKit does not guarantee 4).
func readMeshIndex(_ buffer: UnsafeRawPointer, primitive: Int, corner: Int, indexCountPerPrimitive: Int, bytesPerIndex: Int) -> UInt32 {
    let byteOffset = (primitive * indexCountPerPrimitive + corner) * bytesPerIndex
    switch bytesPerIndex {
    case 2: return UInt32(buffer.load(fromByteOffset: byteOffset, as: UInt16.self))
    default: return buffer.load(fromByteOffset: byteOffset, as: UInt32.self)
    }
}

/// Reads one vertex as three individual Float32s at `offset + stride * index` — never binds
/// the raw pointer to SIMD3<Float> directly (see file header).
func readMeshVertex(_ buffer: UnsafeRawPointer, offset: Int, stride: Int, index: UInt32) -> SIMD3<Float> {
    let base = offset + stride * Int(index)
    let x = buffer.load(fromByteOffset: base, as: Float.self)
    let y = buffer.load(fromByteOffset: base + 4, as: Float.self)
    let z = buffer.load(fromByteOffset: base + 8, as: Float.self)
    return SIMD3<Float>(x, y, z)
}

/// Walks every triangle in `count` primitives, resolving each corner's vertex through
/// `worldVertex` (anchor space -> world space, e.g. `{ mesh.transform * SIMD4($0, 1) }.xyz`).
/// Returns [] for a non-triangle element (`indexCountPerPrimitive != 3`) instead of misreading it.
func meshTriangles(
    vertexBuffer: UnsafeRawPointer, vertexOffset: Int, vertexStride: Int,
    indexBuffer: UnsafeRawPointer, primitiveCount: Int, indexCountPerPrimitive: Int, bytesPerIndex: Int,
    worldVertex: (SIMD3<Float>) -> SIMD3<Float>
) -> [(SIMD3<Float>, SIMD3<Float>, SIMD3<Float>)] {
    guard indexCountPerPrimitive == 3 else { return [] }
    var triangles: [(SIMD3<Float>, SIMD3<Float>, SIMD3<Float>)] = []
    triangles.reserveCapacity(primitiveCount)
    for t in 0..<primitiveCount {
        func corner(_ c: Int) -> SIMD3<Float> {
            let i = readMeshIndex(indexBuffer, primitive: t, corner: c, indexCountPerPrimitive: indexCountPerPrimitive, bytesPerIndex: bytesPerIndex)
            return worldVertex(readMeshVertex(vertexBuffer, offset: vertexOffset, stride: vertexStride, index: i))
        }
        triangles.append((corner(0), corner(1), corner(2)))
    }
    return triangles
}
