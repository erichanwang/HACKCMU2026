// Stand-in for the UIKit symbols Spike/*.swift touches. Signatures from Apple's docs:
// https://developer.apple.com/documentation/uikit/uiview
// https://developer.apple.com/documentation/uikit/uigesturerecognizer
// https://developer.apple.com/documentation/uikit/uitapgesturerecognizer
// https://developer.apple.com/documentation/uikit/uicolor
// https://developer.apple.com/documentation/uikit/uiimage
// https://developer.apple.com/documentation/uikit/uikeyboardtype
//
// Actor isolation: as of the Xcode 16 SDK, Apple annotated UIResponder/UIView/UIGestureRecognizer
// (and its subclasses) `@MainActor`. This is load-bearing for the Coordinator concurrency check
// this harness exists to catch (see run.sh header) — a shim without these annotations would let
// a real Swift 6 data-race error through silently. UIColor and UIImage are NOT MainActor-isolated
// in the real SDK (both are usable off the main thread), so they are left nonisolated here.
//
// CGFloat/CGPoint/CGSize/CGRect need no shim at all: swift-corelibs-foundation already declares
// real versions of them on Linux (confirmed empirically — `import Foundation` alone resolves
// them). `@_exported import Foundation` below is what makes them visible to a file that only
// `import UIKit`s, matching how Apple's real UIKit re-exports CoreGraphics. Two members Linux's
// Foundation does NOT have are added below: `CGAffineTransform` and `CGRect.applying(_:)`
// (https://developer.apple.com/documentation/corefoundation/cgaffinetransform,
// https://developer.apple.com/documentation/corefoundation/cgrect/applying(_:)).
@_exported import Foundation

public struct CGAffineTransform {
    public init(scaleX sx: CGFloat, y sy: CGFloat) {}
}

extension CGRect {
    public func applying(_ t: CGAffineTransform) -> CGRect { fatalError() }
}

/// Real `Selector` lives in the ObjectiveC module, which does not exist without Objective-C
/// interop (unavailable on Linux — `#selector`/`@objc` cannot be used at all here; see run.sh's
/// "what this does NOT prove" section). Declared only so `UIGestureRecognizer.init(target:action:)`
/// has a matching parameter type; nothing can actually construct a `Selector` on this platform.
public struct Selector {}

@MainActor
open class UIResponder {
    public init() {}
}

@MainActor
open class UIView: UIResponder {
    public init(frame: CGRect) {}
    open var bounds: CGRect { fatalError() }
    open func addGestureRecognizer(_ gestureRecognizer: UIGestureRecognizer) {}
}

@MainActor
open class UIGestureRecognizer {
    public init(target: Any?, action: Selector?) {}
    public func location(in view: UIView?) -> CGPoint { fatalError() }
}

@MainActor
open class UITapGestureRecognizer: UIGestureRecognizer {}

/// Not MainActor in the real SDK: UIColor is safe to construct/use off the main thread.
open class UIColor {
    public static let systemGreen = UIColor()
    public static let systemBlue = UIColor()
    public static let systemOrange = UIColor()
    public static let systemPurple = UIColor()
    public static let systemTeal = UIColor()
    public static let systemPink = UIColor()
    public init() {}
    public func withAlphaComponent(_ alpha: CGFloat) -> UIColor { fatalError() }
}

/// Opaque bitmap handle (real CGImage: https://developer.apple.com/documentation/coregraphics/cgimage
/// — a CoreGraphics type, declared here since that's where it's first needed and Linux Foundation
/// re-exports CoreGraphics's other types from this same module already).
public final class CGImage {
    public func cropping(to rect: CGRect) -> CGImage? { fatalError() }
}

/// Not MainActor in the real SDK: UIImage is commonly decoded/processed off the main thread.
open class UIImage {
    public init(cgImage: CGImage) {}
    public var cgImage: CGImage? { fatalError() }
    public var scale: CGFloat { fatalError() }
    public func jpegData(compressionQuality: CGFloat) -> Data? { fatalError() }
}

public struct UIKeyboardType: Equatable {
    public static let URL = UIKeyboardType()
}
