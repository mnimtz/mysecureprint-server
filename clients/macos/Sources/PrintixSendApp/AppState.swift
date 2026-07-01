import Foundation
import AppKit
import PrintixSendCore

// Zentraler Zustand der Menu-Bar-App. Hält Config, Token, User und
// Target-Liste. Spiegelt die Logik von AppState.cs/MainViewModel.cs
// des Windows-Clients — etwas dünner, weil wir keine Home-UI haben.
//
// Thread-Safety: alle Mutationen laufen über den Main-Actor. Die
// Menu-Neuzeichnung hängt an NotificationCenter-Events, damit
// AppDelegate keinen direkten Observer braucht.

@MainActor
public final class AppState {
    public static let shared = AppState()

    // MARK: - Published-ähnlicher State (per Notification)

    public private(set) var config: AppConfig = ConfigStore.shared.load()
    public private(set) var user: UserInfo?
    public private(set) var targets: [Target] = []

    // MARK: - Dependencies

    private let keychain = KeychainStore()
    private let log = AppLogger.shared

    private var api: ApiClient? {
        guard !config.serverUrl.isEmpty else { return nil }
        let token = keychain.get()
        return try? ApiClient(baseUrl: config.serverUrl, token: token)
    }

    // MARK: - Bootstrap

    /// Beim App-Start: Config laden, /me pingen, Targets holen, Quick
    /// Actions synchronisieren. Wenn Config oder Token fehlen, nur
    /// Menü aktualisieren — der User soll sich dann via "Anmelden…"
    /// einloggen.
    public func bootstrap() async {
        config = ConfigStore.shared.load()

        if config.serverUrl.isEmpty {
            log.info("Bootstrap: keine Config — warte auf Einrichtung")
            notifyLoginChange()
            return
        }
        guard keychain.get() != nil else {
            log.info("Bootstrap: kein Token — warte auf Login")
            notifyLoginChange()
            return
        }

        await refreshMe()
        await refreshTargetsAndSync()
    }

    // MARK: - Login

    public func loginPassword(username: String, password: String) async throws {
        guard !config.serverUrl.isEmpty else {
            throw NSError(domain: "PrintixSend", code: 1,
                          userInfo: [NSLocalizedDescriptionKey: "Server-URL fehlt — bitte in Konfiguration hinterlegen."])
        }
        let api = try ApiClient(baseUrl: config.serverUrl)
        let result = try await api.login(username: username,
                                         password: password,
                                         deviceName: config.deviceName)
        guard let token = result.token, !token.isEmpty else {
            throw NSError(domain: "PrintixSend", code: 2,
                          userInfo: [NSLocalizedDescriptionKey: "Login ohne Token — Server-Antwort unerwartet."])
        }
        try keychain.set(token)
        user = result.user

        var cfg = config
        cfg.lastUsername = username
        ConfigStore.shared.save(cfg)
        config = cfg

        notifyLoginChange()
        await refreshTargetsAndSync()
    }

    // MARK: - Logout

    public func logout() async {
        if let api {
            await api.logout()
        }
        keychain.clear()
        user = nil
        targets = []
        notifyLoginChange()
        notifyTargetsChange()
    }

    // MARK: - Targets

    public func refreshMe() async {
        guard let api else { return }
        do {
            user = try await api.me()
            notifyLoginChange()
        } catch {
            log.warn("me() fehlgeschlagen: \(error.localizedDescription)")
        }
    }

    public func refreshTargetsAndSync() async {
        guard let api else { return }
        do {
            let list = try await api.targets()
            targets = list
            notifyTargetsChange()

            let sync = QuickActionsSync(cliPath: resolveCliPath())
            try sync.sync(targets: list)
            log.info("Quick Actions aktualisiert (\(list.count) Ziele)")
        } catch {
            log.error("Target-Sync fehlgeschlagen: \(error.localizedDescription)")
            Notify.show(title: "Printix Send",
                        body: "Ziele konnten nicht aktualisiert werden: \(error.localizedDescription)",
                        ok: false)
        }
    }

    // MARK: - Config

    public func updateConfig(serverUrl: String, deviceName: String) {
        var cfg = config
        cfg.serverUrl  = serverUrl.trimmingCharacters(in: .whitespaces)
        cfg.deviceName = deviceName.trimmingCharacters(in: .whitespaces).isEmpty
                         ? AppConfig.defaultDeviceName
                         : deviceName
        ConfigStore.shared.save(cfg)
        config = cfg
        log.info("Config gespeichert: server=\(cfg.serverUrl) device=\(cfg.deviceName)")
    }

    // MARK: - Helpers

    /// Pfad zum CLI-Helper. In der installierten .app liegt er unter
    /// Contents/MacOS/printix-send-cli. Beim `swift run` Development
    /// greifen wir auf den Build-Output zurück.
    private func resolveCliPath() -> String {
        let bundleExec = Bundle.main.executableURL
        if let exec = bundleExec?.deletingLastPathComponent()
                                 .appendingPathComponent("printix-send-cli"),
           FileManager.default.isExecutableFile(atPath: exec.path) {
            return exec.path
        }
        // Fallback: /usr/local/bin (wenn per pkg installiert)
        let sys = "/usr/local/bin/printix-send-cli"
        if FileManager.default.isExecutableFile(atPath: sys) { return sys }
        // Dev-Fallback
        return bundleExec?.deletingLastPathComponent()
                          .appendingPathComponent("printix-send-cli").path
                          ?? sys
    }

    private func notifyLoginChange() {
        NotificationCenter.default.post(name: .loginStateDidChange, object: nil)
    }
    private func notifyTargetsChange() {
        NotificationCenter.default.post(name: .targetsDidChange, object: nil)
    }
}
