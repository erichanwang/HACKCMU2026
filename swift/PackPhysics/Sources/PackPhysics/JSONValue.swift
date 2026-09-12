// Minimal JSON value type so validation results can carry the exact dictionary
// shapes the Python layer emits (and the renderer / solver already consume),
// and so Swift-vs-Python parity can be checked structurally.

import Foundation

// `Sendable` is explicit (not inferred) because a Swift 5 language-mode module
// grants no implicit `Sendable` to its public types across the module boundary:
// without it a Swift 6 / strict-concurrency app cannot return a
// `ValidationResult` from a background task to the main actor, which is exactly
// how the iOS app calls `validateLayout`. Both types are immutable value trees,
// so the conformance is sound.
public enum JSONValue: Equatable, Hashable, Sendable {
    case null
    case bool(Bool)
    case number(Double)
    case string(String)
    case array([JSONValue])
    case object([String: JSONValue])

    public subscript(key: String) -> JSONValue? {
        if case let .object(o) = self { return o[key] }
        return nil
    }

    public var doubleValue: Double? {
        if case let .number(d) = self { return d }
        return nil
    }

    public var stringValue: String? {
        if case let .string(s) = self { return s }
        return nil
    }

    public var boolValue: Bool? {
        if case let .bool(b) = self { return b }
        return nil
    }

    public var arrayValue: [JSONValue]? {
        if case let .array(a) = self { return a }
        return nil
    }

    public var objectValue: [String: JSONValue]? {
        if case let .object(o) = self { return o }
        return nil
    }
}

extension JSONValue: Codable {
    public init(from decoder: Decoder) throws {
        let c = try decoder.singleValueContainer()
        if c.decodeNil() { self = .null; return }
        if let b = try? c.decode(Bool.self) { self = .bool(b); return }
        if let d = try? c.decode(Double.self) { self = .number(d); return }
        if let s = try? c.decode(String.self) { self = .string(s); return }
        if let a = try? c.decode([JSONValue].self) { self = .array(a); return }
        if let o = try? c.decode([String: JSONValue].self) { self = .object(o); return }
        throw DecodingError.dataCorruptedError(in: c, debugDescription: "unsupported JSON value")
    }

    public func encode(to encoder: Encoder) throws {
        var c = encoder.singleValueContainer()
        switch self {
        case .null: try c.encodeNil()
        case let .bool(b): try c.encode(b)
        case let .number(d): try c.encode(d)
        case let .string(s): try c.encode(s)
        case let .array(a): try c.encode(a)
        case let .object(o): try c.encode(o)
        }
    }
}

// Convenience literals so result dictionaries read naturally in code.
extension JSONValue: ExpressibleByStringLiteral, ExpressibleByBooleanLiteral,
    ExpressibleByFloatLiteral, ExpressibleByIntegerLiteral, ExpressibleByNilLiteral,
    ExpressibleByArrayLiteral, ExpressibleByDictionaryLiteral {
    public init(stringLiteral value: String) { self = .string(value) }
    public init(booleanLiteral value: Bool) { self = .bool(value) }
    public init(floatLiteral value: Double) { self = .number(value) }
    public init(integerLiteral value: Int) { self = .number(Double(value)) }
    public init(nilLiteral: ()) { self = .null }
    public init(arrayLiteral elements: JSONValue...) { self = .array(elements) }
    public init(dictionaryLiteral elements: (String, JSONValue)...) {
        self = .object(Dictionary(uniqueKeysWithValues: elements))
    }
}

public extension JSONValue {
    static func vec(_ v: Vec3) -> JSONValue { .array([.number(v.x), .number(v.y), .number(v.z)]) }
    static func strings(_ s: [String]) -> JSONValue { .array(s.map { .string($0) }) }
}

/// Output of `validateLayout`. Same top-level keys and entry dictionaries as the
/// Python `validate_layout` (see docs/PHYSICS.md §10), so one renderer contract
/// serves both implementations.
public struct ValidationResult: Codable, Equatable, Sendable {
    public var valid: Bool
    public var score: Double
    public var violations: [JSONValue]
    public var warnings: [JSONValue]
    public var metrics: JSONValue

    public init(valid: Bool, score: Double, violations: [JSONValue], warnings: [JSONValue], metrics: JSONValue) {
        self.valid = valid
        self.score = score
        self.violations = violations
        self.warnings = warnings
        self.metrics = metrics
    }

    public func asJSON() -> JSONValue {
        .object([
            "valid": .bool(valid),
            "score": .number(score),
            "violations": .array(violations),
            "warnings": .array(warnings),
            "metrics": metrics,
        ])
    }
}
