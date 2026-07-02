import UIKit
import UniformTypeIdentifiers
import Security
import os.log
import PrintixSendCore

/// iOS Share-Extension fuer MySecurePrint.
///
/// Empfaengt PDF- oder Bild-Anhaenge aus dem System-Share-Sheet,
/// rendert Bilder bei Bedarf zu einer einseitigen PDF und schickt
/// das Ergebnis an `/desktop/send` der konfigurierten App-URL via
/// `PrintixSendCore.ApiClient`. Token + Server-URL kommen aus der
/// geteilten App-Group bzw. dem Keychain-Access-Group der Haupt-App.
///
/// Bewusst UIKit/UIViewController (statt SLComposeServiceViewController)
/// — wir wollen kein Eingabe-UI, sondern silent-send mit kleinem
/// Status. Das war auch die Variante, die zuvor produktiv lief.
final class ShareViewController: UIViewController {

    // MUSS exakt zu den Entitlements von Haupt-App + Extension passen.
    private static let appGroupID = "group.de.nimtz.mysecureprint"

    // Keychain-Konfiguration aus der Haupt-App (KeychainTokenStore).
    // Wir duplizieren das hier, weil die Extension nicht den
    // Main-App-Code linkt — Keychain-Access-Group sorgt fuer den Share.
    private static let keychainService     = "de.nimtz.mysecureprint"
    private static let keychainAccount     = "bearerToken"
    private static let keychainAccessGroup = "group.de.nimtz.mysecureprint"

    nonisolated private static let log = OSLog(subsystem: "de.nimtz.mysecureprint.share",
                                               category: "ShareExtension")

    private enum DefaultsKey {
        static let serverURL          = "serverURL"
        // Legacy: UserDefaults-Spiegel des Tokens (v <= 0.4.x).
        // Aktuell liegt der Bearer im Keychain — wir lesen aber beide.
        static let bearerTokenLegacy  = "bearerToken"
        // Bevorzugt: JSON-Array der Multi-Select-Ziele (Haupt-App).
        static let selectedTargetIds  = "selectedTargetIds"
        // Legacy/Single-Target (vor Multi-Select).
        static let lastTargetId       = "lastTargetId"
        // Wird bei Fehlern fuer die Haupt-App geschrieben.
        static let lastShareError     = "lastShareError"
    }

    /// Sentinel fuer "Mein SecurePrint-Konto" — funktioniert immer,
    /// auch wenn noch kein Ziel ausgewaehlt wurde.
    private static let defaultTargetId = "print:self"

    // Einfaches Status-Label, damit die Extension nicht voellig
    // schwarz/leer wirkt waehrend der ~1s Upload.
    private let statusLabel: UILabel = {
        let l = UILabel()
        l.translatesAutoresizingMaskIntoConstraints = false
        l.textAlignment = .center
        l.numberOfLines = 0
        l.font = .preferredFont(forTextStyle: .body)
        l.textColor = .label
        l.text = String(localized: "Sende an MySecurePrint…")
        return l
    }()

    override func viewDidLoad() {
        super.viewDidLoad()
        view.backgroundColor = .systemBackground
        view.addSubview(statusLabel)
        NSLayoutConstraint.activate([
            statusLabel.centerXAnchor.constraint(equalTo: view.centerXAnchor),
            statusLabel.centerYAnchor.constraint(equalTo: view.centerYAnchor),
            statusLabel.leadingAnchor.constraint(greaterThanOrEqualTo: view.leadingAnchor, constant: 24),
            statusLabel.trailingAnchor.constraint(lessThanOrEqualTo: view.trailingAnchor, constant: -24),
        ])
    }

    /// v1.0.1: Semaphore die den performExpiringActivity-Block am Leben
    /// haelt bis der Upload wirklich fertig ist. Vorher: der Block kehrte
    /// nach 0.05s zurueck → iOS suspendierte die Extension → Task.detached
    /// wurde mid-flight gekilled → Upload verloren, User sieht Success.
    private let uploadDoneSemaphore = DispatchSemaphore(value: 0)
    private var uploadFinished = false

