import SwiftUI
import AppKit
import PrintixSendCore

// Entra-Device-Code-Flow als Sheet. Der Server liefert einen
// User-Code + verification_uri; wir öffnen die URL im Default-Browser
// und pollen parallel /desktop/auth/entra/poll bis "success" oder
// "error". Parallel zur Windows-EntraDeviceWindow.xaml.

@MainActor
struct EntraDeviceView: View {
    let onClose: () -> Void

    @State private var userCode: String = ""
    @State private var verifyUri: String = ""
    @State private var status: String = "Initialisiere…"
    @State private var busy = true
    @State private var pollTask: Task<Void, Never>?

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("Mit Microsoft Entra anmelden")
                .font(.title3).bold()

            if !userCode.isEmpty {
                HStack(spacing: 8) {
                    Text("Code:")
                    Text(userCode)
                        .font(.system(.title2, design: .monospaced)).bold()
                        .textSelection(.enabled)
                    Button("Kopieren") {
                        NSPasteboard.general.clearContents()
                        NSPasteboard.general.setString(userCode, forType: .string)
                    }
                }
                if let url = URL(string: verifyUri) {
                    Link("Bei Microsoft öffnen: \(verifyUri)", destination: url)
                        .font(.footnote)
                }
            }

            Text(status).foregroundColor(.secondary).font(.footnote)
            if busy { ProgressView().controlSize(.small) }

            HStack {
                Spacer()
                Button("Abbrechen") {
                    pollTask?.cancel()
                    onClose()
                }
            }
        }
        .padding(20)
        .frame(width: 460)
        .task { await start() }
        .onDisappear { pollTask?.cancel() }
    }

    private func start() async {
        let cfg = AppState.shared.config
        guard !cfg.serverUrl.isEmpty else {
            status = "Server-URL fehlt — bitte in Konfiguration hinterlegen."
            busy = false
            return
        }
        do {
            let api = try ApiClient(baseUrl: cfg.serverUrl)
            let start = try await api.entraStart(deviceName: cfg.deviceName)
            userCode  = start.userCode ?? ""
            verifyUri = start.verificationUri ?? ""
            status    = start.message ?? "Browser öffnen und Code eingeben."
            if let uri = URL(string: verifyUri) {
                NSWorkspace.shared.open(uri)
            }
            guard let sessionId = start.sessionId else {
                status = "Server lieferte keine Session-ID."
                busy = false
                return
            }
            pollTask = Task { await poll(api: api, sessionId: sessionId,
                                         interval: start.interval ?? 5) }
        } catch {
            status = "Fehler: \(error.localizedDescription)"
            busy = false
        }
    }

    private func poll(api: ApiClient, sessionId: String, interval: Int) async {
        let step = UInt64(max(3, interval)) * NSEC_PER_SEC
        while !Task.isCancelled {
            try? await Task.sleep(nanoseconds: step)
            if Task.isCancelled { return }
            do {
                let r = try await api.entraPoll(sessionId: sessionId)
                switch r.status {
                case "success":
                    if let token = r.token, !token.isEmpty {
                        try KeychainStore().set(token)
                    }
                    await MainActor.run {
                        status = "Erfolgreich angemeldet."
                        busy = false
                    }
                    await AppState.shared.refreshMe()
                    await AppState.shared.refreshTargetsAndSync()
                    await MainActor.run { onClose() }
                    return
                case "pending":
                    await MainActor.run { status = "Warte auf Bestätigung…" }
                case "error":
                    await MainActor.run {
                        status = "Fehler: \(r.error ?? r.message ?? "unbekannt")"
                        busy = false
                    }
                    return
                default:
                    break
                }
            } catch {
                await MainActor.run { status = "Polling-Fehler: \(error.localizedDescription)" }
            }
        }
    }
}
