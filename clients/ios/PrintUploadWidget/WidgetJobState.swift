import Foundation

/// Persistierter Zustand für das Lock Screen Widget.
/// Wird von AppCache ins App Group geschrieben, vom Widget gelesen.
struct WidgetJobState: Codable {
    var pendingCount: Int          // Jobs in nicht-terminalem Zustand
    var lastFilename: String?
    var lastStatus: String?        // "queued", "forwarding", "forwarded", "error", "send_failed"
    var lastQueue: String?
    var updatedAt: Date

    static let userDefaultsKey = "widgetJobState"

    static func load(appGroupID: String) -> WidgetJobState {
        guard let defaults = UserDefaults(suiteName: appGroupID),
              let data = defaults.data(forKey: userDefaultsKey),
              let state = try? JSONDecoder().decode(WidgetJobState.self, from: data) else {
            return WidgetJobState(pendingCount: 0, updatedAt: Date())
        }
        return state
    }

    func save(appGroupID: String) {
        guard let defaults = UserDefaults(suiteName: appGroupID),
              let data = try? JSONEncoder().encode(self) else { return }
        defaults.set(data, forKey: Self.userDefaultsKey)
    }

    var statusIcon: String {
        switch lastStatus?.lowercased() {
        case "forwarded", "ok", "success", "completed", "printed": return "checkmark.circle.fill"
        case "error", "failed", "send_failed":                      return "exclamationmark.circle.fill"
        case "queued", "forwarding":                                return "clock.fill"
        default:                                                    return "printer.fill"
        }
    }

    var isError: Bool {
        ["error", "failed", "send_failed"].contains(lastStatus?.lowercased() ?? "")
    }

    var isPending: Bool {
        ["queued", "forwarding"].contains(lastStatus?.lowercased() ?? "")
    }
}
