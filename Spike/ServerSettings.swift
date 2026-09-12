import Foundation

/// Where the app looks for the FastAPI server in `server/`.
///
/// The address lives in `UserDefaults` rather than in the source: it is a LAN IP
/// that changes whenever the server machine gets a new DHCP lease, and chasing
/// that with a rebuild-and-reinstall costs more than the change is worth.
/// `SettingsSheet` edits it, and `API.base` re-reads it on every request, both
/// through the `serverURL` key.
enum ServerSettings {
    /// The address to fall back on when nothing has been typed and no
    /// `PACKAR_SERVER` is set in the scheme.
    ///
    /// `API.swift`'s own last-resort literal is deliberately left alone — it is
    /// pinned by `tests/swift/api/main.swift`, which compiles that file on Linux.
    static let shippedDefault = "http://172.26.120.200:8000"
}
