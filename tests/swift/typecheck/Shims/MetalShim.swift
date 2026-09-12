// Stand-in for the one Metal symbol ARKit's ARGeometrySource/ARGeometryElement expose:
// MTLBuffer, from https://developer.apple.com/documentation/metal/mtlbuffer
// `contents()` is documented as returning `UnsafeMutableRawPointer`.
//
// MTLBuffer is a protocol in the real SDK, not a class; not actor-isolated (it's a plain
// data-holder, called from any thread in real usage).
public protocol MTLBuffer: AnyObject {
    func contents() -> UnsafeMutableRawPointer
}
