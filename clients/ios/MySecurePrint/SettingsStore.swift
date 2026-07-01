import Foundation
import Combine
import SwiftUI
#if canImport(UIKit)
import UIKit
#endif

/// Gemeinsamer Settings-Store für Haupt-App und Share-Extension.
///
/// Alle Werte liegen in der App-Group, damit die Share-Extension
/// denselben Server und Token verwendet wie die App. Der Bearer-Token
/// bleibt fürs MVP auch in den App-Group-Defaults — gut genug für
/// TestFlight; später kann er in einen Keychain-Access-Group-Eintrag
/// wandern. iOS-Geräte sind sandboxed, also ist das Risiko überschaubar.
@MainActor
final class SettingsStore: ObservableObject {

    // MUSS mit den Entitlements von Haupt-App + Share-Extension übereinstimmen.
    static let appGroupID = "group.de.nimtz.mysecureprint"

    /// Migrations-Flag: stellt sicher, dass der Bearer-Token nur einmal
    /// aus UserDefaults in den Keychain wandert.
    private static let migrationKeychainFlag = "migrated_to_keychain_v1"

    private enum Keys {
        static let serverURL         = "serverURL"
        static let bearerToken       = "bearerToken"
        static let userEmail         = "userEmail"
        static let userFullName      = "userFullName"
        static let userRoleType      = "userRoleType"
        static let lastTargetId      = "lastTargetId"      // legacy/single (Share-Ext)
        static let selectedTargetIds = "selectedTargetIds" // JSON-Array (Multi)
        static let targetLabels      = "targetLabels"      // JSON-Dict id→label
        static let selectionExpiresAt = "selectionExpiresAt" // Auto-Reset-Timer
        static let deviceName        = "deviceName"
        static let appLanguage       = "appLanguage"
        static let delegateEnabled   = "delegateEnabled"
    }

    /// Default-Ziel, auf das der Auto-Reset-Timer zurueckfaellt.
    /// "print:self" ist die magische Printix-ID fuer das eigene
    /// SecurePrint-Konto — immer vorhanden, kein API-Lookup noetig.
    static let defaultTargetId = "print:self"

    /// Wie lange eine abweichende Zielauswahl (Delegate/...)
    /// aktiv bleibt, bevor sie auf SecurePrint zurueckgestellt wird.
    /// 10 min ist ein Kompromiss: lang genug um mehrere Dokumente
    /// zu senden, kurz genug um "vergessen"-Drucke zu vermeiden.
    static let autoResetInterval: TimeInterval = 10 * 60

    private let defaults: UserDefaults

    @Published var serverURL: String {
        didSet { defaults.set(serverURL, forKey: Keys.serverURL) }
    }

    @Published var bearerToken: String {
        didSet {
            // C-4: primaer im Keychain (Access-Group geteilt mit
            // Share-Extension); UserDefaults-Spiegel raeumen.
            // v1.0.1: nur removeObject wenn Keychain-Write OK — sonst
            // verliert der User Token bei Keychain-Fehlern (z.B. nicht-
            // provisioned Access-Group im Dev-Build) ohne Recovery.
            let ok = KeychainTokenStore.set(bearerToken)
            if ok || bearerToken.isEmpty {
                defaults.removeObject(forKey: Keys.bearerToken)
            } else {
                // Fallback: Plaintext-Spiegel im Defaults behalten, damit
                // App nach Restart noch ein Login hat. Keychain wird
                // beim naechsten Login-Versuch erneut probiert.
                defaults.set(bearerToken, forKey: Keys.bearerToken)
            }
        }
    }

    @Published var userEmail: String {
        didSet { defaults.set(userEmail, forKey: Keys.userEmail) }
    }

    @Published var userFullName: String {
        didSet { defaults.set(userFullName, forKey: Keys.userFullName) }
    }

    /// Rolle des Server-Users: "admin" | "user" | "employee" | "".
    /// Bestimmt u.a. Sichtbarkeit des "Printix Management"-Tabs.
    /// Wird beim Login (oder spaeter via /desktop/me) aktualisiert.
    @Published var userRoleType: String {
        didSet { defaults.set(userRoleType, forKey: Keys.userRoleType) }
    }

    /// v1.0.2: Server-Admin hat Delegation-Druck tenant-weit erlaubt.
    /// Wird via /desktop/me ausgeliefert. Bestimmt ob der User in
    /// Settings den lokalen Toggle „Delegation erlauben" sehen darf —
    /// wenn Admin nein sagt, blenden wir den Toggle aus und zeigen einen
    /// Hinweis. Default `true` damit alte Server-Versionen (ohne den
    /// Flag im /me-Response) sich wie vorher verhalten.
    @Published var delegationAllowedByAdmin: Bool = true

    /// True wenn der aktuelle User den Management-Tab sehen darf.
    /// Employees nicht (kein Tenant/API-Kontext), Admin + User ja.
    var hasManagementAccess: Bool {
        let r = userRoleType.lowercased()
        return r == "admin" || r == "user"
    }

