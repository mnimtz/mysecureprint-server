import Foundation
#if canImport(UIKit)
import UIKit
#endif

// Einfache JSON-Konfig nach dem Muster des Windows-Clients.
// Liegt unter ~/Library/Application Support/PrintixSend/config.json
// (Sandbox-sicher, bleibt bei App-Update erhalten).

public struct AppConfig: Codable, Sendable {
    public var serverUrl: String
    public var deviceName: String
    public var lastUsername: String?

    public static var defaultDeviceName: String {
        // Plattform-Split:
        //  - macOS: Host.current().localizedName ("Marcus' MacBook Pro")
        //  - iOS:   UIDevice.current.name ("Marcus' iPhone")
        // Beides ergibt einen menschenlesbaren Geräte-Namen, den die
        // Printix-API in der Device-Liste anzeigt.
        #if os(macOS)
        return Host.current().localizedName ?? "Mac"
        #elseif canImport(UIKit)
        return UIDevice.current.name
        #else
        return "Device"
        #endif
    }

    public init(serverUrl: String = "",
                deviceName: String = AppConfig.defaultDeviceName,
                lastUsername: String? = nil) {
        self.serverUrl = serverUrl
        self.deviceName = deviceName
        self.lastUsername = lastUsername
    }
}

public struct ConfigStore {
    public static let shared = ConfigStore()

    public var configUrl: URL {
        let support = FileManager.default.urls(for: .applicationSupportDirectory,
                                               in: .userDomainMask).first!
        let dir = support.appendingPathComponent("PrintixSend", isDirectory: true)
        try? FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        return dir.appendingPathComponent("config.json")
    }

    public func load() -> AppConfig {
        guard let data = try? Data(contentsOf: configUrl),
              let cfg  = try? JSONDecoder().decode(AppConfig.self, from: data) else {
            return AppConfig()
        }
        return cfg
    }

    public func save(_ cfg: AppConfig) {
        let enc = JSONEncoder()
        enc.outputFormatting = [.prettyPrinted, .sortedKeys]
        if let data = try? enc.encode(cfg) {
            try? data.write(to: configUrl, options: .atomic)
        }
    }
}
