import Foundation
#if canImport(CoreNFC)
import CoreNFC
#endif

/// Liest die UID einer RFID-Karte per iPhone-NFC (iOS 13+, physisches
/// Geraet — Simulator unterstuetzt keine NFC-Session).
///
/// Was iPhone KANN:
/// - UID lesen von 13.56-MHz-HF-Karten:
///   * ISO 14443 Type A/B (MIFARE Classic/Ultralight/DESFire, die
///     meisten NFC-Dienstausweise)
///   * ISO 15693 (HID iClass SE teilweise, LEGIC Advant teilweise)
///   * FeliCa (Japan)
///
/// Was iPhone NICHT KANN:
/// - 125-kHz-LF-Karten (HID Prox, EM4100, Indala) — andere Frequenz,
///   keine Hardware.
/// - LEGIC Prime — proprietaeres Protokoll, iOS unterstuetzt es nicht.
/// - Verschluesselte Sektoren auf MIFARE Classic/DESFire ohne die
///   Firmen-Schluessel (wir lesen deshalb nur die UID, nicht den
///   Inhalt).
///
/// Rueckgabe: UID als Groszbuchstaben-Hex ohne Separator (z.B.
/// "04A3F12B889D80"). Dieses Format uebergeben wir als raw_value an
/// /desktop/cards/preview bzw. /desktop/cards — der Transform auf dem
/// Server uebernimmt alles weitere (Byte-Reverse fuer YSoft,
/// Decimal-Konvertierung, Base64-Wrap usw.).
@MainActor
final class NFCCardScanner: NSObject {

    enum NFCError: Error, LocalizedError {
        case notAvailable
        case cancelled
        case noUID
        case session(String)

        var errorDescription: String? {
            switch self {
            case .notAvailable:    return String(localized: "NFC ist auf diesem Geraet nicht verfuegbar.")
            case .cancelled:       return String(localized: "NFC-Scan wurde abgebrochen.")
            case .noUID:           return String(localized: "Karte erkannt, aber keine UID gelesen.")
            case .session(let m):  return m
            }
        }
    }

    /// Startet eine NFC-Session und liefert die UID der ersten getappten
    /// Karte. Blockiert bis zum Erfolg, Abbruch oder Fehler.
    static func scan(message: String = String(localized: "Karte an die Oberseite des iPhones halten.")) async throws -> String {
        #if canImport(CoreNFC) && !targetEnvironment(simulator)
        guard NFCTagReaderSession.readingAvailable else {
            throw NFCError.notAvailable
        }
        let handler = NFCSessionHandler(alertMessage: message)
        return try await handler.run()
        #else
        throw NFCError.notAvailable
        #endif
    }
}


#if canImport(CoreNFC) && !targetEnvironment(simulator)

/// Interner Delegate-Wrapper. NFCTagReaderSessionDelegate ist an die alte
/// NSObject-Callback-Welt gebunden — wir bruecken das zu async/await via
/// CheckedContinuation. Die Session wird stark gehalten, solange der
/// Handler lebt.
private final class NFCSessionHandler: NSObject, NFCTagReaderSessionDelegate {
    private let alertMessage: String
    private var session: NFCTagReaderSession?
    private var continuation: CheckedContinuation<String, Error>?

    init(alertMessage: String) {
        self.alertMessage = alertMessage
    }

    @MainActor
    func run() async throws -> String {
        try await withCheckedThrowingContinuation { cont in
            self.continuation = cont
            // Polling-Modi: alle drei HF-Familien — die Session entscheidet
            // anhand des Tags welcher Typ zurueckkommt.
            // Polling-Modi: ISO 14443 (MIFARE, DESFire, die meisten
            // europaeischen Firmenausweise) + ISO 15693 (HID iClass,
            // LEGIC Advant teilweise). FeliCa (.iso18092) ist bewusst
            // NICHT dabei — das braucht zusaetzlich den Info.plist-Key
            // "com.apple.developer.nfc.readersession.felica.systemcodes"
            // mit vordefinierten SystemCodes, sonst wirft CoreNFC
            // "Missing required entitlement" (NFCError Code 2 =
            // SecurityViolation). Und FeliCa ist sowieso nur in Japan
            // relevant.
            let session = NFCTagReaderSession(
                pollingOption: [.iso14443, .iso15693],
                delegate: self,
                queue: DispatchQueue.main
            )
            guard let session = session else {
                // Initializer liefert nil, wenn Entitlements/Capabilities
                // nicht matchen — UI wuerde sonst ewig auf "Scanne…" stehen.
                self.continuation = nil
                cont.resume(throwing: NFCCardScanner.NFCError.session(
                    String(localized: "NFCTagReaderSession konnte nicht erstellt werden (Entitlement-Mismatch).")
                ))
                return
            }
            session.alertMessage = self.alertMessage
            self.session = session
            session.begin()
        }
    }

    // MARK: - NFCTagReaderSessionDelegate

    func tagReaderSessionDidBecomeActive(_ session: NFCTagReaderSession) {
        // Keine Aktion noetig — iOS zeigt den System-Sheet selbst.
    }