    override func viewDidAppear(_ animated: Bool) {
        super.viewDidAppear(animated)
        // App-Extensions haben kein UIApplication.beginBackgroundTask;
        // performExpiringActivity ist der einzige Weg, iOS zu bitten die
        // Extension nicht zu suspendieren. Der Block MUSS blocken solange
        // wir Zeit brauchen — sonst signalisieren wir dem System dass wir
        // fertig sind und iOS killt uns.
        ProcessInfo.processInfo.performExpiringActivity(
            withReason: "MySecurePrintShareUpload"
        ) { [weak self] expiring in
            guard let self else { return }
            if expiring {
                // iOS zwingt uns raus. Wecke den Warteblock auf damit die
                // Extension sauber zu Ende geht (Success/Fail wird
                // eventuell noch nicht gemeldet, aber wir wurden gewarnt).
                self.uploadDoneSemaphore.signal()
                return
            }
            // Block bleibt hier haengen bis der Upload signalisiert.
            // Hard-Cap 55s (iOS gibt ~60s im Suspend-Fenster) damit wir
            // notfalls kontrolliert freigeben statt hart gekilled zu
            // werden.
            _ = self.uploadDoneSemaphore.wait(timeout: .now() + 55)
        }
        handleSharedItem()
    }

    /// Muss aus allen Terminal-Pfaden aufgerufen werden (Success/Fail)
    /// damit iOS die Extension freigibt. Idempotent.
    private func markUploadFinished() {
        if !uploadFinished {
            uploadFinished = true
            uploadDoneSemaphore.signal()
        }
    }

    // MARK: - Attachment-Handling

    private func handleSharedItem() {
        guard
            let extensionItem = extensionContext?.inputItems.first as? NSExtensionItem,
            let providers = extensionItem.attachments,
            !providers.isEmpty
        else {
            os_log("Keine Anhaenge im Share-Item", log: Self.log, type: .error)
            finish(error: String(localized: "Keine Datei im Share-Sheet gefunden."))
            return
        }

        if let pdfProvider = providers.first(where: { $0.hasItemConformingToTypeIdentifier(UTType.pdf.identifier) }) {
            loadPDF(from: pdfProvider)
            return
        }
        if let imageProvider = providers.first(where: { $0.hasItemConformingToTypeIdentifier(UTType.image.identifier) }) {
            loadImage(from: imageProvider)
            return
        }
        os_log("Kein PDF/Bild-Provider gefunden", log: Self.log, type: .error)
        finish(error: String(localized: "Nur PDF- und Bild-Dateien werden unterstuetzt."))
    }

    /// v1.0.1: 60 MB Hard-Cap — Share-Extension hat nur ~120 MB Speicher-
    /// Budget, iOS jetsam-killt sonst still ohne Fehlermeldung.
    private static let maxAttachmentBytes: Int64 = 60 * 1024 * 1024

    private func loadPDF(from provider: NSItemProvider) {
        provider.loadItem(forTypeIdentifier: UTType.pdf.identifier, options: nil) { [weak self] item, err in
            guard let self else { return }
            if let err {
                os_log("PDF-Load-Fehler: %{public}@", log: Self.log, type: .error, String(describing: err))
            }
            if let url = item as? URL {
                let secured = url.startAccessingSecurityScopedResource()
                defer { if secured { url.stopAccessingSecurityScopedResource() } }
                // Size-Guard VOR dem Load — Data(contentsOf:) mit 200MB
                // waere ein sicherer OOM-Kill.
                if let attrs = try? FileManager.default.attributesOfItem(atPath: url.path),
                   let size = attrs[.size] as? Int64, size > Self.maxAttachmentBytes {
                    self.finish(error: String(format: String(localized: "Datei zu gross (%d MB). Max: %d MB."), Int(size/1024/1024), Int(Self.maxAttachmentBytes/1024/1024)))
                    return
                }
                // .mappedIfSafe = memory-mapped I/O, kein Full-Read in RAM
                guard let data = try? Data(contentsOf: url, options: [.mappedIfSafe]) else {
                    self.finish(error: String(localized: "PDF konnte nicht gelesen werden."))
                    return
                }
                let name = url.lastPathComponent.isEmpty ? "document.pdf" : url.lastPathComponent
                os_log("PDF geladen: name=%{public}@ size=%{public}d", log: Self.log, type: .info, name, data.count)
                self.upload(data: data, filename: name)
                return
            }
            if let data = item as? Data {
                os_log("PDF als Data geladen: size=%{public}d", log: Self.log, type: .info, data.count)
                self.upload(data: data, filename: "document.pdf")
                return
            }
            self.finish(error: String(localized: "PDF-Anhang im unbekannten Format."))
        }
    }

