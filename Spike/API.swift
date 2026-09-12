import Foundation
import PackingPlan
import UIKit

/// The FastAPI server in server/. Set to this Mac's LAN IP; phone and Mac must share a Wi-Fi network.
enum API {
    static let base = URL(string: "http://172.26.48.172:8000")!

    static func upload(_ item: ScannedItem, image: UIImage?) async throws -> ScannedItem {
        let boundary = "suitcase-\(UUID().uuidString)"
        var req = URLRequest(url: base.appending(path: "items"))
        req.httpMethod = "POST"
        req.setValue("multipart/form-data; boundary=\(boundary)", forHTTPHeaderField: "Content-Type")
        var body = Data()
        body.append("--\(boundary)\r\nContent-Disposition: form-data; name=\"item\"\r\n\r\n".data(using: .utf8)!)
        body.append(try JSONEncoder().encode(item))
        if let jpeg = image?.jpegData(compressionQuality: 0.7) {
            body.append("\r\n--\(boundary)\r\nContent-Disposition: form-data; name=\"image\"; filename=\"o.jpg\"\r\nContent-Type: image/jpeg\r\n\r\n".data(using: .utf8)!)
            body.append(jpeg)
        }
        body.append("\r\n--\(boundary)--\r\n".data(using: .utf8)!)
        req.httpBody = body
        return try await send(req)
    }

    struct Guess: Decodable {
        var label: String; var description: String; var rigidity: String
        var compressibility: Double; var mass: Double; var keepUpright: Bool
    }

    /// Identify the object in a photo. Nothing is stored.
    static func label(_ image: UIImage) async throws -> Guess {
        let boundary = "suitcase-\(UUID().uuidString)"
        var req = URLRequest(url: base.appending(path: "label"))
        req.httpMethod = "POST"
        req.setValue("multipart/form-data; boundary=\(boundary)", forHTTPHeaderField: "Content-Type")
        var body = Data()
        body.append("--\(boundary)\r\nContent-Disposition: form-data; name=\"image\"; filename=\"o.jpg\"\r\nContent-Type: image/jpeg\r\n\r\n".data(using: .utf8)!)
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

    static func createSuitcase(name: String, dimensions: [Float]) async throws -> Suitcase {
        var req = URLRequest(url: base.appending(path: "suitcases"))
        req.httpMethod = "POST"
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        req.httpBody = try JSONEncoder().encode(["name": AnyEncodable(name), "dimensions": AnyEncodable(dimensions)])
        return try await send(req)
    }

    /// Runs the solver server-side and returns what it actually produced. Throws with the
    /// server's message ("suitcase has no scanned items to pack") when there is nothing to pack.
    static func plan(suitcaseId: String) async throws -> PackingPlan {
        var req = URLRequest(url: base.appending(path: "suitcases/\(suitcaseId)/plan"))
        req.httpMethod = "POST"
        return try PlanLoader.plan(fromServerDocument: try await body(of: req))
    }

    static func listSuitcases() async throws -> [Suitcase] {
        try await send(URLRequest(url: base.appending(path: "suitcases")))
    }

    static func items(in suitcaseId: String) async throws -> [ScannedItem] {
        try await send(URLRequest(url: base.appending(path: "items").appending(queryItems: [.init(name: "suitcaseId", value: suitcaseId)])))
    }

    static func delete(_ path: String) async throws {
        var req = URLRequest(url: base.appending(path: path))
        req.httpMethod = "DELETE"
        let _: [String: String] = try await send(req)
    }

    private static func send<T: Decodable>(_ req: URLRequest) async throws -> T {
        try JSONDecoder().decode(T.self, from: try await body(of: req))
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

/// FastAPI reports every error as `{"detail": ...}`.
private struct ServerError: Decodable {
    let detail: String
}

struct AnyEncodable: Encodable {
    let encode: (Encoder) throws -> Void
    init<T: Encodable>(_ v: T) { encode = { try v.encode(to: $0) } }
    func encode(to encoder: Encoder) throws { try encode(encoder) }
}
