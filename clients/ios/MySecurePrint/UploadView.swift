import SwiftUI
import Combine
import UniformTypeIdentifiers
import PhotosUI
import PrintixSendCore

/// Haupt-Upload-Screen: Datei aus dem Files-Picker wählen, optional
/// Copies/Color/Duplex setzen, an gewähltes Target senden.
struct UploadView: View {

    @EnvironmentObject private var settings: SettingsStore

    @State private var pickedURL: URL?
    @State private var copies: Int = 1
    @State private var color: Bool = false
    @State private var duplex: Bool = false
    @State private var comment: String = ""

    @State private var isSending: Bool = false
    /// H-5: Letzter Fehler aus der Share-Extension. Wir lesen ihn beim
    /// Erscheinen aus der App-Group, zeigen ihn als Alert und loeschen
    /// den Key — sonst koennte derselbe Fehler bei jedem App-Wechsel
    /// erneut hochpoppen.
    @State private var shareErrorAlert: String?
    /// Ein Eintrag pro Ziel — strukturiert statt String-Soup, damit
    /// wir pro Zeile Icon/Titel/Subzeile sauber rendern koennen.
    @State private var sendResults: [SendOutcome] = []
    @State private var errorText: String = ""
    @State private var showImporter: Bool = false

    struct SendOutcome: Identifiable {
        let id = UUID()
        let targetDisplay: String
        let ok: Bool
        let detail: String   // Status, Job-Id oder Fehlermeldung
    }
    // Foto-Picker nutzt PhotosUI, nicht fileImporter — die Fotos-
    // Mediathek kriegt man ueber den Files-Picker nicht erreicht.
    @State private var photoItem: PhotosPickerItem?

    // Alle unterstützten Upload-Typen — der Server nimmt mehr als nur
    // PDF entgegen (LibreOffice-Konvertierung im MCP). PDF/Bilder sind
    // aber unsere Hauptszenarien aus dem iOS-Share-Flow.
    private var allowedTypes: [UTType] {
        var t: [UTType] = [.pdf, .image, .plainText]
        if let docx = UTType(filenameExtension: "docx") { t.append(docx) }
        if let xlsx = UTType(filenameExtension: "xlsx") { t.append(xlsx) }
        return t
    }

    var body: some View {
        NavigationStack {
            Form {
                Section(String(localized: "Datei")) {
                    // Datei aus der Files-App (PDF, Office, Textdateien etc.)
                    Button {
                        showImporter = true
                    } label: {
                        HStack {
                            Image(systemName: "folder.fill")
                            Text(String(localized: "Aus Dateien wählen"))
                            Spacer()
                        }
                    }
                    // Foto aus der Fotos-Mediathek — nutzt PhotosPicker,
                    // damit der User wirklich an seine Fotos/Screenshots
                    // rankommt ohne Umweg ueber "In Dateien speichern".
                    PhotosPicker(selection: $photoItem,
                                 matching: .any(of: [.images, .screenshots]),
                                 photoLibrary: .shared()) {
                        HStack {
                            Image(systemName: "photo.fill")
                            Text(String(localized: "Aus Fotos wählen"))
                            Spacer()
                        }
                    }
                    // Anzeige der aktuell gewaehlten Quelle
                    HStack {
                        Image(systemName: "doc.text")
                            .foregroundColor(.secondary)
                        if let name = pickedURL?.lastPathComponent {
                            Text(name)
                                .foregroundColor(.secondary)
                                .lineLimit(1)
                        } else {
                            Text(String(localized: "Noch nichts ausgewählt"))
                                .foregroundColor(.secondary)
                                .lineLimit(1)
                        }
                    }
                }

                Section(String(localized: "Optionen")) {
                    Stepper(String(format: String(localized: "Kopien: %d"), copies), value: $copies, in: 1...50)
                    Toggle(String(localized: "Farbe"), isOn: $color)
                    Toggle(String(localized: "Duplex"), isOn: $duplex)
                    TextField(String(localized: "Kommentar (optional)"), text: $comment)
                        .autocorrectionDisabled()
                }

                // Senden-Button zuerst — kuerzerer Weg vom Datei-
                // Picker zum Absenden. Der User will in der Regel nur
                // drucken; die Ziel-Liste steht informativ drunter.
                Section {
                    Button {
                        Task { await sendNow() }
                    } label: {
                        HStack {
                            Spacer()
                            if isSending { ProgressView() }
                            else {
                                Image(systemName: "paperplane.fill")
                                Text(String(localized: "An Printix senden")).fontWeight(.semibold)
                            }
                            Spacer()
                        }
                    }
                    .disabled(isSending || pickedURL == nil || settings.selectedTargetIds.isEmpty)
                }

                Section(String(localized: "Ziele")) {
                    if settings.selectedTargetIds.isEmpty {
                        Text(String(localized: "Kein Ziel gewählt — unter „Ziele“ auswählen."))
                            .foregroundColor(.secondary)
                    } else {
                        Text(String(format: String(localized: "%d Ziel(e) ausgewählt"), settings.selectedTargetIds.count))
                            .font(.footnote)
                            .foregroundColor(.secondary)
                        ForEach(settings.selectedTargetIds, id: \.self) { id in
                            HStack {
                                Image(systemName: "printer.fill")
                                    .foregroundColor(.secondary)
                                Text(settings.targetLabels[id] ?? id)
                                    .lineLimit(1)
                            }
                        }
                        // Countdown-Banner: laeuft live mit (TimelineView
                        // tickt sekuendlich) und ruft den Reset-Check auf,
                        // damit bei Ablauf auf SecurePrint zurueckgeschaltet
                        // wird — ohne dass der User aktiv was tun muss.
                        if settings.selectionExpiresAt != nil {
                            TimelineView(.periodic(from: .now, by: 1)) { ctx in
                                autoResetBanner(now: ctx.date)
                            }
                        }
                    }
                }

                if !errorText.isEmpty {
                    Section(String(localized: "Fehler")) {
                        Text(errorText).foregroundColor(.red).textSelection(.enabled)
                    }
                }

                if !sendResults.isEmpty {
                    Section(String(localized: "Ergebnis")) {
                        ForEach(sendResults) { r in
                            HStack(alignment: .top, spacing: 10) {
                                Image(systemName: r.ok
                                      ? "checkmark.circle.fill"
                                      : "exclamationmark.triangle.fill")
                                    .foregroundColor(r.ok ? .green : .orange)
                                    .font(.title3)
                                VStack(alignment: .leading, spacing: 2) {
                                    Text(r.targetDisplay)
                                        .fontWeight(.medium)
                                    Text(r.detail)
                                        .font(.caption)
                                        .foregroundColor(.secondary)
                                }
                            }
                            .padding(.vertical, 2)
                        }
                    }
                }
            }
            .brandNavStyle(title: "Upload")
            .fileImporter(isPresented: $showImporter,
                          allowedContentTypes: allowedTypes,
                          allowsMultipleSelection: false) { result in
                switch result {
                case .success(let urls): pickedURL = urls.first
                case .failure(let err):  errorText = err.localizedDescription
                }
            }
            .onChange(of: photoItem) { _, newItem in
                guard let newItem else { return }
                Task { await importPhoto(newItem) }
            }
            .onAppear {
                settings.resetToDefaultIfExpired()
                readShareExtensionError()
            }
            .alert(String(localized: "Share-Fehler"),
                   isPresented: Binding(
                    get: { shareErrorAlert != nil },
                    set: { if !$0 { shareErrorAlert = nil } })) {
                Button("OK", role: .cancel) { shareErrorAlert = nil }
            } message: {
                Text(shareErrorAlert ?? "")
            }
            .onReceive(resetTick) { _ in
                settings.resetToDefaultIfExpired()
            }
        }
    }

