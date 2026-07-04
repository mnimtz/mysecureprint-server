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
        // Druckeinstellungen (werden in der Haupt-App konfiguriert).
        static let printBW            = "printBW"
        static let printImageSize     = "printImageSize"
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

    private var uploadFinished = false

    override func viewDidAppear(_ animated: Bool) {
        super.viewDidAppear(animated)
        handleSharedItem()
    }

    private func markUploadFinished() {
        uploadFinished = true
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
                // Size-Guard VOR dem Load — Data(contentsOf:) mit 200MB
                // waere ein sicherer OOM-Kill.
                if let attrs = try? FileManager.default.attributesOfItem(atPath: url.path),
                   let size = attrs[.size] as? Int64, size > Self.maxAttachmentBytes {
                    if secured { url.stopAccessingSecurityScopedResource() }
                    let msg = String(format: String(localized: "Datei zu gross (%d MB). Max: %d MB."),
                                     Int(size/1024/1024), Int(Self.maxAttachmentBytes/1024/1024))
                    DispatchQueue.main.async { [weak self] in self?.finish(error: msg) }
                    return
                }
                // .mappedIfSafe = memory-mapped I/O, kein Full-Read in RAM
                guard let data = try? Data(contentsOf: url, options: [.mappedIfSafe]) else {
                    if secured { url.stopAccessingSecurityScopedResource() }
                    DispatchQueue.main.async { [weak self] in
                        self?.finish(error: String(localized: "PDF konnte nicht gelesen werden."))
                    }
                    return
                }
                let name = url.lastPathComponent.isEmpty ? "document.pdf" : url.lastPathComponent
                if secured { url.stopAccessingSecurityScopedResource() }
                os_log("PDF geladen: name=%{public}@ size=%{public}d", log: Self.log, type: .info, name, data.count)
                DispatchQueue.main.async { [weak self] in self?.upload(data: data, filename: name) }
                return
            }
            if let data = item as? Data {
                os_log("PDF als Data geladen: size=%{public}d", log: Self.log, type: .info, data.count)
                DispatchQueue.main.async { [weak self] in self?.upload(data: data, filename: "document.pdf") }
                return
            }
            DispatchQueue.main.async { [weak self] in
                self?.finish(error: String(localized: "PDF-Anhang im unbekannten Format."))
            }
        }
    }

    private func loadImage(from provider: NSItemProvider) {
        // JPEG bevorzugen — Photos transkodiert HEIC automatisch zu JPEG
        // wenn wir explizit UTType.jpeg anfordern.
        let typeId = provider.hasItemConformingToTypeIdentifier(UTType.jpeg.identifier)
            ? UTType.jpeg.identifier : UTType.image.identifier

        provider.loadItem(forTypeIdentifier: typeId, options: nil) { [weak self] item, err in
            guard let self else { return }
            if let err {
                os_log("Image-Load-Fehler: %{public}@", log: Self.log, type: .error, String(describing: err))
            }
            if let url = item as? URL {
                let secured = url.startAccessingSecurityScopedResource()
                if let attrs = try? FileManager.default.attributesOfItem(atPath: url.path),
                   let size = attrs[.size] as? Int64, size > Self.maxAttachmentBytes {
                    if secured { url.stopAccessingSecurityScopedResource() }
                    let msg = String(format: String(localized: "Bild zu gross (%d MB). Max: %d MB."),
                                     Int(size/1024/1024), Int(Self.maxAttachmentBytes/1024/1024))
                    DispatchQueue.main.async { [weak self] in self?.finish(error: msg) }
                    return
                }
                // Komprimierte Bytes lesen ohne Bilddekodierung — kein OOM-Risiko.
                // Der Server konvertiert das Bild serverseitig via Pillow zu PDF.
                guard let data = try? Data(contentsOf: url, options: [.mappedIfSafe]) else {
                    if secured { url.stopAccessingSecurityScopedResource() }
                    DispatchQueue.main.async { [weak self] in
                        self?.finish(error: String(localized: "Bild konnte nicht geladen werden."))
                    }
                    return
                }
                let ext = url.pathExtension.lowercased()
                let baseName = url.deletingPathExtension().lastPathComponent
                let filename = (baseName.isEmpty ? "image" : baseName) + (ext.isEmpty ? ".jpg" : "." + ext)
                if secured { url.stopAccessingSecurityScopedResource() }
                os_log("Bild geladen: %{public}d Bytes filename=%{public}@",
                       log: Self.log, type: .info, data.count, filename)
                DispatchQueue.main.async { [weak self] in self?.upload(data: data, filename: filename) }
                return
            }
            if let image = item as? UIImage {
                // UIImage-Fallback: in JPEG konvertieren, Server konvertiert zu PDF
                guard let jpegData = image.jpegData(compressionQuality: 0.85) else {
                    DispatchQueue.main.async { [weak self] in
                        self?.finish(error: String(localized: "Bild konnte nicht konvertiert werden."))
                    }
                    return
                }
                os_log("Bild (UIImage)->JPEG: %{public}d Bytes", log: Self.log, type: .info, jpegData.count)
                DispatchQueue.main.async { [weak self] in self?.upload(data: jpegData, filename: "image.jpg") }
                return
            }
            DispatchQueue.main.async { [weak self] in
                self?.finish(error: String(localized: "Bild-Anhang im unbekannten Format."))
            }
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

        // Druckeinstellungen aus App-Group lesen (von Haupt-App konfiguriert).
        let printBW        = defaults?.bool(forKey: DefaultsKey.printBW) ?? false
        let printImageSize = defaults?.string(forKey: DefaultsKey.printImageSize) ?? "full"

        Task.detached { [weak self] in
            guard let self else { return }
            do {
                let result = try await client.sendData(
                    data,
                    filename: filename,
                    targetId: targetId,
                    comment: nil,
                    copies: 1,
                    color: !printBW,
                    duplex: false,
                    printImageSize: printImageSize
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
        DispatchQueue.main.asyncAfter(deadline: .now() + 2.0) { [weak self] in
            self?.extensionContext?.completeRequest(returningItems: nil)
        }
    }
}
