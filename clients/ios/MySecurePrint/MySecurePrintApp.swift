import SwiftUI

@main
struct MobilePrintApp: App {

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
