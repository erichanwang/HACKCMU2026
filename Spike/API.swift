import Foundation
import UIKit

/// The FastAPI server in server/. Set to this Mac's LAN IP; phone and Mac must share a Wi-Fi network.
enum API {
    static let base = URL(string: "http://172.26.120.200:8000")!

    /// Upload the item and its photo, then its voxels.
    ///
    /// Two calls on purpose. The server caps a single multipart part at 1 MB, and a scan's
    /// voxels run well past that, so they go up separately as JSON via `uploadVoxels`.
    /// Splitting also means labelling returns as soon as the small part lands, rather than
    /// waiting on megabytes of geometry.
    static func upload(_ item: ScannedItem, image: UIImage) async throws -> ScannedItem {
        var labelled = try await uploadMetadata(item, image: image)
        if let voxels = item.voxels {
            // Geometry is worth having even if this leg fails, so a failure here leaves the
            // item in place with its label and simply carries no voxels.
            labelled = (try? await uploadVoxels(id: item.id, voxels: voxels,
                                                viewCoverage: item.viewCoverage)) ?? labelled
        }
        return labelled
    }

    static func uploadVoxels(id: String, voxels: VoxelPayload, viewCoverage: Float?) async throws -> ScannedItem {
        struct Body: Encodable { let voxels: VoxelPayload; let viewCoverage: Float? }
        var req = URLRequest(url: base.appending(path: "items/\(id)/voxels"))
        req.httpMethod = "PUT"
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        req.httpBody = try JSONEncoder().encode(Body(voxels: voxels, viewCoverage: viewCoverage))
        return try await send(req)
    }

    private static func uploadMetadata(_ item: ScannedItem, image: UIImage) async throws -> ScannedItem {
        // Voxels are stripped here and sent by `uploadVoxels`; leaving them in would push
        // this form part past the server's 1 MB per-part limit.
        var small = item
        small.voxels = nil
        let boundary = "suitcase-\(UUID().uuidString)"
        var req = URLRequest(url: base.appending(path: "items"))
        req.httpMethod = "POST"
        req.setValue("multipart/form-data; boundary=\(boundary)", forHTTPHeaderField: "Content-Type")
        var body = Data()
        body.append("--\(boundary)\r\nContent-Disposition: form-data; name=\"item\"\r\n\r\n".data(using: .utf8)!)
        body.append(try JSONEncoder().encode(small))
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

    static func createSuitcase(name: String, dimensions: [Float]) async throws -> Suitcase {
        var req = URLRequest(url: base.appending(path: "suitcases"))
        req.httpMethod = "POST"
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        req.httpBody = try JSONEncoder().encode(["name": AnyEncodable(name), "dimensions": AnyEncodable(dimensions)])
        return try await send(req)
    }

    static func listSuitcases() async throws -> [Suitcase] {
        try await send(URLRequest(url: base.appending(path: "suitcases")))
    }

    private static func send<T: Decodable>(_ req: URLRequest) async throws -> T {
        let (data, resp) = try await URLSession.shared.data(for: req)
        guard (resp as? HTTPURLResponse)?.statusCode == 200 else {
            throw URLError(.badServerResponse, userInfo: [NSLocalizedDescriptionKey: String(data: data, encoding: .utf8) ?? "server error"])
        }
        return try JSONDecoder().decode(T.self, from: data)
    }
}

struct AnyEncodable: Encodable {
    let encode: (Encoder) throws -> Void
    init<T: Encodable>(_ v: T) { encode = { try v.encode(to: $0) } }
    func encode(to encoder: Encoder) throws { try encode(encoder) }
}
