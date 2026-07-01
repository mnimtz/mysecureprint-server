import AppKit
import SwiftUI
import PrintixSendCore

// Kleines, eigenständiges Login-Fenster. Hält sich selbst am Leben
// (statische Referenz), damit der SwiftUI-Lifecycle es nicht
// mitnimmt, sobald AppDelegate die .show()-Methode verlässt.

@MainActor
final class LoginWindowController: NSObject, NSWindowDelegate {
    static let shared = LoginWindowController()

    private var window: NSWindow?

    func show() {
        if let w = window {
            w.makeKeyAndOrderFront(nil)
            NSApp.activate(ignoringOtherApps: true)
            return
        }
        let view = LoginView(onClose: { [weak self] in self?.close() })
        let host = NSHostingController(rootView: view)
        let w = NSWindow(contentViewController: host)
        w.title = "Printix Send — Anmelden"
        w.styleMask = [.titled, .closable, .miniaturizable]
        w.setContentSize(NSSize(width: 420, height: 340))
        w.center()
        w.isReleasedWhenClosed = false
        w.delegate = self
        window = w
        w.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
    }

    func close() {
        window?.close()
        window = nil
    }

    func windowWillClose(_ notification: Notification) {
        window = nil
    }
}

// ─────────────────────────────────────────────────────────────────

@MainActor
private struct LoginView: View {
    let onClose: () -> Void

    @State private var username:  String = ""
    @State private var password:  String = ""
    @State private var serverUrl: String = ""
    @State private var busy = false
    @State private var error: String?
    @State private var showEntra = false

    private func loadDefaults() {
        if username.isEmpty  { username  = AppState.shared.config.lastUsername ?? "" }
        if serverUrl.isEmpty { serverUrl = AppState.shared.config.serverUrl }
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("Bei Printix Send anmelden")
                .font(.title2).bold()

            if serverUrl.isEmpty {
                Text("Bitte zunächst die Server-URL in der Konfiguration hinterlegen.")
                    .foregroundColor(.secondary)
                Button("Konfiguration öffnen…") {
                    NSApp.sendAction(Selector(("showSettingsWindow:")), to: nil, from: nil)
                    NSApp.activate(ignoringOtherApps: true)
                }
            } else {
                Text("Server: \(serverUrl)")
                    .font(.footnote)
                    .foregroundColor(.secondary)
                TextField("Benutzername oder E-Mail", text: $username)
                    .textFieldStyle(.roundedBorder)
                SecureField("Passwort", text: $password)
                    .textFieldStyle(.roundedBorder)

                if let error {
                    Text(error)
                        .font(.footnote)
                        .foregroundColor(.red)
                        .fixedSize(horizontal: false, vertical: true)
                }

                HStack {
                    Button("Mit Microsoft anmelden (Entra)") {
                        showEntra = true
                    }
                    Spacer()
                    Button("Abbrechen") { onClose() }
                    Button("Anmelden") { Task { await doLogin() } }
                        .buttonStyle(.borderedProminent)
                        .disabled(username.isEmpty || password.isEmpty || busy)
                }
            }
            if busy { ProgressView().controlSize(.small) }
        }
        .padding(20)
        .frame(width: 420)
        .onAppear { loadDefaults() }
        .sheet(isPresented: $showEntra) {
            EntraDeviceView(onClose: { showEntra = false; onClose() })
        }
    }

    private func doLogin() async {
        busy = true
        error = nil
        defer { busy = false }
        do {
            try await AppState.shared.loginPassword(username: username, password: password)
            onClose()
        } catch {
            self.error = error.localizedDescription
        }
    }
}
