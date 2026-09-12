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

/// The FastAPI server in server/. The address is a LAN IP that moves, so it lives
/// in `ServerSettings` (UserDefaults) and is editable from the app's root screen;
/// phone and Mac must still share a Wi-Fi network.
enum API {
    static var base: URL { ServerSettings.baseURL }

    static func createSuitcase(name: String, dimensions: [Float]) async throws -> String {
        var req = URLRequest(url: base.appending(path: "suitcases"))
        req.httpMethod = "POST"
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        req.httpBody = try JSONEncoder().encode(NewSuitcase(name: name, dimensions: dimensions))
        let data = try await body(of: req)
        return try JSONDecoder().decode(SuitcaseResponse.self, from: data).id
    }

    /// Runs the solver server-side and returns what it actually produced. Throws with the
    /// server's message ("suitcase has no scanned items to pack") when there is nothing to pack.
    static func plan(suitcaseId: String) async throws -> PackingPlan {
        var req = URLRequest(url: base.appending(path: "suitcases/\(suitcaseId)/plan"))
        req.httpMethod = "POST"
        let data = try await body(of: req)
        return try PlanLoader.plan(fromServerDocument: data)
    }

    static func upload(_ item: ScannedItem, image: UIImage) async throws -> ScannedItem {
        let boundary = "suitcase-\(UUID().uuidString)"
        var req = URLRequest(url: base.appending(path: "items"))
        req.httpMethod = "POST"
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

    static func update(id: String, label: String?, rigidity: String?) async throws -> ScannedItem {
        var req = URLRequest(url: base.appending(path: "items/\(id)"))
        req.httpMethod = "PATCH"
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
