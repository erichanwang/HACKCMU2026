import Foundation

func approx(_ a: Float, _ b: Float, _ tol: Float = 1e-6) -> Bool { abs(a - b) < tol }

// Nine vertices (3 triangles' worth), written as raw bytes exactly like an
// ARGeometrySource buffer: `offset` leading padding bytes, then 3 floats per vertex spaced
// `stride` bytes apart (stride may be > 12, i.e. padded, same as componentsPerVector*4 with
// slack — the code must use the given stride, not assume 12 or sizeof(SIMD3<Float>)==16).
let vertices: [SIMD3<Float>] = (0..<9).map { SIMD3<Float>(Float($0), Float($0) * 2 + 1, Float($0) * 3 + 2) }

func vertexBuffer(offset: Int, stride: Int) -> [UInt8] {
    var bytes = [UInt8](repeating: 0xAA, count: offset + stride * vertices.count)
    for (i, v) in vertices.enumerated() {
        let base = offset + stride * i
        withUnsafeBytes(of: v.x) { bytes.replaceSubrange(base..<base+4, with: $0) }
        withUnsafeBytes(of: v.y) { bytes.replaceSubrange(base+4..<base+8, with: $0) }
        withUnsafeBytes(of: v.z) { bytes.replaceSubrange(base+8..<base+12, with: $0) }
    }
    return bytes
}

// 3 triangles over the 9 vertices: (0,1,2), (3,4,5), (6,7,8).
let indices: [UInt32] = (0..<9).map { UInt32($0) }

func indexBuffer(bytesPerIndex: Int) -> [UInt8] {
    var bytes = [UInt8](repeating: 0, count: bytesPerIndex * indices.count)
    for (i, idx) in indices.enumerated() {
        let base = i * bytesPerIndex
        if bytesPerIndex == 2 {
            withUnsafeBytes(of: UInt16(idx)) { bytes.replaceSubrange(base..<base+2, with: $0) }
        } else {
            withUnsafeBytes(of: idx) { bytes.replaceSubrange(base..<base+4, with: $0) }
        }
    }
    return bytes
}

func check(vertexStride: Int, vertexOffset: Int, bytesPerIndex: Int) {
    let vBytes = vertexBuffer(offset: vertexOffset, stride: vertexStride)
    let iBytes = indexBuffer(bytesPerIndex: bytesPerIndex)
    let triangles = vBytes.withUnsafeBytes { vRaw -> [(SIMD3<Float>, SIMD3<Float>, SIMD3<Float>)] in
        iBytes.withUnsafeBytes { iRaw in
            meshTriangles(
                vertexBuffer: vRaw.baseAddress!, vertexOffset: vertexOffset, vertexStride: vertexStride,
                indexBuffer: iRaw.baseAddress!, primitiveCount: 3, indexCountPerPrimitive: 3, bytesPerIndex: bytesPerIndex,
                worldVertex: { $0 } // identity transform for this check
            )
        }
    }
    let label = "stride=\(vertexStride) offset=\(vertexOffset) bytesPerIndex=\(bytesPerIndex)"
    assert(triangles.count == 3, "\(label): expected 3 triangles, got \(triangles.count)")
    for t in 0..<3 {
        let (a, b, c) = triangles[t]
        let (ea, eb, ec) = (vertices[t * 3], vertices[t * 3 + 1], vertices[t * 3 + 2])
        assert(approx(a.x, ea.x) && approx(a.y, ea.y) && approx(a.z, ea.z), "\(label): triangle \(t) corner a mismatch \(a) vs \(ea)")
        assert(approx(b.x, eb.x) && approx(b.y, eb.y) && approx(b.z, eb.z), "\(label): triangle \(t) corner b mismatch \(b) vs \(eb)")
        assert(approx(c.x, ec.x) && approx(c.y, ec.y) && approx(c.z, ec.z), "\(label): triangle \(t) corner c mismatch \(c) vs \(ec)")
    }
    print("ok: \(label)")
}

// Both documented stride possibilities (tightly packed 12, and a padded 16), both offsets,
// both index widths ARKit allows.
for stride in [12, 16] {
    for offset in [0, 4] {
        for bytesPerIndex in [2, 4] {
            check(vertexStride: stride, vertexOffset: offset, bytesPerIndex: bytesPerIndex)
        }
    }
}

// A non-triangle element must not be misread as triangles.
let empty = meshTriangles(
    vertexBuffer: [UInt8](repeating: 0, count: 12).withUnsafeBytes { $0.baseAddress! },
    vertexOffset: 0, vertexStride: 12,
    indexBuffer: [UInt8](repeating: 0, count: 4).withUnsafeBytes { $0.baseAddress! },
    primitiveCount: 1, indexCountPerPrimitive: 2, bytesPerIndex: 4,
    worldVertex: { $0 }
)
assert(empty.isEmpty, "non-triangle element should yield no triangles, got \(empty.count)")
print("ok: indexCountPerPrimitive != 3 yields no triangles")

// worldVertex transform is actually applied (anchor -> world), not skipped.
let translated = vertexBuffer(offset: 0, stride: 12).withUnsafeBytes { vRaw -> [(SIMD3<Float>, SIMD3<Float>, SIMD3<Float>)] in
    indexBuffer(bytesPerIndex: 4).withUnsafeBytes { iRaw in
        meshTriangles(
            vertexBuffer: vRaw.baseAddress!, vertexOffset: 0, vertexStride: 12,
            indexBuffer: iRaw.baseAddress!, primitiveCount: 1, indexCountPerPrimitive: 3, bytesPerIndex: 4,
            worldVertex: { $0 + SIMD3<Float>(10, 0, 0) }
        )
    }
}
assert(approx(translated[0].0.x, vertices[0].x + 10), "worldVertex transform not applied")
print("ok: worldVertex transform applied")

print("all mesh walk checks passed")