    func tagReaderSession(_ session: NFCTagReaderSession, didInvalidateWithError error: Error) {
        // Wird aufgerufen bei Abbruch (User-Cancel oder Timeout). Wenn
        // wir schon resumed haben (erfolgreich UID gelesen), ist die
        // continuation bereits nil.
        guard let cont = self.continuation else {
            // Continuation schon verbraucht — trotzdem session-Ref freigeben.
            self.session = nil
            return
        }
        self.continuation = nil
        self.session = nil
        let ns = error as NSError
        // NFC_READER_SESSION_INVALIDATION_ERROR_USER_CANCELED = 200
        // NFC_READER_SESSION_INVALIDATION_ERROR_SESSION_TIMEOUT = 201
        if ns.code == 200 {
            cont.resume(throwing: NFCCardScanner.NFCError.cancelled)
        } else {
            // Fehler-Code mitliefern, damit bei "Missing required
            // entitlement" o.ae. sofort klar ist, welche NFCReaderError
            // es war (1 = UnsupportedFeature, 2 = SecurityViolation,
            // 201 = Timeout, 203 = SystemBusy, ...).
            let detail = "\(ns.localizedDescription) [Code \(ns.code), Domain \(ns.domain)]"
            cont.resume(throwing: NFCCardScanner.NFCError.session(detail))
        }
    }

    func tagReaderSession(_ session: NFCTagReaderSession, didDetect tags: [NFCTag]) {
        // Diagnose: was wurde ueberhaupt gefunden?
        let typeDesc = tags.map { describe($0) }.joined(separator: ", ")
        #if DEBUG
        NSLog("[NFCScanner] Tags erkannt: \(tags.count) — \(typeDesc)")
        #endif

        guard let first = tags.first else {
            session.invalidate(errorMessage: String(localized: "Kein Tag erkannt."))
            return
        }

        let typeName = describe(first)

        // Wichtig: KEIN session.connect() — das wuerde bei ISO7816-Smartcards
        // (Bankkarten, Personalausweis, moderne Firmenkarten) das zusaetzliche
        // Entitlement "...iso7816.select-identifiers" verlangen, das wir nicht
        // haben. Die .identifier-Property ist bereits im Polling-Moment
        // gesetzt — Connect ist nur noetig, wenn man APDUs sprechen will.
        let uidData = extractUID(from: first)
        guard let uid = uidData, !uid.isEmpty else {
            // Gefunden, aber iOS liefert keine UID — dem User den Tag-Typ
            // mitteilen, damit wir im Support-Fall wissen was los war.
            session.invalidate(errorMessage: String(format: String(localized: "Tag-Typ %@ gefunden, aber keine UID lesbar."), typeName))
            return
        }
        let hex = uid.map { String(format: "%02X", $0) }.joined()
        #if DEBUG
        // UID nur in Debug-Builds loggen — UIDs sind Firmen-Ausweis-IDs
        // (Tueroeffner!) und gehoeren nicht ins Unified Log eines
        // Release-Builds (sysdiagnose-Bundles, Console.app).
        NSLog("[NFCScanner] UID: \(hex) (\(typeName))")
        #else
        NSLog("[NFCScanner] Karte gelesen — Typ: \(typeName), Laenge: \(uid.count) Bytes")
        #endif
        session.alertMessage = String(localized: "Karte erfolgreich gelesen.")
        // Continuation ZUERST entnehmen und nil-setzen, BEVOR
        // invalidate() aufgerufen wird. invalidate() triggert
        // didInvalidateWithError auf demselben Main-Queue-Run-Loop —
        // wenn continuation dort noch nicht nil ist, wuerde die
        // Continuation doppelt resumed (einmal mit Fehler, einmal hier
        // mit dem UID-Wert). Durch das fruehzeitige Nil-Setzen
        // ignoriert didInvalidateWithError den Callback korrekt.
        let cont = self.continuation
        self.continuation = nil
        self.session = nil
        session.invalidate()
        cont?.resume(returning: hex)
    }

    /// Mensch-lesbarer Tag-Typ, reicht fuer Diagnose und Error-Messages.
    private func describe(_ tag: NFCTag) -> String {
        switch tag {
        case .miFare(let t):
            let fam: String
            switch t.mifareFamily {
            case .ultralight: fam = "MIFARE Ultralight"
            case .plus:       fam = "MIFARE Plus"
            case .desfire:    fam = "MIFARE DESFire"
            default:          fam = "MIFARE"
            }
            return fam
        case .iso15693:    return "ISO 15693"
        case .iso7816:     return "ISO 7816 (Smartcard)"
        case .feliCa:      return "FeliCa"
        @unknown default:  return "Unbekannt"
        }
    }

    /// Tag-Typ-Dispatch zur UID-Extraktion. Jede Tag-Variante hat ihr
    /// eigenes Identifier-Property — CoreNFC unifiziert das leider nicht.
    private func extractUID(from tag: NFCTag) -> Data? {
        switch tag {
        case .miFare(let t):       return t.identifier
        case .iso15693(let t):     return t.identifier
        case .iso7816(let t):      return t.identifier
        case .feliCa:              return nil   // wir pollen FeliCa nicht
        @unknown default:          return nil
        }
    }
}

#endif
