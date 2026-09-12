import CoreVideo
import Foundation
import simd

/// Reads the colour of a world point out of one posed camera frame.
///
/// The idea is borrowed from photogrammetry pipelines -- project a 3D point through the
/// camera intrinsics and sample the pixel it lands on -- but that is the whole of the
/// borrowing. There is no reconstruction here: LiDAR already supplies the geometry, so
/// this only has to answer "what colour is that surface", which runs on the phone in a
/// few microseconds per point instead of minutes on a GPU.
///
/// Kept free of ARKit so the projection maths is testable on the host; `ScanView` builds
/// a `PosedFrame` from `ARFrame` and hands it over.
struct PosedFrame {
    /// World -> camera. ARKit camera space is +X right, +Y up, -Z forward.
    let viewMatrix: simd_float4x4
    /// Pinhole intrinsics for `imageSize`: [[fx,0,0],[0,fy,0],[cx,cy,1]] in column-major.
    let intrinsics: simd_float3x3
    /// Native resolution the intrinsics belong to, pixels.
    let imageSize: SIMD2<Float>

    var fx: Float { intrinsics[0][0] }
    var fy: Float { intrinsics[1][1] }
    var cx: Float { intrinsics[2][0] }
    var cy: Float { intrinsics[2][1] }

    /// Pixel coordinate a world point lands on, or nil if it is behind the camera or
    /// outside the frame.
    ///
    /// ARKit looks down -Z, so forward depth is `-z`. The intrinsics expect a +Z-forward,
    /// Y-down image frame, hence the single sign flip on y.
    func project(_ world: SIMD3<Float>) -> SIMD2<Float>? {
        let p = viewMatrix * SIMD4<Float>(world, 1)
        let depth = -p.z
        guard depth > 0.01 else { return nil }          // behind, or all but touching the lens
        let u = fx * (p.x / depth) + cx
        let v = fy * (-p.y / depth) + cy
        guard u >= 0, v >= 0, u < imageSize.x, v < imageSize.y else { return nil }
        return SIMD2(u, v)
    }

    /// Unit vector from a point towards the camera, for capture-coverage tracking.
    func directionToCamera(from world: SIMD3<Float>) -> SIMD3<Float> {
        let inv = viewMatrix.inverse
        let eye = SIMD3<Float>(inv.columns.3.x, inv.columns.3.y, inv.columns.3.z)
        let d = eye - world
        return simd_length(d) > 1e-6 ? simd_normalize(d) : SIMD3(0, 1, 0)
    }
}

/// Samples `capturedImage` pixels. ARKit hands back biplanar YCbCr, so this walks both
/// planes and converts; there is no RGB buffer to read directly.
///
/// Lock the buffer once around a batch of `colour(at:)` calls -- locking per point would
/// cost far more than the sampling itself.
struct PixelSampler {
    private let buffer: CVPixelBuffer
    private let lumaBase: UnsafeMutableRawPointer
    private let lumaStride: Int
    private let chromaBase: UnsafeMutableRawPointer
    private let chromaStride: Int
    let width: Int
    let height: Int

    /// Fails if the buffer is not the biplanar YCbCr layout ARKit normally provides.
    init?(_ pixelBuffer: CVPixelBuffer) {
        guard CVPixelBufferGetPlaneCount(pixelBuffer) >= 2 else { return nil }
        guard CVPixelBufferLockBaseAddress(pixelBuffer, .readOnly) == kCVReturnSuccess else { return nil }
        guard let y = CVPixelBufferGetBaseAddressOfPlane(pixelBuffer, 0),
              let c = CVPixelBufferGetBaseAddressOfPlane(pixelBuffer, 1) else {
            CVPixelBufferUnlockBaseAddress(pixelBuffer, .readOnly)
            return nil
        }
        buffer = pixelBuffer
        lumaBase = y
        chromaBase = c
        lumaStride = CVPixelBufferGetBytesPerRowOfPlane(pixelBuffer, 0)
        chromaStride = CVPixelBufferGetBytesPerRowOfPlane(pixelBuffer, 1)
        width = CVPixelBufferGetWidthOfPlane(pixelBuffer, 0)
        height = CVPixelBufferGetHeightOfPlane(pixelBuffer, 0)
    }

    /// Must be called once done; `PixelSampler` deliberately does not unlock in `deinit`
    /// because it is a struct held for the duration of one frame's sampling.
    func release() {
        CVPixelBufferUnlockBaseAddress(buffer, .readOnly)
    }

    /// Linear-ish RGB in 0...1 at a pixel, or nil if out of bounds.
    /// BT.601 full range, which is what ARKit's full-range biplanar format carries.
    func colour(at p: SIMD2<Float>) -> SIMD3<Float>? {
        let x = Int(p.x), y = Int(p.y)
        guard x >= 0, y >= 0, x < width, y < height else { return nil }

        let luma = lumaBase.load(fromByteOffset: y * lumaStride + x, as: UInt8.self)
        // Chroma is half resolution in both axes, with Cb and Cr interleaved.
        let cOffset = (y / 2) * chromaStride + (x / 2) * 2
        let cb = chromaBase.load(fromByteOffset: cOffset, as: UInt8.self)
        let cr = chromaBase.load(fromByteOffset: cOffset + 1, as: UInt8.self)

        let Y = Float(luma), Cb = Float(cb) - 128, Cr = Float(cr) - 128
        let r = (Y + 1.402 * Cr) / 255
        let g = (Y - 0.344136 * Cb - 0.714136 * Cr) / 255
        let b = (Y + 1.772 * Cb) / 255
        return simd_clamp(SIMD3(r, g, b), SIMD3(repeating: 0), SIMD3(repeating: 1))
    }
}