    @Published var lastTargetId: String {
        didSet { defaults.set(lastTargetId, forKey: Keys.lastTargetId) }
    }

    /// Multi-Select: Liste der aktuell ausgewaehlten Ziel-Ids.
    /// Die Haupt-App nutzt diese fuer den "Gleichzeitig an mehrere
    /// Ziele"-Upload. Wir halten `lastTargetId` parallel auf das erste
    /// Element synchron, damit die Share-Extension (die nur ein Ziel
    /// kennt) weiter funktioniert — die lesen wir nicht um, damit die
    /// Extension keinen Build-Anschluss an den JSON-Parser braucht.
    @Published var selectedTargetIds: [String] {
        didSet {
            if let data = try? JSONEncoder().encode(selectedTargetIds) {
                defaults.set(data, forKey: Keys.selectedTargetIds)
            }
            // Share-Extension bleibt auf single-target → primaeres Ziel mirror'n.
            let primary = selectedTargetIds.first ?? ""
            if lastTargetId != primary {
                lastTargetId = primary
            }
        }
    }

    /// Cache von id → Anzeigename (Label) der Ziele. TargetsView
    /// befuellt das beim reload(), UploadView liest daraus, damit
    /// wir unter "Ziele" im Upload-Form "Marketing-Drucker" zeigen
    /// koennen statt nur die rohe ID (Gruende: API-Roundtrip sparen
    /// und offline-freundlich bleiben).
    @Published var targetLabels: [String: String] {
        didSet {
            if let data = try? JSONEncoder().encode(targetLabels) {
                defaults.set(data, forKey: Keys.targetLabels)
            }
        }
    }

    /// Zeitpunkt, zu dem die aktuelle (nicht-default) Zielauswahl
    /// automatisch auf `defaultTargetId` zurueckgesetzt wird. `nil`
    /// bedeutet: entweder Default aktiv oder Timer inaktiv.
    @Published var selectionExpiresAt: Date? {
        didSet {
            if let d = selectionExpiresAt {
                defaults.set(d, forKey: Keys.selectionExpiresAt)
            } else {
                defaults.removeObject(forKey: Keys.selectionExpiresAt)
            }
        }
    }

    @Published var deviceName: String {
        didSet { defaults.set(deviceName, forKey: Keys.deviceName) }
    }

    /// ISO-Sprachcode (en, de, es, fr, it, nl, no, sv). Default = "en" —
    /// die App faehrt bewusst auf Englisch hoch, statt dem System zu
    /// folgen; der User kann in Konto > Sprache umstellen.
    @Published var appLanguage: String {
        didSet { defaults.set(appLanguage, forKey: Keys.appLanguage) }
    }

    /// Schalter "Drucken fuer andere User erlauben" (Delegate-Print).
    /// Standardmaessig AUS — der User muss das aktiv freischalten, damit
    /// `print_delegate`-Ziele aus /desktop/targets ueberhaupt in der
    /// Ziele-Liste auftauchen. Schuetzt vor versehentlichem Senden an
    /// einen anderen Empfaenger.
    @Published var delegateEnabled: Bool {
        didSet { defaults.set(delegateEnabled, forKey: Keys.delegateEnabled) }
    }

    /// Einmal-Token aus einem Admin-Mobile-Invite-QR
    /// (`mysecureprint://setup?server=...&token=...`). Wird beim Deep-
    /// Link in ContentView.onOpenURL gesetzt und dann in SetupView /
    /// LoginView gegen den Redeem-Endpoint
    /// `/api/v1/mobile-invite/redeem` getauscht. Lebt nur im Speicher
    /// (kein UserDefaults-Persist) — sobald die App neu startet ist er
    /// weg, was OK ist: der Token wird beim Redeem ohnehin ungueltig.
    @Published var pendingInviteToken: String = ""

