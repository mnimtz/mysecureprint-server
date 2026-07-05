import UIKit
import UniformTypeIdentifiers
import Security
import os.log

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

    /// 50 MB Hard-Cap — identisch mit dem Server-Limit in /desktop/send (MAX=50MB).
    /// Share-Extension hat ~120 MB Speicher-Budget; iOS jetsam-killt bei Überschreitung.
    private static let maxAttachmentBytes: Int64 = 50 * 1024 * 1024

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

    // MARK: - Upload (Background URLSession)

    // Extension und Haupt-App teilen dieselbe Session-ID — iOS verknüpft
    // beide Prozesse und gibt Events an die Haupt-App weiter.
    private static let bgSessionID = "de.nimtz.mysecureprint.bgupload"

    private func upload(data: Data, filename: String) {
        let defaults = UserDefaults(suiteName: Self.appGroupID)
        let serverURL = (defaults?.string(forKey: DefaultsKey.serverURL) ?? "")
            .trimmingCharacters(in: .whitespacesAndNewlines)
        let bearerKeychain = readBearerFromKeychain()
        let bearerLegacy   = (defaults?.string(forKey: DefaultsKey.bearerTokenLegacy) ?? "")
            .trimmingCharacters(in: .whitespacesAndNewlines)
        let token = !bearerKeychain.isEmpty ? bearerKeychain : bearerLegacy
        let targetId = resolveTargetId(defaults: defaults)

        os_log("BG-Upload: server=%{public}@ token=%{public}@ target=%{public}@ file=%{public}@",
               log: Self.log, type: .info,
               serverURL.isEmpty ? "<leer>" : serverURL, token.isEmpty ? "nein" : "ja",
               targetId, filename)

        guard !serverURL.isEmpty else {
            finish(error: String(localized: "App ist nicht eingerichtet — bitte zuerst MySecurePrint starten und einloggen."))
            return
        }
        guard !token.isEmpty else {
            finish(error: String(localized: "Kein Login gefunden — bitte in MySecurePrint einloggen."))
            return
        }
        guard let base = URL(string: serverURL.trimmingCharacters(in: .init(charactersIn: "/"))) else {
            finish(error: String(format: String(localized: "Server-URL ist ungueltig: %@"), serverURL))
            return
        }

        let printBW        = defaults?.bool(forKey: DefaultsKey.printBW) ?? false
        let printImageSize = defaults?.string(forKey: DefaultsKey.printImageSize) ?? "full"

        // Multipart-Body als Datei in App-Group schreiben
        let boundary = "Boundary-\(UUID().uuidString)"
        let bodyData = buildShareMultipart(
            boundary: boundary, fileData: data, filename: filename,
            targetId: targetId, color: !printBW, printImageSize: printImageSize
        )
        guard let container = FileManager.default
            .containerURL(forSecurityApplicationGroupIdentifier: Self.appGroupID) else {
            finish(error: String(localized: "App-Group nicht verfügbar."))
            return
        }
        let dir = container.appendingPathComponent("uploads", isDirectory: true)
        try? FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        let tempFile = dir.appendingPathComponent("\(UUID().uuidString).mpfd")
        guard (try? bodyData.write(to: tempFile)) != nil else {
            finish(error: String(localized: "Temporäre Datei konnte nicht geschrieben werden."))
            return
        }

        // Background URLSession mit derselben ID wie die Haupt-App
        let cfg = URLSessionConfiguration.background(withIdentifier: Self.bgSessionID)
        cfg.isDiscretionary = false
        cfg.sessionSendsLaunchEvents = true
        let bgSession = URLSession(configuration: cfg)

        var req = URLRequest(url: base.appendingPathComponent("desktop/send"))
        req.httpMethod = "POST"
        req.setValue("multipart/form-data; boundary=\(boundary)", forHTTPHeaderField: "Content-Type")
        req.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        req.timeoutInterval = 180

        bgSession.uploadTask(with: req, fromFile: tempFile).resume()

        os_log("BG-Upload-Task gestartet: file=%{public}@", log: Self.log, type: .info, filename)

        // Extension sofort beenden — iOS übernimmt den Upload,
        // die Haupt-App wird bei Abschluss aufgeweckt.
        statusLabel.text = String(localized: "Wird im Hintergrund gesendet…")
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.8) { [weak self] in
            self?.finish(success: true, message: String(localized: "Wird gesendet…"))
        }
    }

    private func buildShareMultipart(boundary: String, fileData: Data, filename: String,
                                      targetId: String, color: Bool,
                                      printImageSize: String) -> Data {
        var body = Data()
        func field(_ name: String, _ value: String) {
            let part = "--\(boundary)\r\nContent-Disposition: form-data; name=\"\(name)\"\r\n\r\n\(value)\r\n"
            body.append(Data(part.utf8))
        }
        field("target_id",        targetId)
        field("copies",           "1")
        field("color",            color ? "1" : "")
        field("duplex",           "")
        field("print_image_size", printImageSize)
        let ext  = (filename as NSString).pathExtension
        let mime = guessMime(ext)
        let hdr = "--\(boundary)\r\nContent-Disposition: form-data; name=\"file\"; filename=\"\(filename)\"\r\nContent-Type: \(mime)\r\n\r\n"
        body.append(Data(hdr.utf8))
        body.append(fileData)
        body.append(Data("\r\n--\(boundary)--\r\n".utf8))
        return body
    }

    private func guessMime(_ ext: String) -> String {
        switch ext.lowercased() {
        case "pdf":  return "application/pdf"
        case "png":  return "image/png"
        case "jpg", "jpeg": return "image/jpeg"
        case "heic", "heif": return "image/heic"
        default:     return "application/octet-stream"
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
