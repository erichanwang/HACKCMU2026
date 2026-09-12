// Minimal stand-in for SwiftUI. Deliberately narrow — see run.sh and the report's "what this does
// NOT cover" section: this shim only supports enough of SwiftUI to typecheck Spike/ScanView.swift
// (a UIViewRepresentable with no `body` of its own — see its file for why that sidesteps
// @ViewBuilder entirely). It does NOT attempt View/@ViewBuilder/opaque-return-type fidelity for
// SwiftUI-heavy view trees (ZStack/VStack/Picker/... chains) — that is out of scope; Spike/
// SpikeApp.swift is only `-parse`d, not typechecked, for that reason.
//
// Signatures from Apple's docs:
// https://developer.apple.com/documentation/swiftui/view
// https://developer.apple.com/documentation/swiftui/binding
// https://developer.apple.com/documentation/swiftui/uiviewrepresentable
// https://developer.apple.com/documentation/swiftui/uiviewrepresentablecontext
//
// Actor isolation: SwiftUI's `View` protocol and `UIViewRepresentable` (including its
// makeUIView/updateUIView/makeCoordinator requirements) are `@MainActor` in the real SDK. That
// matters here because `ScanView.makeCoordinator()` runs on the main actor and constructs
// `Coordinator` — but `Coordinator` itself is a plain, non-@MainActor `NSObject` (see
// RealityKitShim.swift's header for the other half of this).

import UIKit

@MainActor
public protocol View {
    associatedtype Body: View
    @ViewBuilder var body: Self.Body { get }
}

extension Never: View {
    public typealias Body = Never
    public var body: Never { fatalError() }
}

@resultBuilder
public enum ViewBuilder {
    public static func buildBlock<Content: View>(_ content: Content) -> Content { content }
}

@propertyWrapper
public struct Binding<Value> {
    private let get: () -> Value
    private let set: (Value) -> Void
    public var wrappedValue: Value {
        get { get() }
        nonmutating set { set(newValue) }
    }
    public var projectedValue: Binding<Value> { self }
    public init(get: @escaping () -> Value, set: @escaping (Value) -> Void) {
        self.get = get; self.set = set
    }
}

@MainActor
public protocol UIViewRepresentable: View where Body == Never {
    associatedtype UIViewType: UIView
    associatedtype Coordinator = Void
    typealias Context = UIViewRepresentableContext<Self>
    func makeUIView(context: Self.Context) -> Self.UIViewType
    func updateUIView(_ uiView: Self.UIViewType, context: Self.Context)
    func makeCoordinator() -> Self.Coordinator
}

extension UIViewRepresentable {
    public var body: Never { fatalError() }
}

public struct UIViewRepresentableContext<Representable> where Representable: UIViewRepresentable {
    public let coordinator: Representable.Coordinator
}
