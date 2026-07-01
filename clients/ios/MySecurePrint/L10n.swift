import Foundation
import Combine
import SwiftUI

/// In-App-Sprachumschaltung fuer MySecurePrint.
///
/// Warum `AppleLanguages` und nicht Bundle-Swizzle?
/// ------------------------------------------------
/// Mit xcstrings + Xcode 15+ aufloest SwiftUI `Text("key")` nicht mehr
/// ueber `Bundle.main.localizedString(...)`, sondern ueber den intern
/// kompilierten StringsData-Catalog und `Bundle.preferredLocalizations`.
/// Class-Swizzling von Bundle.main greift dort NICHT. Belegt durch
/// Debug-Bar-Test: swizzle liefert "Sign out", SwiftUI-Text bleibt aber
/// in der Source-Sprache.
///
/// Der zuverlaessige Weg ist Apples offizieller: `AppleLanguages` in
/// `UserDefaults.standard` auf die gewuenschte Sprache zu setzen. Dieser
/// Wert steuert `preferredLocalizations` beim App-Start und wird von
/// allen Localization-Pfaden (SwiftUI, UIKit, String(localized:))
/// konsistent respektiert. Der Nachteil: die Umstellung greift erst
/// beim naechsten App-Start. Dafuer bleibt sie dann zu 100% konsistent.

// MARK: - Persistenz

private enum L10nKeys {
    /// iOS-Standardkey in UserDefaults.standard — wird beim App-Start
    /// ausgewertet, deshalb ist ein Restart noetig damit Aenderungen
    /// durchschlagen.
    static let appleLanguages = "AppleLanguages"
}

// MARK: - L10n ObservableObject

/// Zentraler Runtime-Zugriffspunkt auf die aktive App-Sprache.
/// Haelt nur den AKTUELL aktiven Code (= Bundle.preferredLocalizations[0])
/// plus die am letzten Launch gesetzte Pending-Sprache. Wird beim Picker
/// geschrieben, greift aber erst beim naechsten Start.
final class L10n: ObservableObject {

    /// Alle unterstuetzten Sprachen — gleich dem Server (src/web/i18n.py).
    /// Display-Namen in der jeweiligen Muttersprache, damit der Picker
    /// von Nicht-Muttersprachlern trotzdem navigierbar ist. Die Codes
    /// sind BCP-47 und matchen exakt die kompilierten .lproj-Ordner.
    static let supportedLanguages: [(code: String, display: String)] = [
        ("en", "English"),
        ("de", "Deutsch"),
        ("es", "Español"),
        ("fr", "Français"),
        ("it", "Italiano"),
        ("nl", "Nederlands"),
        ("nb", "Norsk"),
        ("sv", "Svenska"),
    ]

    /// Aktuell gerenderte Sprache — wird aus den preferredLocalizations
    /// abgeleitet und bleibt ueber die Session konstant.
    @Published private(set) var currentLanguage: String

    /// Pending-Auswahl des Users im Picker. Wenn != currentLanguage,
    /// zeigt die UI einen "App neu starten"-Hinweis.
    @Published var pendingLanguage: String

    init() {
        let current = L10n.effectiveLanguageCode()
        self.currentLanguage = current
        self.pendingLanguage = current
    }

    /// User hat im Picker eine neue Sprache gewaehlt. Wir persistieren
    /// sie in `AppleLanguages` (damit iOS sie beim naechsten Start als
    /// `preferredLocalizations` liest) — aber die aktuelle Session
    /// laeuft in der alten Sprache weiter, bis der User die App neu
    /// startet.
    func apply(_ code: String) {
        let normalized = L10n.normalize(code)
        pendingLanguage = normalized
        UserDefaults.standard.set([normalized], forKey: L10nKeys.appleLanguages)
    }

    /// True wenn der User eine Sprache gewaehlt hat, die sich erst nach
    /// Restart auswirkt. Fuer den UI-Hinweis in AccountView.
    var restartRequired: Bool {
        pendingLanguage != currentLanguage
    }

    // MARK: - Helpers

    private static func normalize(_ code: String) -> String {
        let c = code.lowercased()
        if Self.supportedLanguages.contains(where: { $0.code == c }) { return c }
        // Norwegisch-Spezial: "no" -> "nb" (Bokmål), weil Xcode die
        // xcstrings unter "nb" kompiliert, Apple aber gerne "no"
        // persistiert.
        if c == "no" { return "nb" }
        return "en"
    }

    /// Welche Sprache zeigt die App GERADE an? Wir nehmen den ersten
    /// Eintrag aus `Bundle.main.preferredLocalizations`, das ist die
    /// effektive Auswahl die iOS beim Launch getroffen hat.
    static func effectiveLanguageCode() -> String {
        guard let first = Bundle.main.preferredLocalizations.first?.lowercased() else {
            return "en"
        }
        // preferredLocalizations kann "en-DE" o.ä. liefern — Region
        // abschneiden und normalisieren.
        let base = String(first.prefix(while: { $0 != "-" }))
        return normalize(base)
    }
}
