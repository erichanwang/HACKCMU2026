import Foundation
// PACKAR_TEST_ONLY (set by tests/swift/api/run.sh) compiles just the pure helpers below on Linux,
// without the PackingPlan package or UIKit.
#if !PACKAR_TEST_ONLY
import PackingPlan
#endif
#if canImport(FoundationNetworking)
import FoundationNetworking
#endif
#if canImport(UIKit)
import UIKit
#endif

private struct NewSuitcase: Encodable {
    let name: String
    let dimensions: [Float]  // [width, height, depth] in metres
}

private struct SuitcaseResponse: Decodable {
    let id: String
}

/// FastAPI reports every error as `{"detail": ...}`.
private struct ServerError: Decodable {
    let detail: String
}

/// Resolves the settings-sheet server URL text to a URL, falling back when it's blank, unparseable,
/// or has no host (e.g. no scheme). Pure so it's unit-testable on Linux without UIKit.
func resolveServerURL(typed: String, fallback: String) -> URL {
    let trimmed = typed.trimmingCharacters(in: .whitespaces)
    guard !trimmed.isEmpty,
          let url = URL(string: trimmed.contains("://") ? trimmed : "http://" + trimmed),
          url.host() != nil
    else {
        return URL(string: fallback) ?? URL(string: "http://172.26.48.172:8000")!
    }
    return url
}

/// Maps a non-2xx response body to a readable message: the server's `{"detail": ...}`, its raw text
/// when the body isn't that shape, or a status-only message when the body is empty or undecodable.
/// Pure so it's unit-testable on Linux without UIKit.
func serverErrorMessage(status: Int, body: Data) -> String {
    if body.isEmpty { return "server error (\(status))" }
    if let err = try? JSONDecoder().decode(ServerError.self, from: body) { return err.detail }
    if let text = String(data: body, encoding: .utf8), !text.isEmpty { return text }
    return "server error (\(status))"
}

/// Maps a transport-level failure (no response at all) to a readable message. Pure so it's
/// unit-testable on Linux without UIKit.
func networkErrorMessage(_ code: URLError.Code) -> String {
    switch code {
    case .cannotFindHost, .dnsLookupFailed: return "can't find that server — check the address in Settings"
    case .cannotConnectToHost, .networkConnectionLost: return "can't reach the server — is it running?"
    case .notConnectedToInternet: return "phone isn't on a network"
    case .timedOut: return "server took too long to respond"
    default: return "network error"
    }
}

#if !PACKAR_TEST_ONLY
/// The FastAPI server in server/. Set to this Mac's LAN IP in the app's settings sheet; phone and Mac
/// must share a Wi-Fi network. The PACKAR_SERVER environment variable (an Xcode scheme variable) is the
/// default when nothing has been typed in. The bearer token is optional: sent only when it is set.
enum API {
    static let defaultBase = ProcessInfo.processInfo.environment["PACKAR_SERVER"] ?? "http://172.26.48.172:8000"

    static var base: URL {
        resolveServerURL(typed: UserDefaults.standard.string(forKey: "serverURL") ?? "", fallback: defaultBase)
    }