    private func loadImage(from provider: NSItemProvider) {
        provider.loadItem(forTypeIdentifier: UTType.image.identifier, options: nil) { [weak self] item, err in
            guard let self else { return }
            if let err {
                os_log("Image-Load-Fehler: %{public}@", log: Self.log, type: .error, String(describing: err))
            }
            if let url = item as? URL {
                let secured = url.startAccessingSecurityScopedResource()
                defer { if secured { url.stopAccessingSecurityScopedResource() } }
                if let attrs = try? FileManager.default.attributesOfItem(atPath: url.path),
                   let size = attrs[.size] as? Int64, size > Self.maxAttachmentBytes {
                    self.finish(error: String(format: String(localized: "Bild zu gross (%d MB). Max: %d MB."), Int(size/1024/1024), Int(Self.maxAttachmentBytes/1024/1024)))
                    return
                }
                guard let data = try? Data(contentsOf: url, options: [.mappedIfSafe]),
                      let image = UIImage(data: data) else {
                    self.finish(error: String(localized: "Bild konnte nicht gelesen werden."))
                    return
                }
                let pdf = self.renderImageToPDF(image)
                let baseName = url.deletingPathExtension().lastPathComponent
                let filename = (baseName.isEmpty ? "image" : baseName) + ".pdf"
                os_log("Bild->PDF konvertiert: in=%{public}d out=%{public}d filename=%{public}@",
                       log: Self.log, type: .info, data.count, pdf.count, filename)
                self.upload(data: pdf, filename: filename)
                return
            }
            if let image = item as? UIImage {
                let pdf = self.renderImageToPDF(image)
                os_log("Bild (UIImage) -> PDF: size=%{public}d", log: Self.log, type: .info, pdf.count)
                self.upload(data: pdf, filename: "image.pdf")
                return
            }
            self.finish(error: String(localized: "Bild-Anhang im unbekannten Format."))
        }
    }

    private func renderImageToPDF(_ image: UIImage) -> Data {
        let pageRect = CGRect(origin: .zero, size: image.size)
        let renderer = UIGraphicsPDFRenderer(bounds: pageRect)
        return renderer.pdfData { ctx in
            ctx.beginPage()
            image.draw(in: pageRect)
        }
    }

    // MARK: - Upload

    private func upload(data: Data, filename: String) {
        let defaults = UserDefaults(suiteName: Self.appGroupID)
        let serverURL = (defaults?.string(forKey: DefaultsKey.serverURL) ?? "")
            .trimmingCharacters(in: .whitespacesAndNewlines)

        // Bearer: zuerst Keychain (aktuelle App), dann Fallback UserDefaults
        // (Legacy/Migration). Loest das v0.4.x-Migrationsproblem fuer User,
        // die die Extension vor dem ersten Haupt-App-Start aufrufen.
        let bearerKeychain = readBearerFromKeychain()
        let bearerLegacy   = (defaults?.string(forKey: DefaultsKey.bearerTokenLegacy) ?? "")
            .trimmingCharacters(in: .whitespacesAndNewlines)
        let token = !bearerKeychain.isEmpty ? bearerKeychain : bearerLegacy

        // Target: Multi-Array bevorzugt, dann Legacy-Single, dann print:self.
        let targetId = resolveTargetId(defaults: defaults)

        os_log("Upload-Vorbereitung: server=%{public}@ tokenPresent=%{public}@ target=%{public}@ size=%{public}d filename=%{public}@",
               log: Self.log,
               type: .info,
               serverURL.isEmpty ? "<leer>" : serverURL,
               token.isEmpty ? "nein" : "ja",
               targetId,
               data.count,
               filename)
        // Token nur als private logging — bewusst NICHT in den oben sichtbaren Log.
        os_log("Bearer (private): %{private}@", log: Self.log, type: .debug, token)

        guard !serverURL.isEmpty else {
            finish(error: String(localized: "App ist nicht eingerichtet — bitte zuerst MySecurePrint starten und einloggen."))
            return
        }
        guard !token.isEmpty else {
            finish(error: String(localized: "Kein Login gefunden — bitte in MySecurePrint einloggen."))
            return
        }
        guard let client = try? PrintixSendCore.ApiClient(baseUrl: serverURL, token: token) else {
            finish(error: String(format: String(localized: "Server-URL ist ungueltig: %@"), serverURL))
            return
        }

        statusLabel.text = String(format: String(localized: "Sende %@ …"), filename)

        Task.detached { [weak self] in
            guard let self else { return }
            do {
                let result = try await client.sendData(
                    data,
                    filename: filename,
                    targetId: targetId,
                    comment: nil,
                    copies: 1,
                    color: false,
                    duplex: false
                )
                os_log("Upload OK: target=%{public}@ result=%{public}@",
                       log: Self.log, type: .info, targetId, String(describing: result))
                await MainActor.run {
                    self.finish(success: true, message: String(format: String(localized: "Gesendet an %@"), targetId))
                }
            } catch {
                os_log("Upload FEHLGESCHLAGEN: target=%{public}@ err=%{public}@",
                       log: Self.log, type: .error, targetId, String(describing: error))
                await MainActor.run {
                    self.finish(error: String(format: String(localized: "Upload fehlgeschlagen: %@"), error.localizedDescription))
                }
            }
        }
    }

