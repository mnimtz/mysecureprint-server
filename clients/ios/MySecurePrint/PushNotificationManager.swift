import Foundation
import UserNotifications

/// Verwaltet APNs-Registrierung und Token-Upload zum MySecurePrint-Server.
///
/// Ablauf:
/// 1. Nach Login: `requestPermissionAndRegister()` aufrufen.
/// 2. AppDelegate empfängt Device-Token via `didRegisterForRemoteNotificationsWithDeviceToken`.
/// 3. `handleDeviceToken(_:settings:)` konvertiert und uploaded den Token.
/// 4. Bei Logout: `unregister(settings:)` löscht den Token serverseitig.
@MainActor
final class PushNotificationManager {

    static let shared = PushNotificationManager()

    private(set) var currentDeviceToken: String?

    private init() {
        currentDeviceToken = UserDefaults.standard.string(forKey: "apns_device_token_v1")
    }

    // MARK: - Permission + Registration

    func requestPermissionAndRegister() {
        UNUserNotificationCenter.current().requestAuthorization(
            options: [.alert, .sound, .badge]
        ) { granted, _ in
            guard granted else { return }
            Task { @MainActor in
                UIApplication.shared.registerForRemoteNotifications()
            }
        }
    }

    // MARK: - Token vom AppDelegate

    func handleDeviceToken(_ tokenData: Data, settings: SettingsStore) {
        let token = tokenData.map { String(format: "%02.2hhx", $0) }.joined()
        guard !token.isEmpty else { return }
        let changed = token != currentDeviceToken
        currentDeviceToken = token
        UserDefaults.standard.set(token, forKey: "apns_device_token_v1")
        guard changed, settings.isLoggedIn else { return }
        Task { await self._upload(token, serverURL: settings.serverURL, bearer: settings.bearerToken) }
    }

    func handleRegistrationError(_ error: Error) {
        print("[Push] APNs registration failed: \(error.localizedDescription)")
    }

    // MARK: - Upload + Unregister

    private func _upload(_ token: String, serverURL: String, bearer: String) async {
        guard let base = URL(string: serverURL), !bearer.isEmpty else { return }
        let url = base.appendingPathComponent("desktop/push/register")
        var req = URLRequest(url: url)
        req.httpMethod = "POST"
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        req.setValue("Bearer \(bearer)", forHTTPHeaderField: "Authorization")
        req.timeoutInterval = 15
        #if DEBUG
        let env = "sandbox"
        #else
        let env = "production"
        #endif
        req.httpBody = try? JSONSerialization.data(withJSONObject: [
            "device_token": token,
            "environment": env,
        ])
        do {
            let (_, resp) = try await URLSession.shared.data(for: req)
            let status = (resp as? HTTPURLResponse)?.statusCode ?? 0
            print("[Push] Token uploaded — HTTP \(status)")
        } catch {
            print("[Push] Token upload failed: \(error.localizedDescription)")
        }
    }

    func unregister(serverURL: String, bearerToken: String) async {
        guard let token = currentDeviceToken, !token.isEmpty,
              !bearerToken.isEmpty,
              let base = URL(string: serverURL) else { return }
        let url = base.appendingPathComponent("desktop/push/unregister")
        var req = URLRequest(url: url)
        req.httpMethod = "DELETE"
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        req.setValue("Bearer \(bearerToken)", forHTTPHeaderField: "Authorization")
        req.timeoutInterval = 10
        req.httpBody = try? JSONSerialization.data(withJSONObject: ["device_token": token])
        _ = try? await URLSession.shared.data(for: req)
        currentDeviceToken = nil
        UserDefaults.standard.removeObject(forKey: "apns_device_token_v1")
    }

    /// Uploade den gecachten Token nach Login (Token wurde ggf. vor dem Login registriert).
    func uploadCachedTokenIfNeeded(settings: SettingsStore) {
        guard let token = currentDeviceToken, settings.isLoggedIn else { return }
        Task { await self._upload(token, serverURL: settings.serverURL, bearer: settings.bearerToken) }
    }
}