    /// Reines Anzeige-Helper — Countdown, keine Mutation.
    /// Der tatsaechliche Reset passiert im onReceive am Form unten,
    /// damit wir nicht waehrend eines View-Builds State mutieren.
    @ViewBuilder
    private func autoResetBanner(now: Date) -> some View {
        if let expiry = settings.selectionExpiresAt {
            let remaining = max(0, Int(expiry.timeIntervalSince(now)))
            let mm = remaining / 60
            let ss = remaining % 60
            HStack {
                Image(systemName: "clock.fill")
                    .foregroundColor(.orange)
                Text(String(format: String(localized: "Zurück zu SecurePrint in %d:%02d"), mm, ss))
                    .font(.footnote)
                    .foregroundColor(.secondary)
            }
        }
    }

    /// 1-Sekunden-Timer fuer den Reset-Check. Vorher als `let` auf
    /// dem struct deklariert — was bei jeder View-Rebuild ein
    /// frisches `Timer.publish(...).autoconnect()` ausgeloest hat,
    /// ohne den alten Publisher zu cancelen (I-3, CPU-/Akku-Drain).
    /// Jetzt in eine `ResetClock` ObservableObject gekapselt und
    /// via `@StateObject` an den View-Lifecycle gebunden — SwiftUI
    /// instanziiert das Objekt genau einmal.
    @StateObject private var clock = ResetClock()
    private var resetTick: AnyPublisher<Date, Never> {
        clock.publisher
    }

    /// PhotosPickerItem → temporaere Datei im Tmp-Verzeichnis.
    /// Der Rest von sendNow() kann dann ueber pickedURL wie gewohnt
    /// arbeiten (security-scoped entfaellt, weil /tmp uns gehoert).
    @MainActor
    private func importPhoto(_ item: PhotosPickerItem) async {
        errorText = ""
        do {
            guard let data = try await item.loadTransferable(type: Data.self) else {
                errorText = String(localized: "Konnte das Foto nicht laden.")
                return
            }
            // Dateiendung bestmoeglich raten — bei HEIC/JPEG steht das
            // im ersten Content-Type-Eintrag, sonst Fallback auf .jpg.
            let ext = fileExtension(for: item.supportedContentTypes.first) ?? "jpg"
            let name = "photo-\(Int(Date().timeIntervalSince1970)).\(ext)"
            let url  = FileManager.default.temporaryDirectory.appendingPathComponent(name)
            try data.write(to: url, options: .atomic)
            pickedURL = url
        } catch {
            errorText = String(localized: "Foto-Import: \(error.localizedDescription)")
        }
    }

