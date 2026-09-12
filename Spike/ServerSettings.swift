import Foundation

/// Where the app looks for the FastAPI server in `server/`.
///
/// The address lives in `UserDefaults` rather than in the source: it is a LAN IP
/// that changes whenever the server machine gets a new DHCP lease, and chasing
/// that with a rebuild-and-reinstall costs more than the change is worth.
enum ServerSettings {
    /// The `@AppStorage` / `UserDefaults` key. Shared with the settings field so
    /// edits there take effect immediately.
    static let baseURLKey = "serverBaseURL"

    static let defaultBaseURL = URL(string: "http://172.26.120.200:8000")!

    /// The address in use. Falls back to the default when nothing is stored, or
    /// when what is stored cannot address a host — a half-typed URL should not
    /// leave the app with no server at all.
    static var baseURL: URL {
        get { url(from: UserDefaults.standard.string(forKey: baseURLKey)) ?? defaultBaseURL }
        set { UserDefaults.standard.set(newValue.absoluteString, forKey: baseURLKey) }
    }

    /// Parses a typed address, or `nil` if it could not reach anything.
    ///
    /// `URL(string:)` alone is too permissive — it happily accepts "172.26.1.1",
    /// which has no scheme and no host and silently resolves to a relative path.
    static func url(from text: String?) -> URL? {
        guard let trimmed = text?.trimmingCharacters(in: .whitespacesAndNewlines),
              !trimmed.isEmpty,
              let url = URL(string: trimmed),
              let scheme = url.scheme?.lowercased(),
              scheme == "http" || scheme == "https",
              let host = url.host(), !host.isEmpty
        else { return nil }
        return url
    }
}