    init() {
        let appGroupDefaults = UserDefaults(suiteName: Self.appGroupID)
        self.defaults = appGroupDefaults ?? .standard

        self.serverURL    = defaults.string(forKey: Keys.serverURL)    ?? ""
        // C-4: Bearer-Token aus Keychain laden; bei erstem Start nach
        // dem Upgrade einmalig aus den alten App-Group-Defaults migrieren
        // (guard via migration-Flag, damit wir bei spaeterem Sign-out
        // den dann leeren Token nicht erneut "migrieren").
        let migrated = defaults.bool(forKey: Self.migrationKeychainFlag)
        if migrated {
            self.bearerToken = KeychainTokenStore.get()
        } else {
            let legacyToken = defaults.string(forKey: Keys.bearerToken) ?? ""
            if !legacyToken.isEmpty {
                _ = KeychainTokenStore.set(legacyToken)
            }
            defaults.removeObject(forKey: Keys.bearerToken)
            defaults.set(true, forKey: Self.migrationKeychainFlag)
            self.bearerToken = KeychainTokenStore.get()
        }
        self.userEmail    = defaults.string(forKey: Keys.userEmail)    ?? ""
        self.userFullName = defaults.string(forKey: Keys.userFullName) ?? ""
        self.userRoleType = defaults.string(forKey: Keys.userRoleType) ?? ""
        let legacyTarget = defaults.string(forKey: Keys.lastTargetId) ?? ""
        self.lastTargetId = legacyTarget
        // Multi-Target aus JSON laden; Fallback = Single aus Legacy-Key.
        if let data = defaults.data(forKey: Keys.selectedTargetIds),
           let arr  = try? JSONDecoder().decode([String].self, from: data) {
            self.selectedTargetIds = arr
        } else {
            self.selectedTargetIds = legacyTarget.isEmpty ? [] : [legacyTarget]
        }
        if let data = defaults.data(forKey: Keys.targetLabels),
           let dict = try? JSONDecoder().decode([String: String].self, from: data) {
            self.targetLabels = dict
        } else {
            self.targetLabels = [:]
        }
        self.selectionExpiresAt = defaults.object(forKey: Keys.selectionExpiresAt) as? Date
        let storedDeviceName = defaults.string(forKey: Keys.deviceName) ?? ""
        self.deviceName = storedDeviceName.isEmpty ? Self.defaultDeviceName() : storedDeviceName
        let storedLang = (defaults.string(forKey: Keys.appLanguage) ?? "").lowercased()
        // v0.6.5: Beim ersten Start hat der User noch keine Sprache
        // gewaehlt — der Picker sollte trotzdem zeigen was die App
        // tatsaechlich gerade anzeigt (OS-Sprache via Bundle.main.
        // preferredLocalizations, gefiltert auf unsere supported
        // languages). Vorher: hartes "en" hier, obwohl die App in DE
        // gerendert wurde -> Picker zeigte "English", verwirrend.
        self.appLanguage = storedLang.isEmpty
            ? L10n.effectiveLanguageCode()
            : storedLang
        // Delegate-Print: default OFF, also nur an wenn der User es
        // explizit eingeschaltet hat (UserDefaults.bool gibt false fuer
        // den nicht-gesetzten Key — genau das Verhalten was wir wollen).
        self.delegateEnabled = defaults.bool(forKey: Keys.delegateEnabled)
    }

    // MARK: - Auto-Reset

    /// Nach jeder User-Auswahl aufrufen. Setzt oder loescht den
    /// Auto-Reset-Timer je nachdem, ob die aktuelle Auswahl dem
    /// Default entspricht. Bestehender Timer wird NICHT verlaengert
    /// — sonst koennte man durch wiederholtes Tippen ewig bei einem
    /// Delegate haengen bleiben (das wollen wir genau verhindern).
    func applyAutoResetPolicy() {
        let isDefault = selectedTargetIds == [Self.defaultTargetId]
                     || selectedTargetIds.isEmpty
        if isDefault {
            selectionExpiresAt = nil
        } else if selectionExpiresAt == nil {
            selectionExpiresAt = Date().addingTimeInterval(Self.autoResetInterval)
        }
    }

    /// Prueft ob der Timer abgelaufen ist und setzt ggf. zurueck.
    /// Wird von UploadView periodisch aufgerufen (TimelineView).
    /// Rueckgabewert signalisiert, ob ein Reset passiert ist —
    /// UploadView kann dann ein kurzes Toast/Feedback zeigen.
    @discardableResult
    func resetToDefaultIfExpired() -> Bool {
        guard let expiry = selectionExpiresAt, Date() >= expiry else {
            return false
        }
        selectedTargetIds = [Self.defaultTargetId]
        selectionExpiresAt = nil
        return true
    }

    // MARK: - Derived

    var serverBaseURL: URL? {
        let trimmed = serverURL.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else { return nil }
        guard let url = URL(string: trimmed) else { return nil }
        // H-2: nicht jede aus URL(string:) ableitbare URL ist auch eine
        // brauchbare Server-Basis. "foo" parsed durch, hat aber weder
        // Scheme noch Host. Wir verlangen http(s) + Host.
        guard let scheme = url.scheme?.lowercased(),
              scheme.hasPrefix("http"),
              let host = url.host, !host.isEmpty else {
            return nil
        }
        return url
    }

    var isLoggedIn: Bool {
        !bearerToken.isEmpty && serverBaseURL != nil
    }

    func clearSession() {
        bearerToken = ""
        userEmail = ""
        userFullName = ""
        userRoleType = ""
        lastTargetId = ""
        // H-3: weitere session-gebundene Felder mit aufraeumen, damit
        // ein neuer Login nicht in stale-State landet. `delegateEnabled`
        // bewusst NICHT zuruecksetzen — das ist eine Geraete-Einstellung
        // und ueberlebt einen Re-Login.
        selectedTargetIds = []
        targetLabels = [:]
        selectionExpiresAt = nil
        pendingInviteToken = ""
    }

    private static func defaultDeviceName() -> String {
        #if canImport(UIKit)
        return UIDevice.current.name
        #else
        return "iOS Device"
        #endif
    }
}