    private func fileExtension(for type: UTType?) -> String? {
        guard let type else { return nil }
        if type.conforms(to: .png)  { return "png" }
        if type.conforms(to: .jpeg) { return "jpg" }
        if type.conforms(to: .heic) { return "heic" }
        if type.conforms(to: .gif)  { return "gif" }
        return type.preferredFilenameExtension
    }

    @MainActor
    private func sendNow() async {
        errorText = ""
        sendResults = []
        guard let fileURL = pickedURL else {
            errorText = String(localized: "Bitte zuerst eine Datei auswählen.")
            return
        }
        guard let client = ApiClientFactory.make(baseURL: settings.serverURL,
                                                 token: settings.bearerToken) else {
            errorText = String(localized: "Keine gültige Server-Konfiguration.")
            return
        }

        // Security-scoped Zugriff: zwingend für Files-Picker-URLs.
        let secured = fileURL.startAccessingSecurityScopedResource()
        defer { if secured { fileURL.stopAccessingSecurityScopedResource() } }

        isSending = true
        defer { isSending = false }

        do {
            // v1.0.1: Data(contentsOf:) auf MainActor blockte bei grossen
            // PDFs die UI und riskierte 0x8badf00d Watchdog-Kills. Auf
            // Background-Thread lesen; die security-scope bleibt aktiv
            // solange sendNow() nicht returnt (defer above).
            let localURL = fileURL
            let data = try await Task.detached(priority: .userInitiated) {
                return try Data(contentsOf: localURL, options: [.mappedIfSafe])
            }.value
            let filename = fileURL.lastPathComponent
            // Multi-Target: pro ausgewaehltem Ziel einmal senden.
            // Wir sammeln Erfolge/Fehler pro Ziel und zeigen die Liste
            // anschliessend an — ein Fehler bei einem Ziel bricht den
            // Rest nicht ab (z.B. wenn ein Drucker offline ist, soll
            // der andere trotzdem losgehen).
            var outcomes: [SendOutcome] = []
            for targetId in settings.selectedTargetIds {
                let display = settings.targetLabels[targetId] ?? targetId
                do {
                    let result = try await client.sendData(data,
                                                           filename: filename,
                                                           targetId: targetId,
                                                           comment: comment.isEmpty ? nil : comment,
                                                           copies: copies,
                                                           color: color,
                                                           duplex: duplex)
                    if result.ok == true || result.status?.lowercased() == "queued" {
                        outcomes.append(SendOutcome(targetDisplay: display,
                                                    ok: true,
                                                    detail: successDetail(result)))
                    } else {
                        outcomes.append(SendOutcome(targetDisplay: display,
                                                    ok: false,
                                                    detail: result.error ?? result.message ?? String(localized: "Unbekannter Fehler")))
                    }
                } catch {
                    outcomes.append(SendOutcome(targetDisplay: display,
                                                ok: false,
                                                detail: error.localizedDescription))
                }
            }
            sendResults = outcomes
        } catch {
            errorText = error.localizedDescription
        }
    }

    /// H-5: Liest den von der Share-Extension hinterlegten letzten
    /// Fehler aus der App-Group, triggert den Alert und loescht den
    /// Key. Schweigt wenn kein Fehler vorliegt — sonst nervt jeder
    /// App-Wechsel mit leeren Banner.
    private func readShareExtensionError() {
        guard let defaults = UserDefaults(suiteName: SettingsStore.appGroupID) else {
            return
        }
        let key = "lastShareError"
        guard let payload = defaults.dictionary(forKey: key),
              let msg = payload["message"] as? String,
              !msg.isEmpty else {
            return
        }
        shareErrorAlert = msg
        defaults.removeObject(forKey: key)
    }

    /// Detailzeile fuer einen erfolgreichen Send — lesbar statt
    /// "queued job=abc". Wir zeigen den Status kapitalisiert und
    /// haengen die Job-Id nur an wenn vorhanden.
    private func successDetail(_ r: SendResult) -> String {
        let status = (r.status ?? String(localized: "gesendet")).capitalized
        if let job = r.jobId, !job.isEmpty {
            return "\(status) · Job \(job)"
        }
        return status
    }

}

/// I-3: Haelt genau EINEN autoconnected 1s-Timer ueber den
/// gesamten View-Lifecycle. SwiftUI instanziiert StateObjects einmal,
/// sodass nicht bei jedem `body`-Rebuild ein neuer Publisher entsteht.
@MainActor
final class ResetClock: ObservableObject {
    let publisher: AnyPublisher<Date, Never>
    private let cancellable: AnyCancellable

    init() {
        let p = Timer.publish(every: 1, on: .main, in: .common).autoconnect()
        self.publisher = p.eraseToAnyPublisher()
        // Cancellable-Halter, damit der autoconnect aktiv bleibt; wird
        // beim Deinit der View automatisch geloest -> Timer endet.
        self.cancellable = p.sink { _ in }
    }
}
