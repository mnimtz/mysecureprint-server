import SwiftUI
import UIKit

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
        // Konsistente Navy-Navigationsleiste über alle Tabs hinweg — ohne
        // globale UIAppearance-Override kann SwiftUI's toolbarBackground
        // auf bestimmten Tabs (z.B. List ohne Scroll-Inhalt) fehlerhaft
        // transparent bleiben bis der User scrollt.
        let navyColor = UIColor(red: 0/255, green: 40/255, blue: 84/255, alpha: 1)
        let appearance = UINavigationBarAppearance()
        appearance.configureWithOpaqueBackground()
        appearance.backgroundColor = navyColor
        appearance.titleTextAttributes = [.foregroundColor: UIColor.white]
        appearance.largeTitleTextAttributes = [.foregroundColor: UIColor.white]
        UINavigationBar.appearance().standardAppearance   = appearance
        UINavigationBar.appearance().scrollEdgeAppearance = appearance
        UINavigationBar.appearance().compactAppearance    = appearance
        UINavigationBar.appearance().tintColor = UIColor(red: 0/255, green: 160/255, blue: 251/255, alpha: 1)
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