    private func resolveTargetId(defaults: UserDefaults?) -> String {
        if let data = defaults?.data(forKey: DefaultsKey.selectedTargetIds),
           let arr  = try? JSONDecoder().decode([String].self, from: data),
           let first = arr.first, !first.isEmpty {
            return first
        }
        let legacy = (defaults?.string(forKey: DefaultsKey.lastTargetId) ?? "")
            .trimmingCharacters(in: .whitespacesAndNewlines)
        if !legacy.isEmpty { return legacy }
        // Fallback: SecurePrint-Konto. NIEMALS crashen, wenn der User
        // die Extension vor der ersten Ziel-Auswahl benutzt.
        return Self.defaultTargetId
    }

    // MARK: - Keychain

    private func readBearerFromKeychain() -> String {
        let query: [String: Any] = [
            kSecClass as String:            kSecClassGenericPassword,
            kSecAttrService as String:      Self.keychainService,
            kSecAttrAccount as String:      Self.keychainAccount,
            kSecAttrAccessGroup as String:  Self.keychainAccessGroup,
            kSecMatchLimit as String:       kSecMatchLimitOne,
            kSecReturnData as String:       true,
        ]
        var item: CFTypeRef?
        let status = SecItemCopyMatching(query as CFDictionary, &item)
        guard status == errSecSuccess,
              let data = item as? Data,
              let str = String(data: data, encoding: .utf8) else {
            if status != errSecItemNotFound {
                os_log("Keychain-Lesefehler: status=%{public}d", log: Self.log, type: .error, Int(status))
            }
            return ""
        }
        return str
    }

    // MARK: - Finish

    private func finish(success: Bool = true, message: String) {
        statusLabel.text = message
        // Semaphore freigeben BEVOR wir completeRequest rufen — sonst
        // steht der Warteblock unnoetig weitere Sekunden.
        markUploadFinished()
        // kurz stehen lassen, damit der User die Bestaetigung sieht
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.6) { [weak self] in
            self?.extensionContext?.completeRequest(returningItems: nil)
        }
    }

    private func finish(error: String) {
        os_log("Finish-mit-Fehler: %{public}@", log: Self.log, type: .error, error)
        // Fehler in App-Group ablegen, damit die Haupt-App ihn anzeigen kann.
        if let defaults = UserDefaults(suiteName: Self.appGroupID) {
            let payload: [String: Any] = [
                "ts": Date().timeIntervalSince1970,
                "message": error,
            ]
            defaults.set(payload, forKey: DefaultsKey.lastShareError)
        }
        statusLabel.text = error
        statusLabel.textColor = .systemRed
        markUploadFinished()
        DispatchQueue.main.asyncAfter(deadline: .now() + 1.6) { [weak self] in
            self?.extensionContext?.completeRequest(returningItems: nil)
        }
    }
}