    /// Every request. The Authorization header is omitted entirely when no token is set. `timeout`
    /// defaults to fast-fail for near-instant DB reads/writes; slower endpoints pass their own.
    private static func request(_ path: String, _ method: String, timeout: TimeInterval = 8) -> URLRequest {
        var req = URLRequest(url: base.appending(path: path))
        req.httpMethod = method
        req.timeoutInterval = timeout
        let token = (UserDefaults.standard.string(forKey: "authToken") ?? "").trimmingCharacters(in: .whitespaces)
        if !token.isEmpty { req.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization") }
        return req
    }

    static func createSuitcase(name: String, dimensions: [Float]) async throws -> String {
        var req = request("suitcases", "POST")
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        req.httpBody = try JSONEncoder().encode(NewSuitcase(name: name, dimensions: dimensions))
        let data = try await body(of: req)
        return try JSONDecoder().decode(SuitcaseResponse.self, from: data).id
    }

    /// One item the solver could not fit; alongside `plan` in the server's plan document.
    struct UnpackedItem: Decodable {
        let itemId: String
        let label: String
    }

    /// The document's top-level `unpacked` and `pendingLabels` fields. Both tolerated as absent
    /// (an older server).
    private struct PlanExtras: Decodable {
        let unpacked: [UnpackedItem]?
        let pendingLabels: Int?
    }

    /// Runs the solver server-side and returns what it actually produced, plus any items it
    /// couldn't fit and how many were packed while still waiting for a label.
    /// Throws with the server's message ("suitcase has no scanned items to pack")
    /// when there is nothing to pack.
    /// Timeout is 15s: the solver itself budgets 3s of CPU (server/planner.py TIME_BUDGET_S), so
    /// this leaves generous margin without matching URLSession's silent 60s default.
    static func plan(suitcaseId: String) async throws -> (plan: PackingPlan, unpacked: [UnpackedItem], pendingLabels: Int) {
        let req = request("suitcases/\(suitcaseId)/plan", "POST", timeout: 15)
        let data = try await body(of: req)
        let plan = try PlanLoader.plan(fromServerDocument: data)
        let extras = try? JSONDecoder().decode(PlanExtras.self, from: data)
        return (plan, extras?.unpacked ?? [], extras?.pendingLabels ?? 0)
    }

    #if canImport(UIKit)
    /// `async=1`: the server saves the scan and labels it in the background, so the reply comes back
    /// with `labelStatus: "pending"` and ScanView's polling fills the label in.
    /// Timeout stays 40s — it now only covers sending the photo over Wi-Fi.
    static func upload(_ item: ScannedItem, image: UIImage) async throws -> ScannedItem {
        guard let jpeg = image.jpegData(compressionQuality: 0.7) else {
            throw URLError(.cannotCreateFile, userInfo: [NSLocalizedDescriptionKey: "couldn't encode the photo"])
        }
        let boundary = "suitcase-\(UUID().uuidString)"
        var req = request("items", "POST", timeout: 40)
        req.url = req.url?.appending(queryItems: [URLQueryItem(name: "async", value: "1")])
        req.setValue("multipart/form-data; boundary=\(boundary)", forHTTPHeaderField: "Content-Type")
        var body = Data()
        body.append("--\(boundary)\r\nContent-Disposition: form-data; name=\"item\"\r\n\r\n".data(using: .utf8)!)
        body.append(try JSONEncoder().encode(item))
        body.append("\r\n--\(boundary)\r\nContent-Disposition: form-data; name=\"image\"; filename=\"o.jpg\"\r\nContent-Type: image/jpeg\r\n\r\n".data(using: .utf8)!)
        body.append(jpeg)
        body.append("\r\n--\(boundary)--\r\n".data(using: .utf8)!)
        req.httpBody = body
        return try await send(req)
    }
    #endif

    /// The public item document, re-read while the server retries a failed Grok call.
    static func get(id: String) async throws -> ScannedItem {
        try await send(request("items/\(id)", "GET"))
    }

    /// Everything scanned into one suitcase, for the Items sheet.
    static func items(suitcaseId: String) async throws -> [ScannedItem] {
        var req = request("items", "GET")
        req.url = req.url?.appending(queryItems: [URLQueryItem(name: "suitcaseId", value: suitcaseId)])
        return try JSONDecoder().decode([ScannedItem].self, from: try await body(of: req))
    }

    /// Everything the signed-in user has ever scanned, across suitcases, newest first; the
    /// server keeps these when a suitcase is deleted, so this is the Inventory sheet's source.
    static func inventory() async throws -> [ScannedItem] {
        try JSONDecoder().decode([ScannedItem].self, from: try await body(of: request("inventory", "GET")))
    }

    /// Removes one item (and the suitcase's stored plan, which no longer matches).
    static func delete(itemId: String) async throws {
        _ = try await body(of: request("items/\(itemId)", "DELETE"))
    }

    /// Removes a suitcase and its plan — the app's Reset. Its items are detached and stay in the inventory.
    static func deleteSuitcase(id: String) async throws {
        _ = try await body(of: request("suitcases/\(id)", "DELETE"))
    }

    static func update(id: String, label: String?, rigidity: String?) async throws -> ScannedItem {
        var req = request("items/\(id)", "PATCH")
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        var fields: [String: String] = [:]
        if let label { fields["label"] = label }
        if let rigidity { fields["rigidity"] = rigidity }
        req.httpBody = try JSONEncoder().encode(fields)
        return try await send(req)
    }

    private static func send(_ req: URLRequest) async throws -> ScannedItem {
        let data = try await body(of: req)
        return try JSONDecoder().decode(ScannedItem.self, from: data)
    }

    private static func body(of req: URLRequest) async throws -> Data {
        let data: Data
        let resp: URLResponse
        do {
            (data, resp) = try await URLSession.shared.data(for: req)
        } catch let error as URLError {
            throw URLError(error.code, userInfo: [NSLocalizedDescriptionKey: networkErrorMessage(error.code)])
        }
        let status = (resp as? HTTPURLResponse)?.statusCode ?? 0
        guard status == 200 else {
            throw URLError(.badServerResponse, userInfo: [NSLocalizedDescriptionKey: serverErrorMessage(status: status, body: data)])
        }
        return data
    }
}
#endif
