import Foundation

/// Where the app looks for the FastAPI server in `server/`.
///
/// The address lives in `UserDefaults` rather than in the source: it is a LAN IP
/// that changes whenever the server machine gets a new DHCP lease, and chasing
/// that with a rebuild-and-reinstall costs more than the change is worth.
///
/// Two screens edit it — the root list here and `SettingsSheet` — so both go
/// through this type and through `resolveServerURL` in `API.swift`. Sharing one
/// key and one resolver is the point: a settings sheet writing `serverURL` while
/// something else read `serverBaseURL` would look like the address simply not
/// taking effect.
enum ServerSettings {
    /// The `UserDefaults` key, matching what `API.base` and `SettingsSheet` use.
    static let baseURLKey = "serverURL"

    /// The address to fall back on when nothing has been typed and no
    /// `PACKAR_SERVER` is set in the scheme.
    ///
    /// `API.swift`'s own last-resort literal is deliberately left alone — it is
    /// pinned by `tests/swift/api/main.swift`, which compiles that file on Linux.
    static let shippedDefault = "http://172.26.120.200:8000"

    static var defaultBaseURL: URL {
        URL(string: API.defaultBase) ?? URL(string: shippedDefault)!
    }

    /// The address in use, resolved exactly the way `API` resolves it.
    static var baseURL: URL { API.base }

    /// Whether typed text can address a host — the check behind the root screen's
    /// warning. Kept in step with `resolveServerURL` by calling it: text is usable
    /// when resolving it does *not* land on the fallback.
    ///
    /// A bare `host:port` counts as usable; `resolveServerURL` supplies the
    /// scheme. That is a deliberate change from this side's earlier behaviour,
    /// which rejected it — main's is friendlier and is the tested one.
    static func url(from text: String?) -> URL? {
        let sentinel = "http://__unresolved__"
        let resolved = resolveServerURL(typed: text ?? "", fallback: sentinel)
        return resolved.absoluteString == sentinel ? nil : resolved
    }
}
