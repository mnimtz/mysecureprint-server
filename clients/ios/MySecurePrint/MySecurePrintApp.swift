import SwiftUI

@main
struct MobilePrintApp: App {

    @UIApplicationDelegateAdaptor(AppDelegate.self) var appDelegate

    // Default = Geraete-Sprache: wir setzen KEIN AppleLanguages auf der
    // ersten Installation. iOS matcht selbst die preferredLanguages des
    // Geraets gegen unsere verfuegbaren lprojs (en, de, es, fr, it, nl,
    // nb, sv). Bei unsupported Geraetesprachen greift iOS auf die
    // sourceLanguage der xcstrings zurueck (= de, mit Key = Anzeige-Text).
    // User-Auswahl im Picker ueberschreibt das via AppleLanguages in
    // UserDefaults.
    @StateObject private var l10n = L10n()

    var body: some Scene {
        WindowGroup {
            ContentView()
                .environmentObject(l10n)
        }
    }
}

// MARK: - AppDelegate (APNs)

final class AppDelegate: NSObject, UIApplicationDelegate {

    func application(
        _ application: UIApplication,
        didFinishLaunchingWithOptions launchOptions: [UIApplication.LaunchOptionsKey: Any]? = nil
    ) -> Bool {
        return true
    }

    func application(
        _ application: UIApplication,
        didRegisterForRemoteNotificationsWithDeviceToken deviceToken: Data
    ) {
        Task { @MainActor in
            // SettingsStore-Instanz aus der laufenden App holen ist hier
            // nicht direkt moeglich — wir lesen den Token und cachen ihn.
            // ContentView ruft uploadCachedTokenIfNeeded() nach Login auf.
            PushNotificationManager.shared.handleDeviceToken(
                deviceToken,
                settings: _sharedSettings()
            )
        }
    }

    func application(
        _ application: UIApplication,
        didFailToRegisterForRemoteNotificationsWithError error: Error
    ) {
        PushNotificationManager.shared.handleRegistrationError(error)
    }

    /// Erstellt einen kurzlebigen SettingsStore um Token + Server-URL zu lesen.
    @MainActor
    private func _sharedSettings() -> SettingsStore {
        SettingsStore()
    }
}
