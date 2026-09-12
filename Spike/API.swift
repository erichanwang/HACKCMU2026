import Foundation
import PackingPlan
import UIKit

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

/// The FastAPI server in server/. Set to this Mac's LAN IP in the app's settings sheet; phone and Mac
/// must share a Wi-Fi network. The PACKAR_SERVER environment variable (an Xcode scheme variable) is the
/// default when nothing has been typed in. The bearer token is optional: sent only when it is set.
enum API {
    static let defaultBase = ProcessInfo.processInfo.environment["PACKAR_SERVER"] ?? "http://172.26.48.172:8000"

    static var base: URL {
        let typed = (UserDefaults.standard.string(forKey: "serverURL") ?? "").trimmingCharacters(in: .whitespaces)
        guard let url = URL(string: typed.contains("://") ? typed : "http://" + typed), url.host() != nil else {
            return URL(string: defaultBase) ?? URL(string: "http://172.26.48.172:8000")!
        }
        return url
    }

    /// Every request. The Authorization header is omitted entirely when no token is set.
    private static func request(_ path: String, _ method: String) -> URLRequest {
        var req = URLRequest(url: base.appending(path: path))
        req.httpMethod = method
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

    /// Runs the solver server-side and returns what it actually produced. Throws with the
    /// server's message ("suitcase has no scanned items to pack") when there is nothing to pack.
    static func plan(suitcaseId: String) async throws -> PackingPlan {
        let req = request("suitcases/\(suitcaseId)/plan", "POST")
        let data = try await body(of: req)
        return try PlanLoader.plan(fromServerDocument: data)
    }

    static func upload(_ item: ScannedItem, image: UIImage) async throws -> ScannedItem {
        let boundary = "suitcase-\(UUID().uuidString)"
        var req = request("items", "POST")
        req.setValue("multipart/form-data; boundary=\(boundary)", forHTTPHeaderField: "Content-Type")
        var body = Data()
        body.append("--\(boundary)\r\nContent-Disposition: form-data; name=\"item\"\r\n\r\n".data(using: .utf8)!)
        body.append(try JSONEncoder().encode(item))
        body.append("\r\n--\(boundary)\r\nContent-Disposition: form-data; name=\"image\"; filename=\"o.jpg\"\r\nContent-Type: image/jpeg\r\n\r\n".data(using: .utf8)!)
        body.append(image.jpegData(compressionQuality: 0.7)!)
        body.append("\r\n--\(boundary)--\r\n".data(using: .utf8)!)
        req.httpBody = body
        return try await send(req)
    }

    /// The public item document, re-read while the server retries a failed Grok call.
    static func get(id: String) async throws -> ScannedItem {
        try await send(request("items/\(id)", "GET"))
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
        let (data, resp) = try await URLSession.shared.data(for: req)
        guard (resp as? HTTPURLResponse)?.statusCode == 200 else {
            let message = (try? JSONDecoder().decode(ServerError.self, from: data))?.detail
                ?? String(data: data, encoding: .utf8) ?? "server error"
            throw URLError(.badServerResponse, userInfo: [NSLocalizedDescriptionKey: message])
        }
        return data
    }
}
